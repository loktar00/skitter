"""
Crawler API Server

FastAPI server that accepts task prompts via HTTP POST, executes an AI agent CLI,
and returns results. Designed so other LLMs, n8n, or any HTTP client can
submit automation tasks.

Usage:
    python -m uvicorn api_server:app --host 0.0.0.0 --port 8080
    # or via systemd: systemctl start crawler-api
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mimetypes

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# --- API Key Auth ---

API_KEY = os.environ.get("CRAWLER_API_KEY", "")

app = FastAPI(
    title="Crawler API",
    description="HTTP API for sending tasks to an AI agent",
    version="1.0.0",
)

# Paths that don't require an API key (dashboard, static, health)
_PUBLIC_PREFIXES = ("/dashboard", "/health")


@app.middleware("http")
async def api_key_middleware(request, call_next):
    """Require X-API-Key header on API routes when CRAWLER_API_KEY is set."""
    if not API_KEY:
        return await call_next(request)

    path = request.url.path
    # Allow dashboard, static files, and health without auth
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES) or path == "/":
        return await call_next(request)

    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)

# --- Configuration ---

AGENT_BIN = os.environ.get("AGENT_BIN", "claude")
WORKING_DIR = os.environ.get("CRAWLER_WORKING_DIR", "/opt/crawler")
DATA_DIR = os.environ.get("CRAWLER_DATA_DIR", "/opt/crawler/output")
DEFAULT_ALLOWED_TOOLS = os.environ.get(
    "AGENT_DEFAULT_TOOLS",
    "Bash,Read,Write,Edit,Glob,Grep,mcp__playwright__browser_navigate,"
    "mcp__playwright__browser_screenshot,mcp__playwright__browser_click,"
    "mcp__playwright__browser_type,mcp__playwright__browser_snapshot",
)
MAX_TIMEOUT = 600  # 10 minutes
RECIPES_DIR = Path(os.environ.get("CRAWLER_RECIPES_DIR", os.path.join(WORKING_DIR, "recipes")))
VENV_PYTHON = os.environ.get("CRAWLER_VENV_PYTHON", sys.executable)
XDISPLAY = os.environ.get("DISPLAY", ":99")


def _agent_available() -> bool:
    """Check if the agent CLI binary is installed and reachable."""
    import shutil
    return shutil.which(AGENT_BIN) is not None


def _require_agent():
    """Raise 503 if agent CLI is not installed."""
    if not _agent_available():
        raise HTTPException(
            status_code=503,
            detail=f"Agent CLI '{AGENT_BIN}' is not installed on this server. "
                   f"Install it or set AGENT_BIN to your agent binary. "
                   f"Crawl, login, recipe, and workflow replay endpoints work without it.",
        )


# --- In-memory storage ---

sessions: dict[str, dict] = {}
crawl_tasks: dict[str, dict] = {}


# --- Request/Response models ---


class TaskRequest(BaseModel):
    prompt: str
    allowed_tools: Optional[list[str]] = None
    timeout: int = Field(default=120, le=MAX_TIMEOUT)
    system_prompt: Optional[str] = None


class TaskResponse(BaseModel):
    status: str
    result: str
    session_id: str
    duration_seconds: float


class ContinueRequest(BaseModel):
    session_id: str
    prompt: str
    timeout: int = Field(default=120, le=MAX_TIMEOUT)


class AuthPrepareRequest(BaseModel):
    url: str
    message: str = "Please log in via VNC"


class AuthPrepareResponse(BaseModel):
    status: str
    vnc_display: str
    message: str
    pid: Optional[int] = None


class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    last_prompt: str
    turns: int


class RecipeCreate(BaseModel):
    name: str
    start_urls: list[str]
    list_scope_css: str
    item_link_css: str = "a[href]"
    pagination_type: Optional[str] = None  # next, all_links, url_template
    next_css: Optional[str] = None
    pagination_scope_css: Optional[str] = None
    page_param: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    max_list_pages: Optional[int] = None
    max_items: Optional[int] = None
    items_jsonl: Optional[str] = None
    pages_jsonl: Optional[str] = None


class CrawlRequest(BaseModel):
    recipe_path: str
    headless: bool = True


class FullCrawlRequest(BaseModel):
    urls: list[str]
    max_depth: int = Field(default=2, ge=0, le=10)
    allowed_domains: list[str] = Field(default_factory=list)
    headless: bool = True


# --- Helpers ---


async def run_agent(
    prompt: str,
    timeout: int = 120,
    allowed_tools: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    resume: bool = False,
    system_prompt: Optional[str] = None,
) -> tuple[str, str]:
    """
    Run agent CLI and return (output_text, session_id).
    """
    tools = allowed_tools or DEFAULT_ALLOWED_TOOLS.split(",")

    cmd = [AGENT_BIN, "-p", prompt, "--output-format", "json"]

    for tool in tools:
        cmd.extend(["--allowedTools", tool.strip()])

    if resume and session_id:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--continue", session_id])

    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise HTTPException(status_code=504, detail="Agent task timed out")

    output = stdout.decode("utf-8", errors="replace").strip()

    # Parse JSON output to extract result and session_id
    try:
        data = json.loads(output)
        result_text = data.get("result", output)
        new_session_id = data.get("session_id", session_id or str(uuid.uuid4()))
    except (json.JSONDecodeError, TypeError):
        result_text = output
        new_session_id = session_id or str(uuid.uuid4())

    if proc.returncode != 0 and not result_text:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status_code=500,
            detail=f"Agent exited with code {proc.returncode}: {err}",
        )

    return result_text, new_session_id


# --- Endpoints ---


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_available": _agent_available(),
        "agent_bin": AGENT_BIN,
    }


@app.post("/task", response_model=TaskResponse)
async def run_task(req: TaskRequest):
    """Send a task prompt to the agent and get the response."""
    _require_agent()
    start = asyncio.get_event_loop().time()
    result, session_id = await run_agent(
        prompt=req.prompt,
        timeout=req.timeout,
        allowed_tools=req.allowed_tools,
        system_prompt=req.system_prompt,
    )
    duration = asyncio.get_event_loop().time() - start

    sessions[session_id] = {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_prompt": req.prompt,
        "turns": 1,
    }

    return TaskResponse(
        status="ok",
        result=result,
        session_id=session_id,
        duration_seconds=round(duration, 2),
    )


MCP_PROFILE_DIR = Path(DATA_DIR) / "browser_session" / "mcp_profile"


@app.post("/task/stream")
async def run_task_stream(req: TaskRequest):
    """Send a task prompt to the agent and stream the response as SSE."""
    _require_agent()

    async def event_generator():
        tools = req.allowed_tools or DEFAULT_ALLOWED_TOOLS.split(",")
        cmd = [AGENT_BIN, "-p", req.prompt, "--output-format", "stream-json", "--verbose"]
        for tool in tools:
            cmd.extend(["--allowedTools", tool.strip()])
        if req.system_prompt:
            cmd.extend(["--append-system-prompt", req.system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKING_DIR,
            env={**os.environ, "DISPLAY": XDISPLAY},
            limit=10 * 1024 * 1024,  # 10MB line buffer for large payloads (screenshots)
        )

        try:
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    yield f"data: {text}\n\n"
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            stderr_bytes = await proc.stderr.read()
            await proc.wait()
            if proc.returncode != 0 and stderr_bytes:
                err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
                import json as _json
                yield f"data: {_json.dumps({'type': 'error', 'error': err_msg})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/task/continue", response_model=TaskResponse)
async def continue_task(req: ContinueRequest):
    """Continue a previous conversation by session_id."""
    _require_agent()
    if req.session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    start = asyncio.get_event_loop().time()
    result, session_id = await run_agent(
        prompt=req.prompt,
        timeout=req.timeout,
        session_id=req.session_id,
    )
    duration = asyncio.get_event_loop().time() - start

    sessions[session_id]["last_prompt"] = req.prompt
    sessions[session_id]["turns"] += 1

    return TaskResponse(
        status="ok",
        result=result,
        session_id=session_id,
        duration_seconds=round(duration, 2),
    )


@app.get("/sessions")
async def list_sessions() -> list[SessionInfo]:
    """List recent sessions."""
    return [SessionInfo(**s) for s in sessions.values()]


@app.post("/auth-prepare", response_model=AuthPrepareResponse)
async def auth_prepare(req: AuthPrepareRequest):
    """
    Open a URL in a visible browser on the Xvfb display for VNC-based login.
    The browser stays open until the user finishes authenticating.
    """
    script = f"""
import sys
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(
        viewport={{"width": 1280, "height": 900}},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});")
    page.goto("{req.url}", wait_until="domcontentloaded", timeout=60000)
    print("AUTH_READY", flush=True)
    # Keep browser open - user interacts via VNC
    # Process will be killed when auth is done
    import time
    while True:
        time.sleep(60)
"""

    proc = await asyncio.create_subprocess_exec(
        VENV_PYTHON, "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    # Wait for the page to load (up to 30s)
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            if b"AUTH_READY" in line:
                break
    except asyncio.TimeoutError:
        proc.kill()
        stderr = await proc.stderr.read()
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        detail = "Browser failed to open page"
        if err_msg:
            detail += f": {err_msg}"
        raise HTTPException(status_code=500, detail=detail)

    return AuthPrepareResponse(
        status="ready",
        vnc_display=":99",
        message=f"Page opened at {req.url}. Connect via VNC to authenticate.",
        pid=proc.pid,
    )


# --- Login Session Management ---

COOKIES_FILE = Path(DATA_DIR) / "browser_session" / "cookies.json"

# Track active login browser processes
login_sessions: dict[str, dict] = {}


class LoginOpenRequest(BaseModel):
    url: str
    label: str = ""  # optional friendly name like "eBay", "Facebook"


@app.post("/api/login/open")
async def login_open(req: LoginOpenRequest):
    """Open a non-headless browser for manual login. View via Browser tab."""
    # Kill any existing login session
    for sid, sess in list(login_sessions.items()):
        proc = sess.get("proc")
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        login_sessions.pop(sid, None)

    session_id = str(uuid.uuid4())[:8]

    # Script that opens browser using MCP's persistent Chrome profile,
    # loads existing cookies, navigates to URL,
    # waits for SAVE signal on stdin, then saves cookies and exits.
    script = f'''
import json, sys, signal, time, random
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIES_FILE = Path("{COOKIES_FILE}")
MCP_PROFILE = Path("{MCP_PROFILE_DIR}")
MCP_PROFILE.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    vw = 1280 + random.randint(-50, 50)
    vh = 900 + random.randint(-50, 50)

    # Use persistent context with same Chrome profile as the MCP browser
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(MCP_PROFILE),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
        viewport={{"width": vw, "height": vh}},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="America/New_York",
    )

    # Also load cookies from cookies.json for sites logged in before this change
    if COOKIES_FILE.exists():
        try:
            cookies = json.loads(COOKIES_FILE.read_text())
            ctx.add_cookies(cookies)
        except Exception:
            pass

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, \\'webdriver\\', {{get: () => undefined}});")
    page.goto("{req.url}", wait_until="domcontentloaded", timeout=60000)
    print("AUTH_READY", flush=True)

    # Wait for SAVE command on stdin, or SIGTERM
    running = True
    def handle_sig(s, f):
        global running
        running = False
    signal.signal(signal.SIGTERM, handle_sig)

    while running:
        try:
            line = sys.stdin.readline().strip()
            if line == "SAVE":
                # Save cookies to cookies.json (for headless crawler)
                COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
                cookies = ctx.cookies()
                COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
                count = len(cookies)
                # Profile cookies are saved automatically by Chrome on close
                print(f"SAVED {{count}}", flush=True)
                break
            elif line == "QUIT":
                break
        except Exception:
            time.sleep(1)

    ctx.close()
    print("CLOSED", flush=True)
'''

    proc = await asyncio.create_subprocess_exec(
        VENV_PYTHON, "-c", script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    # Wait for the page to load
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            if b"AUTH_READY" in line:
                break
    except asyncio.TimeoutError:
        proc.kill()
        stderr = await proc.stderr.read()
        await proc.wait()
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        detail = "Browser failed to open"
        if err_msg:
            detail += f": {err_msg}"
        raise HTTPException(status_code=500, detail=detail)

    login_sessions[session_id] = {
        "session_id": session_id,
        "url": req.url,
        "label": req.label or req.url,
        "proc": proc,
        "status": "open",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "session_id": session_id,
        "status": "open",
        "message": f"Browser opened at {req.url}. Log in via the Browser View tab, then click Save Session.",
    }


@app.post("/api/login/save")
async def login_save():
    """Save cookies from the active login browser and close it."""
    active = [s for s in login_sessions.values() if s["status"] == "open"]
    if not active:
        raise HTTPException(status_code=404, detail="No active login session")

    sess = active[0]
    proc = sess["proc"]

    if proc.returncode is not None:
        sess["status"] = "closed"
        raise HTTPException(status_code=400, detail="Browser already closed")

    # Send SAVE command
    try:
        proc.stdin.write(b"SAVE\n")
        await proc.stdin.drain()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send save signal: {e}")

    # Wait for confirmation
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded.startswith("SAVED"):
                count = decoded.split()[-1] if " " in decoded else "?"
                sess["status"] = "saved"
                await proc.wait()
                return {
                    "status": "saved",
                    "cookies_saved": count,
                    "cookies_file": str(COOKIES_FILE),
                    "message": f"Session saved ({count} cookies). Future headless crawls will use these cookies.",
                }
            if decoded == "CLOSED":
                break
    except asyncio.TimeoutError:
        pass

    proc.kill()
    await proc.wait()
    sess["status"] = "saved"
    return {"status": "saved", "cookies_file": str(COOKIES_FILE)}


@app.post("/api/login/cancel")
async def login_cancel():
    """Close the login browser without saving."""
    active = [s for s in login_sessions.values() if s["status"] == "open"]
    if not active:
        raise HTTPException(status_code=404, detail="No active login session")

    sess = active[0]
    proc = sess["proc"]

    if proc.returncode is None:
        try:
            proc.stdin.write(b"QUIT\n")
            await proc.stdin.drain()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            proc.kill()
            await proc.wait()

    sess["status"] = "cancelled"
    return {"status": "cancelled"}


@app.get("/api/login/status")
async def login_status():
    """Get current login session status."""
    active = [s for s in login_sessions.values() if s["status"] == "open"]
    if not active:
        return {"active": False}
    sess = active[0]
    # Check if process is still alive
    if sess["proc"].returncode is not None:
        sess["status"] = "closed"
        return {"active": False}
    return {
        "active": True,
        "session_id": sess["session_id"],
        "url": sess["url"],
        "label": sess["label"],
        "opened_at": sess["opened_at"],
    }


@app.get("/api/login/sessions")
async def list_saved_sessions():
    """List domains with saved cookies."""
    if not COOKIES_FILE.exists():
        return {"domains": [], "cookie_count": 0}
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
        domains = sorted(set(c.get("domain", "").lstrip(".") for c in cookies if c.get("domain")))
        return {"domains": domains, "cookie_count": len(cookies)}
    except Exception:
        return {"domains": [], "cookie_count": 0}


# --- Persistent Browser Session ---
# Keeps a browser open across requests so an external agent can drive it
# interactively: navigate, click, type, take snapshots, etc.

browser_session: dict = {"proc": None, "status": "closed"}


def _browser_script() -> str:
    """Python script that runs in a subprocess, keeps a browser alive,
    and accepts JSON commands on stdin, returning JSON results on stdout."""
    return f'''
import json, sys, signal, time, random, base64, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIES_FILE = Path("{COOKIES_FILE}")
MCP_PROFILE = Path("{MCP_PROFILE_DIR}")
MCP_PROFILE.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    vw = 1920 + random.randint(-50, 50)
    vh = 1080 + random.randint(-50, 50)

    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(MCP_PROFILE),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
        viewport={{"width": vw, "height": vh}},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="America/New_York",
    )

    if COOKIES_FILE.exists():
        try:
            cookies = json.loads(COOKIES_FILE.read_text())
            ctx.add_cookies(cookies)
        except Exception:
            pass

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, \\'webdriver\\', {{get: () => undefined}});")

    print(json.dumps({{"ready": True}}), flush=True)

    def save_cookies():
        try:
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            c = ctx.cookies()
            COOKIES_FILE.write_text(json.dumps(c, indent=2))
        except Exception:
            pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            action = cmd.get("action", "")
            result = {{}}

            if action == "navigate":
                page.goto(cmd["url"], wait_until="domcontentloaded", timeout=60000)
                time.sleep(random.uniform(1.0, 2.5))
                result = {{"url": page.url, "title": page.title()}}

            elif action == "click":
                sel = cmd.get("selector", "")
                text = cmd.get("text", "")
                if sel:
                    page.locator(sel).first.click(timeout=10000)
                elif text:
                    page.get_by_text(text, exact=False).first.click(timeout=10000)
                time.sleep(random.uniform(0.5, 1.5))
                result = {{"url": page.url, "title": page.title()}}

            elif action == "type":
                sel = cmd.get("selector", "")
                text = cmd.get("text", "")
                if sel:
                    page.locator(sel).first.fill(text, timeout=10000)
                result = {{"typed": text}}

            elif action == "press_key":
                page.keyboard.press(cmd.get("key", "Enter"))
                result = {{"key": cmd.get("key", "Enter")}}

            elif action == "snapshot":
                # Get accessible page content as text
                title = page.title()
                url = page.url
                text = page.evaluate("document.body.innerText")
                # Truncate to avoid huge responses
                if len(text) > 20000:
                    text = text[:20000] + "\\n... (truncated)"
                result = {{"url": url, "title": title, "text": text}}

            elif action == "screenshot":
                raw = page.screenshot(full_page=cmd.get("full_page", False))
                b64 = base64.b64encode(raw).decode("ascii")
                result = {{"url": page.url, "screenshot_base64": b64, "size": len(raw)}}

            elif action == "get_links":
                links = page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]')).map(a => ({{
                        text: a.innerText.trim().substring(0, 200),
                        href: a.href
                    }}))
                """)
                result = {{"url": page.url, "links": links}}

            elif action == "evaluate":
                val = page.evaluate(cmd.get("expression", "document.title"))
                result = {{"value": val}}

            elif action == "wait":
                secs = cmd.get("seconds", 2)
                time.sleep(secs)
                result = {{"waited": secs}}

            elif action == "scroll":
                direction = cmd.get("direction", "down")
                amount = cmd.get("amount", 500)
                if direction == "down":
                    page.evaluate(f"window.scrollBy(0, {{amount}})")
                elif direction == "up":
                    page.evaluate(f"window.scrollBy(0, -{{amount}})")
                time.sleep(0.5)
                result = {{"scrolled": direction, "amount": amount}}

            elif action == "save_cookies":
                save_cookies()
                result = {{"saved": True}}

            elif action == "close":
                save_cookies()
                ctx.close()
                print(json.dumps({{"closed": True}}), flush=True)
                sys.exit(0)

            else:
                result = {{"error": f"Unknown action: {{action}}"}}

            print(json.dumps(result), flush=True)

        except Exception as e:
            print(json.dumps({{"error": str(e), "traceback": traceback.format_exc()}}), flush=True)
'''


@app.post("/api/browser/open")
async def browser_open():
    """Open a persistent browser session. Loads saved cookies."""
    proc = browser_session.get("proc")
    if proc and proc.returncode is None:
        return {"status": "already_open", "message": "Browser session already active."}

    script = _browser_script()
    proc = await asyncio.create_subprocess_exec(
        VENV_PYTHON, "-c", script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        data = json.loads(line.decode("utf-8", errors="replace").strip())
        if not data.get("ready"):
            raise Exception("Browser did not report ready")
    except asyncio.TimeoutError:
        proc.kill()
        stderr = await proc.stderr.read()
        await proc.wait()
        err = stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(status_code=500, detail=f"Browser failed to start: {err}")

    browser_session["proc"] = proc
    browser_session["status"] = "open"
    return {"status": "open", "message": "Browser session started with saved cookies."}


async def _browser_cmd(cmd: dict) -> dict:
    """Send a command to the persistent browser and return the result."""
    proc = browser_session.get("proc")
    if not proc or proc.returncode is not None:
        raise HTTPException(status_code=400, detail="No active browser session. Call /api/browser/open first.")

    try:
        proc.stdin.write((json.dumps(cmd) + "\n").encode())
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
        return json.loads(line.decode("utf-8", errors="replace").strip())
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Browser command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Browser command failed: {e}")


@app.post("/api/browser/navigate")
async def browser_navigate(req: dict):
    """Navigate to a URL."""
    return await _browser_cmd({"action": "navigate", "url": req["url"]})


@app.post("/api/browser/click")
async def browser_click(req: dict):
    """Click an element by CSS selector or visible text."""
    cmd = {"action": "click"}
    if req.get("selector"):
        cmd["selector"] = req["selector"]
    elif req.get("text"):
        cmd["text"] = req["text"]
    else:
        raise HTTPException(status_code=400, detail="Provide 'selector' or 'text'")
    return await _browser_cmd(cmd)


@app.post("/api/browser/type")
async def browser_type(req: dict):
    """Type text into an element by CSS selector."""
    return await _browser_cmd({"action": "type", "selector": req["selector"], "text": req["text"]})


@app.post("/api/browser/press_key")
async def browser_press_key(req: dict):
    """Press a keyboard key (e.g. Enter, Tab, Escape)."""
    return await _browser_cmd({"action": "press_key", "key": req.get("key", "Enter")})


@app.post("/api/browser/snapshot")
async def browser_snapshot():
    """Get the current page text content, URL, and title."""
    return await _browser_cmd({"action": "snapshot"})


@app.post("/api/browser/screenshot")
async def browser_screenshot(req: dict = None):
    """Take a screenshot. Returns base64-encoded PNG."""
    req = req or {}
    return await _browser_cmd({"action": "screenshot", "full_page": req.get("full_page", False)})


@app.post("/api/browser/get_links")
async def browser_get_links():
    """Get all links on the current page."""
    return await _browser_cmd({"action": "get_links"})


@app.post("/api/browser/scroll")
async def browser_scroll(req: dict):
    """Scroll the page. Direction: 'up' or 'down'. Amount in pixels."""
    return await _browser_cmd({
        "action": "scroll",
        "direction": req.get("direction", "down"),
        "amount": req.get("amount", 500),
    })


@app.post("/api/browser/evaluate")
async def browser_evaluate(req: dict):
    """Run JavaScript in the browser and return the result."""
    return await _browser_cmd({"action": "evaluate", "expression": req["expression"]})


@app.post("/api/browser/close")
async def browser_close():
    """Close the persistent browser session and save cookies."""
    proc = browser_session.get("proc")
    if not proc or proc.returncode is not None:
        browser_session["status"] = "closed"
        return {"status": "already_closed"}

    try:
        result = await _browser_cmd({"action": "close"})
    except Exception:
        proc.kill()
        await proc.wait()
    browser_session["status"] = "closed"
    browser_session["proc"] = None
    return {"status": "closed"}


@app.get("/api/browser/status")
async def browser_status():
    """Check if a browser session is active."""
    proc = browser_session.get("proc")
    alive = proc is not None and proc.returncode is None
    if not alive:
        browser_session["status"] = "closed"
    return {"active": alive, "status": browser_session["status"]}


# --- Recipe CRUD ---


@app.get("/api/recipes")
async def list_recipes():
    """List all recipes."""
    recipes = []
    for path in sorted(RECIPES_DIR.rglob("*.yaml")):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
            rel = str(path.relative_to(RECIPES_DIR))
            recipes.append({
                "name": path.stem,
                "path": rel,
                "start_urls": data.get("start_urls", []),
                "pagination_type": (data.get("pagination") or {}).get("type"),
            })
        except Exception:
            continue
    return recipes


@app.get("/api/recipes/{path:path}")
async def get_recipe(path: str):
    """Get full recipe content."""
    file_path = RECIPES_DIR / path
    if not file_path.resolve().is_relative_to(RECIPES_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)
    return {"path": path, "content": data}


@app.post("/api/recipes")
async def create_recipe(req: RecipeCreate):
    """Create a new recipe from form fields."""
    # Build nested YAML dict from flat fields
    data: dict = {
        "start_urls": req.start_urls,
        "list_scope_css": req.list_scope_css,
        "item_link_css": req.item_link_css,
    }

    if req.pagination_type:
        pag: dict = {"type": req.pagination_type}
        if req.pagination_type == "next" and req.next_css:
            pag["next_css"] = req.next_css
        elif req.pagination_type == "all_links" and req.pagination_scope_css:
            pag["pagination_scope_css"] = req.pagination_scope_css
        elif req.pagination_type == "url_template":
            if req.page_param:
                pag["page_param"] = req.page_param
            if req.page_start is not None:
                pag["page_start"] = req.page_start
            if req.page_end is not None:
                pag["page_end"] = req.page_end
        data["pagination"] = pag

    if req.max_list_pages or req.max_items:
        limits: dict = {}
        if req.max_list_pages:
            limits["max_list_pages"] = req.max_list_pages
        if req.max_items:
            limits["max_items"] = req.max_items
        data["limits"] = limits

    items_default = f"output/{req.name}_items.jsonl"
    pages_default = f"output/{req.name}_pages.jsonl"
    data["output"] = {
        "items_jsonl": req.items_jsonl or items_default,
        "pages_jsonl": req.pages_jsonl or pages_default,
    }

    # Validate using recipe_loader
    from recipe_loader import Recipe
    try:
        Recipe.from_dict(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Save file
    safe_name = req.name.replace("/", "_").replace("..", "_")
    file_path = RECIPES_DIR / f"{safe_name}.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return {"status": "created", "path": str(file_path.relative_to(RECIPES_DIR))}


@app.delete("/api/recipes/{path:path}")
async def delete_recipe(path: str):
    """Delete a recipe."""
    file_path = RECIPES_DIR / path
    if not file_path.resolve().is_relative_to(RECIPES_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    file_path.unlink()
    return {"status": "deleted", "path": path}


# --- Crawl Task Management ---


async def _run_crawl(task_id: str, recipe_path: str, headless: bool):
    """Background coroutine: runs crawler.py and captures output."""
    cmd = [
        VENV_PYTHON, "crawler.py",
        "--mode", "list",
        "--recipe", recipe_path,
    ]
    if headless:
        cmd.append("--headless")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    crawl_tasks[task_id]["pid"] = proc.pid
    lines = crawl_tasks[task_id]["log_lines"]

    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").rstrip()
        lines.append(text)

    await proc.wait()
    crawl_tasks[task_id]["returncode"] = proc.returncode
    crawl_tasks[task_id]["status"] = "completed" if proc.returncode == 0 else "failed"
    crawl_tasks[task_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


async def _run_full_crawl(
    task_id: str,
    urls: list[str],
    max_depth: int,
    allowed_domains: list[str],
    headless: bool,
):
    """Background coroutine: runs crawler.py --mode crawl."""
    cmd = [
        VENV_PYTHON, "crawler.py",
        "--mode", "crawl",
        "--urls", *urls,
        "--max-depth", str(max_depth),
    ]
    if allowed_domains:
        cmd.extend(["--domains", *allowed_domains])
    if headless:
        cmd.append("--headless")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=WORKING_DIR,
        env={**os.environ, "DISPLAY": XDISPLAY},
    )

    crawl_tasks[task_id]["pid"] = proc.pid
    lines = crawl_tasks[task_id]["log_lines"]

    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").rstrip()
        lines.append(text)

    await proc.wait()
    crawl_tasks[task_id]["returncode"] = proc.returncode
    crawl_tasks[task_id]["status"] = "completed" if proc.returncode == 0 else "failed"
    crawl_tasks[task_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/api/crawl/full")
async def start_full_crawl(req: FullCrawlRequest):
    """Start a full HTML crawl as a background task."""
    if not req.urls:
        raise HTTPException(status_code=400, detail="At least one URL required")

    task_id = str(uuid.uuid4())[:8]
    crawl_tasks[task_id] = {
        "task_id": task_id,
        "mode": "full",
        "urls": req.urls,
        "max_depth": req.max_depth,
        "headless": req.headless,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "pid": None,
        "returncode": None,
        "log_lines": [],
    }

    asyncio.create_task(
        _run_full_crawl(task_id, req.urls, req.max_depth, req.allowed_domains, req.headless)
    )
    return {"task_id": task_id, "status": "running"}


@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    """Start a crawl as a background task."""
    recipe_file = RECIPES_DIR / req.recipe_path
    if not recipe_file.resolve().is_relative_to(RECIPES_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not recipe_file.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")

    task_id = str(uuid.uuid4())[:8]
    crawl_tasks[task_id] = {
        "task_id": task_id,
        "mode": "list",
        "recipe_path": req.recipe_path,
        "headless": req.headless,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "pid": None,
        "returncode": None,
        "log_lines": [],
    }

    asyncio.create_task(_run_crawl(task_id, str(recipe_file), req.headless))
    return {"task_id": task_id, "status": "running"}


@app.get("/api/crawl")
async def list_crawl_tasks():
    """List all crawl tasks."""
    return [
        {k: v for k, v in t.items() if k != "log_lines"}
        | {"log_count": len(t["log_lines"])}
        for t in crawl_tasks.values()
    ]


@app.get("/api/crawl/{task_id}")
async def get_crawl_task(task_id: str, tail: int = 50):
    """Get crawl task status and log tail."""
    if task_id not in crawl_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    t = crawl_tasks[task_id]
    return {
        "task_id": t["task_id"],
        "mode": t.get("mode", "list"),
        "recipe_path": t.get("recipe_path"),
        "urls": t.get("urls"),
        "status": t["status"],
        "started_at": t["started_at"],
        "finished_at": t["finished_at"],
        "returncode": t["returncode"],
        "log_count": len(t["log_lines"]),
        "log_tail": t["log_lines"][-tail:],
    }


# --- Workflow Management ---

WORKFLOWS_DIR = Path(os.path.join(WORKING_DIR, "workflows"))
WORKFLOWS_DIR.mkdir(exist_ok=True)

workflow_runs: dict[str, dict] = {}


class WorkflowSaveRequest(BaseModel):
    name: str
    description: str = ""
    recorded_from: str = ""
    input_schema: dict = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    inputs: dict[str, str] = Field(default_factory=dict)
    headless: bool = True


class WorkflowRecordRequest(BaseModel):
    prompt: str
    name: str = ""
    description: str = ""
    timeout: int = Field(default=300, le=600)


@app.get("/api/workflows")
async def list_workflows():
    """List all saved workflows."""
    workflows = []
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            workflows.append({
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "step_count": len(data.get("steps", [])),
                "tags": data.get("tags", []),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            continue
    return workflows


@app.get("/api/workflows/runs")
async def list_workflow_runs():
    """List all workflow runs."""
    return [
        {k: v for k, v in r.items() if k != "log_lines"}
        | {"log_count": len(r.get("log_lines", []))}
        for r in workflow_runs.values()
    ]


@app.get("/api/workflows/runs/{run_id}")
async def get_workflow_run(run_id: str, tail: int = 50):
    """Get workflow run status and log tail."""
    if run_id not in workflow_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    r = workflow_runs[run_id]
    return {
        "run_id": r["run_id"],
        "workflow_name": r["workflow_name"],
        "status": r["status"],
        "started_at": r["started_at"],
        "finished_at": r.get("finished_at"),
        "log_count": len(r.get("log_lines", [])),
        "log_tail": r.get("log_lines", [])[-tail:],
    }


@app.get("/api/workflows/{name}")
async def get_workflow(name: str):
    """Get full workflow JSON."""
    safe = name.replace("/", "_").replace("..", "_")
    path = WORKFLOWS_DIR / f"{safe}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")
    return json.loads(path.read_text())


@app.post("/api/workflows")
async def save_workflow(req: WorkflowSaveRequest):
    """Save a workflow."""
    safe = req.name.replace("/", "_").replace("..", "_")
    if not safe:
        raise HTTPException(status_code=400, detail="Name is required")
    path = WORKFLOWS_DIR / f"{safe}.json"
    data = {
        "name": req.name,
        "description": req.description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recorded_from": req.recorded_from,
        "input_schema": req.input_schema,
        "steps": req.steps,
        "tags": req.tags,
    }
    path.write_text(json.dumps(data, indent=2))
    return {"status": "saved", "name": req.name}


RECORDING_SYSTEM_PROMPT = """You are a web automation recorder. Perform the user's task using ONLY Playwright MCP tools.

Rules:
- Take a browser_snapshot before each interaction to see the current page state
- Use CSS selectors, data-testid, or aria-label for element identification when possible
- Be methodical: navigate, observe, act, verify
- After completing the task, take a final snapshot to confirm success
- Do NOT explain what you're doing — just perform the actions
"""

RECORDING_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_evaluate",
    "mcp__playwright__browser_file_upload",
    "mcp__playwright__browser_handle_dialog",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_tabs",
    "mcp__playwright__browser_drag",
    "mcp__playwright__browser_resize",
    "mcp__playwright__browser_console_messages",
    "mcp__playwright__browser_network_requests",
]


@app.post("/api/workflows/record")
async def record_workflow(req: WorkflowRecordRequest):
    """
    Start an AI-driven recording session. The agent performs the task using
    Playwright MCP tools. Returns SSE stream with progress, then final
    workflow steps.
    """
    _require_agent()
    from workflow_recorder import stream_to_steps

    async def event_generator():
        cmd = [
            AGENT_BIN, "-p", req.prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--append-system-prompt", RECORDING_SYSTEM_PROMPT,
        ]
        for tool in RECORDING_TOOLS:
            cmd.extend(["--allowedTools", tool])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKING_DIR,
            env={**os.environ, "DISPLAY": XDISPLAY},
        )

        raw_lines: list[str] = []

        try:
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    raw_lines.append(text)
                    # Forward to client for live viewing
                    yield f"data: {text}\n\n"
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

        # Parse raw stream into workflow steps
        steps = stream_to_steps(raw_lines)
        steps_json = [s.model_dump() for s in steps]

        result = {
            "type": "workflow_result",
            "name": req.name or "",
            "description": req.description or "",
            "recorded_from": req.prompt,
            "steps": steps_json,
        }
        yield f"data: {json.dumps(result)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/workflows/{name}")
async def delete_workflow(name: str):
    """Delete a workflow."""
    safe = name.replace("/", "_").replace("..", "_")
    path = WORKFLOWS_DIR / f"{safe}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")
    path.unlink()
    return {"status": "deleted", "name": name}


@app.post("/api/workflows/{name}/run")
async def run_workflow(name: str, req: WorkflowRunRequest):
    """Run a saved workflow with provided inputs."""
    safe = name.replace("/", "_").replace("..", "_")
    path = WORKFLOWS_DIR / f"{safe}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")

    data = json.loads(path.read_text())

    run_id = str(uuid.uuid4())[:8]
    workflow_runs[run_id] = {
        "run_id": run_id,
        "workflow_name": name,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "log_lines": [],
    }

    async def _run():
        from workflow_engine import WorkflowPlayer
        from workflow_models import Workflow

        workflow = Workflow(**data)
        player = WorkflowPlayer(
            workflow=workflow,
            inputs=req.inputs,
            headless=req.headless,
        )
        result = await asyncio.to_thread(player.run)
        workflow_runs[run_id]["log_lines"] = result.get("log", [])
        workflow_runs[run_id]["status"] = result.get("status", "failed")
        workflow_runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_run())
    return {"run_id": run_id, "status": "running"}


# --- Output Files ---

OUTPUT_DIR = Path(DATA_DIR)


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(OUTPUT_DIR)),
        "size": stat.st_size,
        "size_fmt": _fmt_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "is_dir": path.is_dir(),
    }


@app.get("/api/files")
async def list_output_files(path: str = ""):
    """List files in the output directory."""
    target = (OUTPUT_DIR / path).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_file():
        return _file_info(target)
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    return {
        "path": path or "/",
        "entries": [_file_info(e) for e in entries],
    }


@app.get("/api/files/{path:path}")
async def get_output_file_info(path: str):
    """Get file info, with text content for small text files."""
    target = (OUTPUT_DIR / path).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        return await list_output_files(path)
    info = _file_info(target)
    text_exts = {".json", ".jsonl", ".txt", ".csv", ".yaml", ".yml", ".html", ".md", ".log"}
    if target.suffix.lower() in text_exts and info["size"] < 5_000_000:
        try:
            info["content"] = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            info["content"] = None
    return info


@app.get("/api/files-download/{path:path}")
async def download_output_file(path: str):
    """Download a file from the output directory."""
    target = (OUTPUT_DIR / path).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media_type or "application/octet-stream", filename=target.name)


# --- Dashboard redirect ---


@app.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/dashboard/")


# --- MCP over HTTP ---
# Serves the MCP protocol at /mcp so remote agents (Hermes, etc.) can
# connect directly via URL — no file copying or local installs needed.
#
# Hermes config:
#   mcp_servers:
#     crawler:
#       url: "http://<container-ip>:8080/mcp"
#       headers:
#         X-API-Key: "your-key"

from mcp_server import TOOLS as MCP_TOOLS, handle_tool as mcp_handle_tool


@app.post("/mcp")
async def mcp_endpoint(request: dict):
    """
    MCP JSON-RPC endpoint. Handles initialize, tools/list, tools/call.
    Agents connect here as a remote MCP server.
    """
    msg_id = request.get("id")
    method = request.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "crawler", "version": "1.0.0"},
            },
        }

    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": MCP_TOOLS},
        }

    elif method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        # Call the tool handler — it makes HTTP calls back to our own API
        # Pass the real API key so internal calls pass auth
        local_url = f"http://127.0.0.1:{os.environ.get('CRAWLER_API_PORT', '8080')}"
        try:
            result = mcp_handle_tool(tool_name, tool_args, local_url, API_KEY)
            text = json.dumps(result, indent=2) if not isinstance(result, str) else result
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


# Also serve MCP via SSE for agents that expect that transport
@app.get("/mcp/sse")
async def mcp_sse_info():
    """Info endpoint for SSE-based MCP clients."""
    return {
        "message": "POST JSON-RPC requests to /mcp",
        "protocol": "MCP",
        "version": "2024-11-05",
        "tools": len(MCP_TOOLS),
    }


# --- Static files mount (must be last) ---

static_dir = Path(os.path.join(WORKING_DIR, "static"))
static_dir.mkdir(exist_ok=True)
app.mount("/dashboard", StaticFiles(directory=str(static_dir), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CRAWLER_API_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
