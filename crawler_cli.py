#!/usr/bin/env python3
"""
Crawler CLI

Command-line interface for the crawler API. Works with any agent that
has shell/Bash access, or for manual use and scripting.

Usage:
    python crawler_cli.py [--api-url URL] [--api-key KEY] <command> [args...]

Environment variables (alternative to flags):
    CRAWLER_API_URL  - e.g. http://192.168.1.190:8080
    CRAWLER_API_KEY  - API key (if configured on server)

Examples:
    python crawler_cli.py health
    python crawler_cli.py recipes list
    python crawler_cli.py crawl run example_quotes.yaml
    python crawler_cli.py browser open
    python crawler_cli.py browser navigate https://youtube.com
    python crawler_cli.py browser snapshot
    python crawler_cli.py browser click --text "Notifications"
    python crawler_cli.py browser record start
    python crawler_cli.py browser record stop
    python crawler_cli.py browser record save my_workflow
    python crawler_cli.py browser close
    python crawler_cli.py workflows list
    python crawler_cli.py workflows run my_workflow
    python crawler_cli.py files list
    python crawler_cli.py login open https://youtube.com --label YouTube
    python crawler_cli.py login save
    python crawler_cli.py login sessions
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def _call(base_url: str, path: str, method: str = "GET", body: dict | None = None, api_key: str = ""):
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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
        print(f"Error ({e.code}): {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _out(data):
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Crawler CLI — control the crawler API from the command line",
        usage="%(prog)s [--api-url URL] [--api-key KEY] <command> ...",
    )
    parser.add_argument("--api-url", default=os.environ.get("CRAWLER_API_URL", "http://localhost:8080"))
    parser.add_argument("--api-key", default=os.environ.get("CRAWLER_API_KEY", ""))

    sub = parser.add_subparsers(dest="command", required=True)

    # --- health ---
    sub.add_parser("health", help="Check API server status")

    # --- recipes ---
    recipes = sub.add_parser("recipes", help="Manage scraping recipes")
    recipes_sub = recipes.add_subparsers(dest="action", required=True)
    recipes_sub.add_parser("list", help="List all recipes")
    rget = recipes_sub.add_parser("get", help="Get recipe content")
    rget.add_argument("path", help="Recipe path (e.g. example_quotes.yaml)")
    rcreate = recipes_sub.add_parser("create", help="Create recipe from JSON")
    rcreate.add_argument("json_data", help="Recipe JSON string")
    rdel = recipes_sub.add_parser("delete", help="Delete a recipe")
    rdel.add_argument("path", help="Recipe path")

    # --- crawl ---
    crawl = sub.add_parser("crawl", help="Run crawls")
    crawl_sub = crawl.add_subparsers(dest="action", required=True)
    crun = crawl_sub.add_parser("run", help="Run a recipe-based crawl")
    crun.add_argument("recipe", help="Recipe path (e.g. example_quotes.yaml)")
    crun.add_argument("--visible", action="store_true", help="Run with visible browser")
    cfull = crawl_sub.add_parser("full", help="Run a full HTML crawl")
    cfull.add_argument("urls", nargs="+", help="URLs to crawl")
    cfull.add_argument("--max-depth", type=int, default=2)
    cfull.add_argument("--domains", nargs="*", default=[])
    crawl_sub.add_parser("list", help="List all crawl tasks")
    cstatus = crawl_sub.add_parser("status", help="Get crawl task status")
    cstatus.add_argument("task_id", help="Task ID")
    cstatus.add_argument("--tail", type=int, default=50)

    # --- browser ---
    browser = sub.add_parser("browser", help="Control the live browser")
    browser_sub = browser.add_subparsers(dest="action", required=True)
    browser_sub.add_parser("open", help="Open browser session")
    browser_sub.add_parser("close", help="Close browser session")
    browser_sub.add_parser("status", help="Check browser status")
    browser_sub.add_parser("snapshot", help="Read current page text")
    browser_sub.add_parser("screenshot", help="Take a screenshot (base64)")
    browser_sub.add_parser("links", help="Get all links on the page")
    bnav = browser_sub.add_parser("navigate", help="Navigate to URL")
    bnav.add_argument("url", help="URL to navigate to")
    bclick = browser_sub.add_parser("click", help="Click an element")
    bclick.add_argument("--selector", help="CSS selector")
    bclick.add_argument("--text", help="Visible text to click")
    btype = browser_sub.add_parser("type", help="Type into a field")
    btype.add_argument("selector", help="CSS selector")
    btype.add_argument("text", help="Text to type")
    bkey = browser_sub.add_parser("key", help="Press a key")
    bkey.add_argument("key_name", help="Key name (Enter, Tab, Escape, etc.)")
    bscroll = browser_sub.add_parser("scroll", help="Scroll the page")
    bscroll.add_argument("--direction", default="down", choices=["up", "down"])
    bscroll.add_argument("--amount", type=int, default=500)
    beval = browser_sub.add_parser("eval", help="Run JavaScript")
    beval.add_argument("expression", help="JavaScript expression")

    # --- browser record ---
    brec = browser_sub.add_parser("record", help="Record browser actions")
    brec_sub = brec.add_subparsers(dest="rec_action", required=True)
    brec_sub.add_parser("start", help="Start recording")
    brec_sub.add_parser("stop", help="Stop recording")
    bsave = brec_sub.add_parser("save", help="Save recording as workflow")
    bsave.add_argument("name", help="Workflow name")
    bsave.add_argument("--description", default="", help="Workflow description")

    # --- workflows ---
    workflows = sub.add_parser("workflows", help="Manage workflows")
    wf_sub = workflows.add_subparsers(dest="action", required=True)
    wf_sub.add_parser("list", help="List all workflows")
    wfget = wf_sub.add_parser("get", help="Get workflow JSON")
    wfget.add_argument("name", help="Workflow name")
    wfrun = wf_sub.add_parser("run", help="Run a workflow")
    wfrun.add_argument("name", help="Workflow name")
    wfrun.add_argument("--inputs", default="{}", help="JSON inputs")
    wfrun.add_argument("--visible", action="store_true")
    wfdel = wf_sub.add_parser("delete", help="Delete a workflow")
    wfdel.add_argument("name", help="Workflow name")

    # --- files ---
    files = sub.add_parser("files", help="Browse output files")
    files_sub = files.add_subparsers(dest="action", required=True)
    flist = files_sub.add_parser("list", help="List files")
    flist.add_argument("path", nargs="?", default="", help="Subdirectory path")
    fget = files_sub.add_parser("get", help="Get file content")
    fget.add_argument("path", help="File path")

    # --- login ---
    login = sub.add_parser("login", help="Manage login sessions")
    login_sub = login.add_subparsers(dest="action", required=True)
    lopen = login_sub.add_parser("open", help="Open browser for login")
    lopen.add_argument("url", help="Login page URL")
    lopen.add_argument("--label", default="", help="Friendly site name")
    login_sub.add_parser("save", help="Save login session")
    login_sub.add_parser("cancel", help="Cancel login")
    login_sub.add_parser("status", help="Check login status")
    login_sub.add_parser("sessions", help="List saved sessions")

    args = parser.parse_args()
    url = args.api_url
    key = args.api_key
    call = lambda path, **kw: _call(url, path, api_key=key, **kw)

    # --- Dispatch ---

    if args.command == "health":
        _out(call("/health"))

    elif args.command == "recipes":
        if args.action == "list":
            _out(call("/api/recipes"))
        elif args.action == "get":
            _out(call(f"/api/recipes/{args.path}"))
        elif args.action == "create":
            _out(call("/api/recipes", method="POST", body=json.loads(args.json_data)))
        elif args.action == "delete":
            _out(call(f"/api/recipes/{args.path}", method="DELETE"))

    elif args.command == "crawl":
        if args.action == "run":
            _out(call("/api/crawl", method="POST", body={"recipe_path": args.recipe, "headless": not args.visible}))
        elif args.action == "full":
            _out(call("/api/crawl/full", method="POST", body={
                "urls": args.urls, "max_depth": args.max_depth,
                "allowed_domains": args.domains, "headless": True,
            }))
        elif args.action == "list":
            _out(call("/api/crawl"))
        elif args.action == "status":
            _out(call(f"/api/crawl/{args.task_id}?tail={args.tail}"))

    elif args.command == "browser":
        if args.action == "open":
            _out(call("/api/browser/open", method="POST"))
        elif args.action == "close":
            _out(call("/api/browser/close", method="POST"))
        elif args.action == "status":
            _out(call("/api/browser/status"))
        elif args.action == "snapshot":
            _out(call("/api/browser/snapshot", method="POST"))
        elif args.action == "screenshot":
            _out(call("/api/browser/screenshot", method="POST", body={}))
        elif args.action == "links":
            _out(call("/api/browser/get_links", method="POST"))
        elif args.action == "navigate":
            _out(call("/api/browser/navigate", method="POST", body={"url": args.url}))
        elif args.action == "click":
            body = {}
            if args.selector:
                body["selector"] = args.selector
            if args.text:
                body["text"] = args.text
            if not body:
                print("Error: provide --selector or --text", file=sys.stderr)
                sys.exit(1)
            _out(call("/api/browser/click", method="POST", body=body))
        elif args.action == "type":
            _out(call("/api/browser/type", method="POST", body={"selector": args.selector, "text": args.text}))
        elif args.action == "key":
            _out(call("/api/browser/press_key", method="POST", body={"key": args.key_name}))
        elif args.action == "scroll":
            _out(call("/api/browser/scroll", method="POST", body={"direction": args.direction, "amount": args.amount}))
        elif args.action == "eval":
            _out(call("/api/browser/evaluate", method="POST", body={"expression": args.expression}))
        elif args.action == "record":
            if args.rec_action == "start":
                _out(call("/api/browser/record/start", method="POST"))
            elif args.rec_action == "stop":
                _out(call("/api/browser/record/stop", method="POST"))
            elif args.rec_action == "save":
                _out(call("/api/browser/record/save", method="POST", body={
                    "name": args.name, "description": args.description,
                }))

    elif args.command == "workflows":
        if args.action == "list":
            _out(call("/api/workflows"))
        elif args.action == "get":
            _out(call(f"/api/workflows/{args.name}"))
        elif args.action == "run":
            _out(call(f"/api/workflows/{args.name}/run", method="POST", body={
                "inputs": json.loads(args.inputs), "headless": not args.visible,
            }))
        elif args.action == "delete":
            _out(call(f"/api/workflows/{args.name}", method="DELETE"))

    elif args.command == "files":
        if args.action == "list":
            path = args.path
            _out(call(f"/api/files?path={path}" if path else "/api/files"))
        elif args.action == "get":
            _out(call(f"/api/files/{args.path}"))

    elif args.command == "login":
        if args.action == "open":
            _out(call("/api/login/open", method="POST", body={"url": args.url, "label": args.label}))
        elif args.action == "save":
            _out(call("/api/login/save", method="POST"))
        elif args.action == "cancel":
            _out(call("/api/login/cancel", method="POST"))
        elif args.action == "status":
            _out(call("/api/login/status"))
        elif args.action == "sessions":
            _out(call("/api/login/sessions"))


if __name__ == "__main__":
    main()
