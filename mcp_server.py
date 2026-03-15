#!/usr/bin/env python3
"""
Crawler MCP Server

Exposes the crawler HTTP API as MCP tools so any MCP-compatible agent
(Claude Code, etc.) can control the crawler remotely — no agent CLI
needed inside the container.

Setup in Claude Code:
    claude mcp add crawler -- python /path/to/mcp_server.py \
        --api-url http://<container-ip>:8080 \
        --api-key <your-key>

Environment variables (alternative to CLI args):
    CRAWLER_API_URL  - e.g. http://192.168.1.190:8080
    CRAWLER_API_KEY  - API key (if configured on server)
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any

# MCP protocol via stdio — minimal implementation, no extra dependencies.
# Implements just enough of the MCP spec for tool serving.


def _api_call(
    base_url: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    api_key: str = "",
) -> dict | list | str:
    """Make an HTTP request to the crawler API."""
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(error_body).get("detail", error_body)
        except Exception:
            detail = error_body
        return {"error": True, "status": e.code, "detail": detail}
    except urllib.error.URLError as e:
        return {"error": True, "detail": f"Connection failed: {e.reason}"}


# --- Tool definitions ---

TOOLS = [
    {
        "name": "crawler_health",
        "description": "Check if the crawler API server is running and whether an internal agent CLI is available.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_list_recipes",
        "description": "List all available scraping recipes.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_get_recipe",
        "description": "Get the full YAML content of a recipe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Recipe path, e.g. 'example_quotes.yaml' or 'my-project/recipe.yaml'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "crawler_create_recipe",
        "description": "Create a new scraping recipe. Defines how to extract item links from paginated list pages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Recipe name (used as filename)"},
                "start_urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to start crawling from"},
                "list_scope_css": {"type": "string", "description": "CSS selector for item containers on the list page"},
                "item_link_css": {"type": "string", "description": "CSS selector for links within each item (default: a[href])"},
                "pagination_type": {"type": "string", "enum": ["next", "all_links", "url_template"], "description": "Pagination strategy"},
                "next_css": {"type": "string", "description": "CSS selector for next page button (when pagination_type=next)"},
                "max_list_pages": {"type": "integer", "description": "Max list pages to visit"},
                "max_items": {"type": "integer", "description": "Max items to discover"},
            },
            "required": ["name", "start_urls", "list_scope_css"],
        },
    },
    {
        "name": "crawler_run_recipe",
        "description": "Start a recipe-based list crawl. Returns a task ID to check progress.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_path": {"type": "string", "description": "Recipe path, e.g. 'example_quotes.yaml'"},
                "headless": {"type": "boolean", "description": "Run browser headlessly (default: true)"},
            },
            "required": ["recipe_path"],
        },
    },
    {
        "name": "crawler_run_full",
        "description": "Start a full HTML crawl from one or more URLs with depth and domain controls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to crawl"},
                "max_depth": {"type": "integer", "description": "Max crawl depth (0-10, default: 2)"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Restrict crawl to these domains"},
                "headless": {"type": "boolean", "description": "Run browser headlessly (default: true)"},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "crawler_task_status",
        "description": "Get the status and log tail of a running or completed crawl task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID returned by crawler_run_recipe or crawler_run_full"},
                "tail": {"type": "integer", "description": "Number of log lines to return (default: 50)"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "crawler_list_tasks",
        "description": "List all crawl tasks (running and completed).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_login_open",
        "description": "Open a browser on the server for manual login to a website. The browser stays open for VNC-based interaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Login page URL, e.g. https://www.facebook.com/login"},
                "label": {"type": "string", "description": "Friendly name for this site, e.g. 'Facebook'"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "crawler_login_save",
        "description": "Save cookies from the active login browser session. Call after the user has logged in.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_login_cancel",
        "description": "Close the active login browser without saving cookies.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_login_status",
        "description": "Check if there is an active login browser session.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_login_sessions",
        "description": "List domains with saved login cookies.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_list_files",
        "description": "List output files and directories. Use path to browse subdirectories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Subdirectory path (empty for root)"},
            },
            "required": [],
        },
    },
    {
        "name": "crawler_get_file",
        "description": "Get file info and content (for text files like JSONL, JSON, CSV). Returns file content inline for small text files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to output directory"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "crawler_list_workflows",
        "description": "List all saved replayable workflows.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "crawler_run_workflow",
        "description": "Run a saved workflow with template inputs. Workflows are replayable browser automations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name"},
                "inputs": {"type": "object", "description": "Input values for template fields (e.g. {\"text\": \"hello\"})"},
                "headless": {"type": "boolean", "description": "Run headlessly (default: true)"},
            },
            "required": ["name"],
        },
    },
    # --- Live browser session tools ---
    {
        "name": "browser_open",
        "description": "Open a persistent browser session on the server. Loads saved login cookies automatically. Must be called before other browser_ tools.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_navigate",
        "description": "Navigate the browser to a URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": "Click an element by CSS selector or visible text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to click"},
                "text": {"type": "string", "description": "Visible text to click (alternative to selector)"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into a form field identified by CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input field"},
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_press_key",
        "description": "Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to press, e.g. 'Enter', 'Tab', 'Escape'"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Get the current page's text content, URL, and title. Use this to read what's on screen.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current page. Returns base64-encoded PNG.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default: false)"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_get_links",
        "description": "Get all links on the current page with their text and URLs.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page up or down.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction"},
                "amount": {"type": "integer", "description": "Pixels to scroll (default: 500)"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_evaluate",
        "description": "Run JavaScript in the browser and return the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "browser_close",
        "description": "Close the persistent browser session and save cookies.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_status",
        "description": "Check if a browser session is currently active and whether recording is on.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_record_start",
        "description": "Start recording browser actions. All subsequent browser commands (navigate, click, type, etc.) will be captured as replayable workflow steps.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_record_stop",
        "description": "Stop recording and return the captured steps.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_record_save",
        "description": "Save the recorded steps as a named workflow. The workflow can be replayed later with crawler_run_workflow without any AI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name (used as filename)"},
                "description": {"type": "string", "description": "What this workflow does"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags"},
            },
            "required": ["name"],
        },
    },
]


def handle_tool(name: str, args: dict, api_url: str, api_key: str) -> Any:
    """Execute a tool and return the result."""
    call = lambda path, **kw: _api_call(api_url, path, api_key=api_key, **kw)

    if name == "crawler_health":
        return call("/health")

    elif name == "crawler_list_recipes":
        return call("/api/recipes")

    elif name == "crawler_get_recipe":
        return call(f"/api/recipes/{args['path']}")

    elif name == "crawler_create_recipe":
        return call("/api/recipes", method="POST", body=args)

    elif name == "crawler_run_recipe":
        body = {"recipe_path": args["recipe_path"], "headless": args.get("headless", True)}
        return call("/api/crawl", method="POST", body=body)

    elif name == "crawler_run_full":
        body = {
            "urls": args["urls"],
            "max_depth": args.get("max_depth", 2),
            "allowed_domains": args.get("allowed_domains", []),
            "headless": args.get("headless", True),
        }
        return call("/api/crawl/full", method="POST", body=body)

    elif name == "crawler_task_status":
        tail = args.get("tail", 50)
        return call(f"/api/crawl/{args['task_id']}?tail={tail}")

    elif name == "crawler_list_tasks":
        return call("/api/crawl")

    elif name == "crawler_login_open":
        body = {"url": args["url"], "label": args.get("label", "")}
        return call("/api/login/open", method="POST", body=body)

    elif name == "crawler_login_save":
        return call("/api/login/save", method="POST")

    elif name == "crawler_login_cancel":
        return call("/api/login/cancel", method="POST")

    elif name == "crawler_login_status":
        return call("/api/login/status")

    elif name == "crawler_login_sessions":
        return call("/api/login/sessions")

    elif name == "crawler_list_files":
        path = args.get("path", "")
        return call(f"/api/files?path={path}" if path else "/api/files")

    elif name == "crawler_get_file":
        return call(f"/api/files/{args['path']}")

    elif name == "crawler_list_workflows":
        return call("/api/workflows")

    elif name == "crawler_run_workflow":
        body = {"inputs": args.get("inputs", {}), "headless": args.get("headless", True)}
        return call(f"/api/workflows/{args['name']}/run", method="POST", body=body)

    # --- Browser session tools ---

    elif name == "browser_open":
        return call("/api/browser/open", method="POST")

    elif name == "browser_navigate":
        return call("/api/browser/navigate", method="POST", body={"url": args["url"]})

    elif name == "browser_click":
        body = {}
        if args.get("selector"):
            body["selector"] = args["selector"]
        if args.get("text"):
            body["text"] = args["text"]
        return call("/api/browser/click", method="POST", body=body)

    elif name == "browser_type":
        return call("/api/browser/type", method="POST", body={"selector": args["selector"], "text": args["text"]})

    elif name == "browser_press_key":
        return call("/api/browser/press_key", method="POST", body={"key": args["key"]})

    elif name == "browser_snapshot":
        return call("/api/browser/snapshot", method="POST")

    elif name == "browser_screenshot":
        return call("/api/browser/screenshot", method="POST", body={"full_page": args.get("full_page", False)})

    elif name == "browser_get_links":
        return call("/api/browser/get_links", method="POST")

    elif name == "browser_scroll":
        return call("/api/browser/scroll", method="POST", body={
            "direction": args.get("direction", "down"),
            "amount": args.get("amount", 500),
        })

    elif name == "browser_evaluate":
        return call("/api/browser/evaluate", method="POST", body={"expression": args["expression"]})

    elif name == "browser_close":
        return call("/api/browser/close", method="POST")

    elif name == "browser_status":
        return call("/api/browser/status")

    return {"error": f"Unknown tool: {name}"}


# --- MCP stdio protocol ---


def _write_msg(msg: dict):
    """Write a JSON-RPC message to stdout."""
    raw = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(raw)}\r\n\r\n{raw}")
    sys.stdout.flush()


def _read_msg() -> dict | None:
    """Read a JSON-RPC message from stdin."""
    # Read headers
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()

    length = int(headers.get("Content-Length", 0))
    if length == 0:
        return None

    body = sys.stdin.read(length)
    return json.loads(body)


def serve(api_url: str, api_key: str):
    """Run the MCP server on stdio."""
    while True:
        msg = _read_msg()
        if msg is None:
            break

        msg_id = msg.get("id")
        method = msg.get("method", "")

        if method == "initialize":
            _write_msg({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "crawler", "version": "1.0.0"},
                },
            })

        elif method == "notifications/initialized":
            pass  # No response needed

        elif method == "tools/list":
            _write_msg({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            try:
                result = handle_tool(tool_name, tool_args, api_url, api_key)
                text = json.dumps(result, indent=2) if not isinstance(result, str) else result
                _write_msg({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                    },
                })
            except Exception as e:
                _write_msg({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })

        elif method == "ping":
            _write_msg({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        else:
            _write_msg({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawler MCP Server")
    parser.add_argument("--api-url", default=os.environ.get("CRAWLER_API_URL", "http://localhost:8080"),
                        help="Crawler API URL (or set CRAWLER_API_URL)")
    parser.add_argument("--api-key", default=os.environ.get("CRAWLER_API_KEY", ""),
                        help="API key (or set CRAWLER_API_KEY)")
    args = parser.parse_args()

    serve(args.api_url, args.api_key)
