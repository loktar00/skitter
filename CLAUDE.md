# Crawler Project Context

## Overview
Web automation platform at `/opt/crawler/` on a Proxmox LXC container. Three capabilities:
1. **Full HTML Crawl** — recursive page download (`--mode crawl`)
2. **List Crawl** — recipe-driven link discovery (`--mode list`)
3. **AI-Recorded Workflows** — describe a task in English, the agent performs it via Playwright, steps are recorded as a replayable workflow (no AI needed on replay)

Uses Playwright 1.58+ and curl-cffi for scraping with CloudFlare bypass. YAML recipe system for defining scraping targets. Integrated with n8n for scheduled runs via SSH.

## Environment
- **Python**: 3.11 (venv at `/opt/crawler/venv/`)
- **Node.js**: 22 LTS (for Playwright MCP server, if using agent workflow recording)
- **Display**: Xvfb on `:99` (for headless browser automation, VNC for auth)
- **Activate venv**: `source /opt/crawler/venv/bin/activate`

## Key Files

| File | Purpose |
|------|---------|
| `crawler.py` | Main crawler — `--mode crawl` (full HTML) or `--mode list` (recipe) |
| `list_crawler.py` | List-mode crawler implementation |
| `crawler_config.py` | Config: URLs, depth, domains, headless mode, rate limits |
| `recipe_loader.py` | Loads and validates YAML recipe files |
| `validate_recipe.py` | CLI tool to validate recipe YAML |
| `browser_helper.py` | CLI for browser actions: navigate, click, type, screenshot, etc. |
| `api_server.py` | FastAPI HTTP API — dashboard, crawl control, workflow management |
| `data_server.py` | HTTP file server for output data |
| `workflow_models.py` | Pydantic models: Workflow, WorkflowStep, WorkflowInputField |
| `workflow_recorder.py` | Parses agent CLI stream-json output → replayable workflow steps |
| `workflow_engine.py` | Replays workflows via Playwright sync API (no AI needed) |
| `workflows/` | Saved workflow JSON files (gitignored) |

## Recipe System

Recipes are YAML files in `recipes/`. Template:

```yaml
start_urls:
  - "https://example.com/page"

list_scope_css: "div.item"       # CSS selector scoping each item
item_link_css: "a[href]"         # Link selector within each item

pagination:
  type: next                     # "next" or "url_template"
  next_css: "a.next-page"       # For type: next

limits:
  max_list_pages: 5
  max_items: 100

output:
  items_jsonl: "output/my_items.jsonl"
  pages_jsonl: "output/my_pages.jsonl"
```

**Workflow**: Create recipe → `python validate_recipe.py recipes/my_recipe.yaml` → `python crawler.py --mode list --recipe recipes/my_recipe.yaml --headless`

## Browser Helper

```bash
# Screenshot a page
python browser_helper.py screenshot https://example.com /tmp/test.png

# Navigate and get page info
python browser_helper.py navigate https://example.com --screenshot /tmp/nav.png

# Click an element
python browser_helper.py click https://example.com "button.submit"

# Fill form and submit
python browser_helper.py fill-and-submit https://example.com \
  --fields '{"#username": "user", "#password": "pass"}' \
  --submit "button[type=submit]"

# Dump all links as JSON
python browser_helper.py dump-links https://example.com

# Run JS and get result
python browser_helper.py evaluate https://example.com "document.title"
```

All commands output JSON to stdout. Use `--visible` for non-headless mode. Cookies are saved/loaded from `output/browser_session/cookies.json` by default.

## API Server (port 8080)

```bash
# Send a task
curl -X POST http://localhost:8080/task \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "List files in output/", "timeout": 60}'

# Continue a conversation
curl -X POST http://localhost:8080/task/continue \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "...", "prompt": "Now process those files"}'

# Open page for VNC login
curl -X POST http://localhost:8080/auth-prepare \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://facebook.com/login"}'

# Health check
curl http://localhost:8080/health
```

Service: `systemctl start crawler-api`

## Data Server (port 8081)

```bash
curl http://localhost:8081/api/files          # List all output files as JSON
curl http://localhost:8081/files/my-project/items.jsonl  # Download file
```

Browse at `http://<host>:8081/` for HTML directory listing.
Service: `systemctl start crawler-data`

## n8n Integration
- SSH scripts in repo root for scheduled recipe execution
- HTTP API: `POST http://<crawler-ip>:8080/task` with JSON body from n8n HTTP Request node

## Cookie/Session Management
- Cookies saved at `output/browser_session/cookies.json`
- For authenticated sites (Facebook, etc.), first run with `--visible` flag and log in via VNC
- Or use `POST /auth-prepare` endpoint to open a login page on VNC display

## Workflow System

### Record a workflow (AI-driven)
```bash
# Via dashboard: Workflows tab → Record New → enter prompt → Start Recording
# Via API:
curl -X POST http://localhost:8080/api/workflows/record \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Post a tweet saying hello", "name": "post_tweet"}'
# Returns SSE stream with live progress, then workflow_result with extracted steps
```

### Run a workflow (no AI)
```bash
curl -X POST http://localhost:8080/api/workflows/post_tweet/run \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"text": "Hello world"}, "headless": true}'
```

### Workflow JSON format
Saved in `workflows/<name>.json`. Steps use Playwright actions (navigate, click, type, etc.) with `{{input.field}}` template interpolation.

### API Endpoints
```
GET    /api/workflows              — list all workflows
GET    /api/workflows/{name}       — get workflow JSON
POST   /api/workflows              — save workflow
DELETE /api/workflows/{name}       — delete workflow
POST   /api/workflows/record       — start AI recording (SSE)
POST   /api/workflows/{name}/run   — run workflow with inputs
GET    /api/workflows/runs          — list all runs
GET    /api/workflows/runs/{id}    — get run status + log
POST   /api/crawl/full             — start full HTML crawl
```

## Quick Setup

```bash
# System packages
apt install -y novnc xvfb x11vnc

# Python venv
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements-api.txt

# Playwright browser
playwright install chromium

# Start services
systemctl start xvfb x11vnc websockify crawler-api crawler-data

# Agent MCP setup (for workflow recording — uses Claude Code CLI as default agent)
# Replace with your preferred agent CLI if different
claude mcp add playwright npx @playwright/mcp@latest -e DISPLAY=:99
```

Dashboard: `http://<host>:8080/dashboard/`

## Conventions
- All output as JSONL (one JSON object per line) for items
- Recipes in `recipes/<project>/` subdirectories
- Python code uses the venv: `/opt/crawler/venv/bin/python`
- Always set `DISPLAY=:99` for browser operations
