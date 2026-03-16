"""
Display Manager — manages multiple Xvfb + x11vnc + websockify stacks
for concurrent browser sessions with isolated VNC displays.

Each display session gets:
  - Xvfb on display :N
  - x11vnc on port 5900+N
  - websockify on port 6080+(N-BASE)

The default display (:99) is managed by systemd and registered but not spawned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DISPLAY = int(os.environ.get("DISPLAY_NUM", "99"))
MAX_SESSIONS = int(os.environ.get("MAX_DISPLAY_SESSIONS", "8"))


def _vnc_port(display_num: int) -> int:
    return 5900 + display_num


def _ws_port(display_num: int) -> int:
    return 6080 + (display_num - BASE_DISPLAY)


@dataclass
class DisplaySession:
    display_num: int
    display: str
    vnc_port: int
    ws_port: int
    xvfb_proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    x11vnc_proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    websockify_proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    task_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "starting"
    is_default: bool = False

    def to_dict(self) -> dict:
        return {
            "display_num": self.display_num,
            "display": self.display,
            "vnc_port": self.vnc_port,
            "ws_port": self.ws_port,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "status": self.status,
            "is_default": self.is_default,
        }


class DisplayManager:
    """Manages the lifecycle of Xvfb + x11vnc + websockify display stacks."""

    def __init__(
        self,
        base_display: int = BASE_DISPLAY,
        max_sessions: int = MAX_SESSIONS,
    ):
        self._base_display = base_display
        self._max_sessions = max_sessions
        self._sessions: dict[int, DisplaySession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_default(self) -> DisplaySession:
        """Register the default systemd-managed display (no process spawning)."""
        num = self._base_display
        session = DisplaySession(
            display_num=num,
            display=f":{num}",
            vnc_port=_vnc_port(num),
            ws_port=_ws_port(num),
            status="ready",
            is_default=True,
        )
        self._sessions[num] = session
        logger.info("Registered default display :%d (ws port %d)", num, session.ws_port)
        return session

    async def allocate(self, task_id: str | None = None) -> DisplaySession:
        """Allocate a new display session with its own Xvfb/x11vnc/websockify stack."""
        async with self._lock:
            # Find next available display number
            display_num = None
            for candidate in range(self._base_display + 1, self._base_display + self._max_sessions):
                if candidate not in self._sessions and not self._display_socket_exists(candidate):
                    display_num = candidate
                    break

            if display_num is None:
                raise RuntimeError(
                    f"Maximum display sessions ({self._max_sessions - 1}) reached. "
                    "Deallocate an existing session first."
                )

            session = DisplaySession(
                display_num=display_num,
                display=f":{display_num}",
                vnc_port=_vnc_port(display_num),
                ws_port=_ws_port(display_num),
                task_id=task_id,
                status="starting",
            )
            self._sessions[display_num] = session

        # Start processes outside the lock (they take time)
        try:
            await self._start_stack(session)
            session.status = "ready"
            logger.info(
                "Allocated display :%d (vnc %d, ws %d) for task %s",
                display_num, session.vnc_port, session.ws_port, task_id,
            )
            return session
        except Exception:
            logger.exception("Failed to start display stack for :%d", display_num)
            await self._stop_stack(session)
            async with self._lock:
                self._sessions.pop(display_num, None)
            raise

    async def deallocate(self, display_num: int) -> None:
        """Tear down a display session and free its resources."""
        async with self._lock:
            session = self._sessions.pop(display_num, None)
        if session is None:
            return
        if session.is_default:
            # Re-add default — it can't be deallocated
            async with self._lock:
                self._sessions[display_num] = session
            raise RuntimeError("Cannot deallocate the default display")

        session.status = "stopping"
        await self._stop_stack(session)
        session.status = "stopped"
        logger.info("Deallocated display :%d", display_num)

    def get_session(self, display_num: int) -> DisplaySession | None:
        return self._sessions.get(display_num)

    def get_default(self) -> DisplaySession | None:
        return self._sessions.get(self._base_display)

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in sorted(self._sessions.values(), key=lambda s: s.display_num)]

    async def cleanup_dead(self) -> None:
        """Check for dead processes and tear down stale sessions."""
        dead = []
        for num, session in list(self._sessions.items()):
            if session.is_default:
                continue
            if session.status != "ready":
                continue
            # Check if key processes died
            if session.xvfb_proc and session.xvfb_proc.returncode is not None:
                logger.warning("Xvfb died for display :%d (rc=%s)", num, session.xvfb_proc.returncode)
                dead.append(num)
            elif session.x11vnc_proc and session.x11vnc_proc.returncode is not None:
                # Try restarting x11vnc — Xvfb may still be fine
                logger.warning("x11vnc died for display :%d, restarting", num)
                try:
                    session.x11vnc_proc = await self._start_x11vnc(num, session.vnc_port)
                    await self._wait_for_port(session.vnc_port, timeout=5.0)
                except Exception:
                    logger.exception("Failed to restart x11vnc for :%d", num)
                    dead.append(num)
            elif session.websockify_proc and session.websockify_proc.returncode is not None:
                # Try restarting websockify
                logger.warning("websockify died for display :%d, restarting", num)
                try:
                    session.websockify_proc = await self._start_websockify(session.ws_port, session.vnc_port)
                except Exception:
                    logger.exception("Failed to restart websockify for :%d", num)
                    dead.append(num)

        for num in dead:
            try:
                await self.deallocate(num)
            except Exception:
                logger.exception("Error cleaning up dead display :%d", num)

    async def periodic_cleanup(self) -> None:
        """Run cleanup_dead() every 30 seconds."""
        while True:
            await asyncio.sleep(30)
            try:
                await self.cleanup_dead()
            except Exception:
                logger.exception("Error in periodic display cleanup")

    def start_cleanup_task(self) -> None:
        """Start the background periodic cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self.periodic_cleanup())

    async def shutdown_all(self) -> None:
        """Deallocate all non-default sessions. Called on app shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        to_remove = [num for num, s in self._sessions.items() if not s.is_default]
        for num in to_remove:
            try:
                await self.deallocate(num)
            except Exception:
                logger.exception("Error shutting down display :%d", num)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_stack(self, session: DisplaySession) -> None:
        """Start the full Xvfb → x11vnc → websockify stack for a session."""
        session.xvfb_proc = await self._start_xvfb(session.display_num)
        await self._wait_for_display_socket(session.display_num, timeout=5.0)

        session.x11vnc_proc = await self._start_x11vnc(session.display_num, session.vnc_port)
        await self._wait_for_port(session.vnc_port, timeout=5.0)

        session.websockify_proc = await self._start_websockify(session.ws_port, session.vnc_port)
        # websockify is ready almost immediately after binding

    async def _stop_stack(self, session: DisplaySession) -> None:
        """Stop all processes in reverse order."""
        for proc_name in ("websockify_proc", "x11vnc_proc", "xvfb_proc"):
            proc = getattr(session, proc_name, None)
            if proc is None:
                continue
            try:
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
            except ProcessLookupError:
                pass
            except Exception:
                logger.exception("Error stopping %s for display :%d", proc_name, session.display_num)

    async def _start_xvfb(self, display_num: int) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "/usr/bin/Xvfb", f":{display_num}",
            "-screen", "0", "1920x1080x24",
            "-ac",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _start_x11vnc(self, display_num: int, vnc_port: int) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "/usr/bin/x11vnc",
            "-display", f":{display_num}",
            "-rfbport", str(vnc_port),
            "-nopw", "-forever", "-shared", "-xkb",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _start_websockify(self, ws_port: int, vnc_port: int) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "/usr/bin/websockify",
            "--web", "/usr/share/novnc",
            str(ws_port), f"localhost:{vnc_port}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    @staticmethod
    def _display_socket_exists(display_num: int) -> bool:
        """Check if an X11 display socket already exists."""
        return Path(f"/tmp/.X11-unix/X{display_num}").exists()

    @staticmethod
    async def _wait_for_display_socket(display_num: int, timeout: float = 5.0) -> None:
        """Poll until the Xvfb socket file appears."""
        socket_path = Path(f"/tmp/.X11-unix/X{display_num}")
        elapsed = 0.0
        while elapsed < timeout:
            if socket_path.exists():
                return
            await asyncio.sleep(0.1)
            elapsed += 0.1
        raise TimeoutError(f"Xvfb socket for :{display_num} did not appear within {timeout}s")

    @staticmethod
    async def _wait_for_port(port: int, timeout: float = 5.0) -> None:
        """Poll until a TCP port is accepting connections."""
        elapsed = 0.0
        while elapsed < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect(("127.0.0.1", port))
                sock.close()
                return
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.2)
                elapsed += 0.2
        raise TimeoutError(f"Port {port} did not become available within {timeout}s")
