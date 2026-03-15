"""
Crawler Data File Server

Lightweight HTTP file server that serves the crawler output directory
with directory listing, file downloads, and a JSON API.

Usage:
    python data_server.py
    # or via systemd: systemctl start crawler-data

Environment variables:
    CRAWLER_DATA_DIR   - Directory to serve (default: /opt/crawler/output)
    CRAWLER_DATA_MOUNT - Optional mount/symlink point for remote access
    CRAWLER_DATA_PORT  - Port to listen on (default: 8081)
"""

import os
import json
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get("CRAWLER_DATA_DIR", "/opt/crawler/output"))
DATA_MOUNT = os.environ.get("CRAWLER_DATA_MOUNT")


@asynccontextmanager
async def lifespan(app):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    setup_mount()
    yield


app = FastAPI(
    title="Crawler Data Server",
    description="HTTP file server for crawler output data",
    version="1.0.0",
    lifespan=lifespan,
)


def setup_mount():
    """Create symlink for configurable mount point if specified."""
    if not DATA_MOUNT:
        return
    mount_path = Path(DATA_MOUNT)
    if mount_path.exists() and not mount_path.is_symlink():
        return  # Don't overwrite an existing real directory
    if mount_path.is_symlink():
        if mount_path.resolve() == DATA_DIR.resolve():
            return
        mount_path.unlink()
    mount_path.parent.mkdir(parents=True, exist_ok=True)
    mount_path.symlink_to(DATA_DIR)


def file_info(path: Path, base: Path) -> dict:
    """Get file metadata as dict."""
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(base)),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "is_dir": path.is_dir(),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "data_dir": str(DATA_DIR), "mount": DATA_MOUNT}


@app.get("/", response_class=HTMLResponse)
async def index():
    """Directory listing as HTML."""
    return await directory_listing("")


@app.get("/browse/{path:path}", response_class=HTMLResponse)
async def directory_listing(path: str = ""):
    """Browse directories with HTML listing."""
    target = (DATA_DIR / path).resolve()
    if not str(target).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_file():
        return FileResponse(target)

    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    rows = []
    if path:
        parent = str(Path(path).parent) if Path(path).parent != Path(path) else ""
        rows.append(f'<tr><td><a href="/browse/{parent}">..</a></td><td></td><td></td></tr>')

    for entry in entries:
        rel = entry.relative_to(DATA_DIR)
        if entry.is_dir():
            rows.append(
                f'<tr><td><a href="/browse/{rel}/">{entry.name}/</a></td>'
                f"<td>-</td><td>-</td></tr>"
            )
        else:
            stat = entry.stat()
            size = _fmt_size(stat.st_size)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(
                f'<tr><td><a href="/files/{rel}">{entry.name}</a></td>'
                f"<td>{size}</td><td>{mtime}</td></tr>"
            )

    html = f"""<!DOCTYPE html>
<html><head><title>Data: /{path}</title>
<style>
body {{ font-family: monospace; margin: 2em; }}
table {{ border-collapse: collapse; }}
td, th {{ padding: 4px 16px; text-align: left; }}
a {{ color: #0066cc; }}
</style></head>
<body>
<h2>/{path or "output"}</h2>
<table><tr><th>Name</th><th>Size</th><th>Modified</th></tr>
{"".join(rows)}
</table>
</body></html>"""
    return HTMLResponse(html)


@app.get("/files/{path:path}")
async def download_file(path: str):
    """Download a file from the data directory."""
    target = (DATA_DIR / path).resolve()
    if not str(target).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@app.get("/api/files")
async def list_files_api(path: str = ""):
    """List files as JSON."""
    target = (DATA_DIR / path).resolve()
    if not str(target).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_file():
        return file_info(target, DATA_DIR)

    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    return {
        "path": path or "/",
        "entries": [file_info(e, DATA_DIR) for e in entries],
    }


@app.get("/api/files/{path:path}")
async def get_file_api(path: str):
    """Get file contents as JSON (for text files) or metadata (for binary)."""
    target = (DATA_DIR / path).resolve()
    if not str(target).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target.is_dir():
        return await list_files_api(path)

    info = file_info(target, DATA_DIR)
    # Try to read as text for common text types
    text_exts = {".json", ".jsonl", ".txt", ".csv", ".yaml", ".yml", ".html", ".md", ".log"}
    if target.suffix.lower() in text_exts and info["size"] < 10_000_000:
        try:
            info["content"] = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            info["content"] = None
            info["note"] = "Could not read as text"
    else:
        info["content"] = None
        info["note"] = f"Binary file or too large. Download via /files/{path}"
    return info


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CRAWLER_DATA_PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port)
