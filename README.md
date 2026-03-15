# Web Crawler

A flexible web crawling and browser automation platform with CloudFlare bypass, recipe-driven scraping, workflow replay, and HTTP API access. Can be controlled by any AI agent via MCP or REST API.

## Features

- **General-purpose crawling** — BFS link discovery with depth control and domain filtering
- **Recipe-driven list crawling** — YAML-configured scraping of paginated list pages
- **CloudFlare bypass** — dual-engine approach using curl-cffi and Playwright
- **Anti-detection** — webdriver property override, randomized viewport, human-like delays
- **Remote browser control** — persistent browser sessions an external agent can drive (navigate, click, type, screenshot, snapshot)
- **MCP server** — built-in MCP endpoint at `/mcp` so any MCP-compatible agent can connect with just a URL
- **HTTP API** — full REST API for crawling, login management, recipe CRUD, file browsing, and workflow replay
- **Dashboard** — web UI for managing crawls, recipes, logins, and output files
- **Session persistence** — saved cookies for authenticated scraping (YouTube, Facebook, etc.)
- **Workflow system** — record browser automations and replay them without AI
- **Resume capability** — state tracking to pick up interrupted crawls

## Quick Setup (Container)

Run one command on a fresh Debian/Ubuntu container:

```bash
curl -sL https://raw.githubusercontent.com/loktar00/crawler/main/setup-crawler.sh | bash
```

This installs everything, sets up systemd services, and starts the dashboard.

## Connecting an AI Agent

The crawler exposes an MCP endpoint so external agents can control it remotely — no agent CLI needed inside the container.

### Claude Code

Run this on the machine where Claude Code is installed:

```bash
claude mcp add crawler -- python /path/to/mcp_server.py \
    --api-url http://<container-ip>:8080 \
    --api-key <your-key>
```

`mcp_server.py` is a single-file, zero-dependency MCP bridge. Copy it from the repo or clone locally — it just makes HTTP calls to the crawler API.

Alternatively, set environment variables instead of CLI args:

```bash
export CRAWLER_API_URL=http://<container-ip>:8080
export CRAWLER_API_KEY=<your-key>
claude mcp add crawler -- python /path/to/mcp_server.py
```

### Hermes Agent

No file copying needed. Hermes connects directly to the built-in MCP endpoint. Add to your Hermes config:

```yaml
mcp_servers:
  crawler:
    url: "http://<container-ip>:8080/mcp"
    headers:
      X-API-Key: "<your-key>"
```

Hermes will discover all tools at startup. Use `/reload-mcp` in Hermes to refresh after config changes.

### Any MCP-Compatible Agent

Any agent that supports remote MCP servers over HTTP can connect to:

```
POST http://<container-ip>:8080/mcp
Header: X-API-Key: <your-key>
Body: JSON-RPC (MCP protocol)
```

### Available MCP Tools

Once connected, the agent gets these tools:

| Tool | Description |
|------|-------------|
| `crawler_health` | Check API server status |
| `crawler_list_recipes` | List scraping recipes |
| `crawler_get_recipe` | Get recipe YAML content |
| `crawler_create_recipe` | Create a new recipe |
| `crawler_run_recipe` | Start a recipe-based crawl |
| `crawler_run_full` | Start a full HTML crawl |
| `crawler_task_status` | Check crawl progress |
| `crawler_list_tasks` | List all crawl tasks |
| `crawler_login_open` | Open browser for manual login |
| `crawler_login_save` | Save login session cookies |
| `crawler_login_cancel` | Cancel login session |
| `crawler_login_status` | Check login session state |
| `crawler_login_sessions` | List saved login domains |
| `crawler_list_files` | Browse output files |
| `crawler_get_file` | Get file contents |
| `crawler_list_workflows` | List saved workflows |
| `crawler_run_workflow` | Run a saved workflow |
| `browser_open` | Open a persistent browser (loads saved cookies) |
| `browser_navigate` | Navigate to a URL |
| `browser_click` | Click by CSS selector or text |
| `browser_type` | Type into a form field |
| `browser_press_key` | Press a keyboard key |
| `browser_snapshot` | Read current page text |
| `browser_screenshot` | Take a screenshot |
| `browser_get_links` | Get all links on the page |
| `browser_scroll` | Scroll the page |
| `browser_evaluate` | Run JavaScript |
| `browser_close` | Close browser and save cookies |
| `browser_status` | Check if browser is active |
| `browser_record_start` | Start recording browser actions |
| `browser_record_stop` | Stop recording, return captured steps |
| `browser_record_save` | Save recorded steps as a replayable workflow |

## Agent Skills

The `skills/` directory contains instruction prompts that teach agents how to use the platform effectively. Feed these to your agent as system prompts or instructions.

| Skill | Description |
|-------|-------------|
| [browser-automation.md](skills/browser-automation.md) | Explore-record-replay pattern. Agent figures out a task, records the clean steps, saves as a workflow. Future runs skip AI entirely. |
| [recipe-builder.md](skills/recipe-builder.md) | Automatic recipe creation. Agent inspects a page's DOM, discovers CSS selectors, builds and tests a scraping recipe. |
| [site-login.md](skills/site-login.md) | Guided login flow. Agent opens the site, walks the user through VNC-based login, saves the session for future use. |

## API Authentication

Set `CRAWLER_API_KEY` to require authentication:

```bash
# In the systemd service or environment
Environment=CRAWLER_API_KEY=your-secret-key

# Or when starting manually
CRAWLER_API_KEY=your-secret-key python -m uvicorn api_server:app --host 0.0.0.0 --port 8080
```

When set, all API routes require an `X-API-Key` header. The dashboard and `/health` are always public.

## Installation (Manual)

```bash
pip install -r requirements.txt
playwright install

# For the API server
pip install -r requirements-api.txt
```

## Project Structure

```
crawler.py            # Core crawler (general + list modes)
crawler_config.py     # Default configuration
list_crawler.py       # List crawl mode engine
recipe_loader.py      # YAML recipe parser
validate_recipe.py    # Recipe schema validator
api_server.py         # FastAPI server (port 8080) — REST API + MCP endpoint
data_server.py        # FastAPI file server for output (port 8081)
mcp_server.py         # Standalone MCP bridge (stdio, for Claude Code)
browser_helper.py     # CLI for browser interactions
workflow_engine.py    # Workflow replay engine
workflow_models.py    # Workflow data models (Pydantic)
workflow_recorder.py  # Records agent MCP calls into replayable workflows
setup-crawler.sh      # One-shot container setup script
static/index.html     # Dashboard UI
recipes/              # YAML scraping recipes
output/               # Crawled data (JSONL, gitignored)
tests/                # Unit tests
```

## Quick Start

### General Crawling

```bash
# Crawl a single URL
python crawler.py --url https://example.com

# Crawl with depth and domain limits
python crawler.py --url https://example.com --max-depth 2 --domains example.com

# Crawl from a file of URLs
python crawler.py --file urls.txt

# Run headless
python crawler.py --url https://example.com --headless
```

### Recipe-Driven List Crawling

Create a YAML recipe, validate it, then run:

```bash
# Validate
python validate_recipe.py recipes/example_quotes.yaml

# Dry run (preview without saving)
python crawler.py --mode list --recipe recipes/example_quotes.yaml --dry-run

# Real run
python crawler.py --mode list --recipe recipes/example_quotes.yaml --headless
```

See [QUICK_START.md](QUICK_START.md) for a step-by-step guide and [LIST_CRAWL_GUIDE.md](LIST_CRAWL_GUIDE.md) for full recipe documentation.

## API Server

```bash
# Start the server
python -m uvicorn api_server:app --host 0.0.0.0 --port 8080

# Or via systemd
systemctl start crawler-api
```

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (includes `agent_available` status) |
| `POST` | `/mcp` | MCP JSON-RPC endpoint for remote agents |
| `POST` | `/api/crawl` | Start a recipe-based crawl |
| `POST` | `/api/crawl/full` | Start a full HTML crawl |
| `GET` | `/api/crawl/{id}` | Get crawl task status and logs |
| `GET/POST` | `/api/recipes` | List or create recipes |
| `POST` | `/api/login/open` | Open browser for manual login |
| `POST` | `/api/login/save` | Save session cookies |
| `GET` | `/api/login/sessions` | List saved login domains |
| `POST` | `/api/browser/open` | Open persistent browser session |
| `POST` | `/api/browser/navigate` | Navigate browser to URL |
| `POST` | `/api/browser/click` | Click element |
| `POST` | `/api/browser/snapshot` | Read page content as text |
| `POST` | `/api/browser/screenshot` | Take screenshot |
| `POST` | `/api/browser/close` | Close browser, save cookies |
| `GET` | `/api/files` | Browse output files |
| `GET/POST` | `/api/workflows` | List or save workflows |
| `POST` | `/api/workflows/{name}/run` | Run a saved workflow |

### Example

```bash
# Run a crawl
curl -X POST http://localhost:8080/api/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"recipe_path": "example_quotes.yaml", "headless": true}'

# Open browser for login
curl -X POST http://localhost:8080/api/login/open \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"url": "https://youtube.com", "label": "YouTube"}'
```

## Data Server

Serves the `output/` directory over HTTP with a browsable UI and JSON API.

```bash
systemctl start crawler-data
```

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/files` | List files as JSON |
| `GET` | `/api/files/{path}` | List subdirectory or download file |
| `GET` | `/browse/` | Browsable HTML directory listing |

## Recipe System

Recipes are YAML files that define how to scrape paginated list pages:

```yaml
start_urls:
  - "https://example.com/items"

list_scope_css: "div.item"
item_link_css: "a.item-link"

pagination:
  type: next              # next, all_links, or url_template
  next_css: "a.next"

limits:
  max_list_pages: 10
  max_items: 100

output:
  items_jsonl: "output/items.jsonl"
  pages_jsonl: "output/pages.jsonl"
```

## Workflow System

Record browser interactions as replayable workflows:

1. **Record** — An agent performs a task via Playwright, and the steps are captured as a replayable workflow
2. **Replay** — The workflow engine executes saved workflows with template interpolation (`{{input.field_name}}`), human-like delays, and anti-detection — no AI needed

## Configuration

Edit `crawler_config.py` for default settings:

```python
START_URLS = ["https://example.com"]
MAX_DEPTH = 2
ALLOWED_DOMAINS = ["example.com"]
HEADLESS = False
RATE_LIMIT_DELAY = 2.5
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CRAWLER_API_KEY` | *(none)* | API key for authentication (optional) |
| `AGENT_BIN` | `claude` | Path to agent CLI (only needed for `/task` endpoints) |
| `CRAWLER_WORKING_DIR` | `/opt/crawler` | Working directory |
| `CRAWLER_DATA_DIR` | `/opt/crawler/output` | Output directory |
| `CRAWLER_API_PORT` | `8080` | API server port |
| `CRAWLER_DATA_PORT` | `8081` | Data server port |
| `CRAWLER_VENV_PYTHON` | `sys.executable` | Python binary path |

## Authenticated Scraping

For sites requiring login (YouTube, Facebook, etc.):

1. Use the dashboard Login tab, or call `POST /api/login/open` with the site URL
2. Complete login via VNC (display `:99`) or the dashboard Browser View tab
3. Click "Save Session" or call `POST /api/login/save`
4. Cookies persist in `output/browser_session/cookies.json` — all future crawls and browser sessions use them automatically

## Testing

```bash
python -m unittest tests.test_extractors -v
python validate_recipe.py recipes/example_quotes.yaml
```

## Command Line Reference

```
General mode:
  --url URL                Single URL to crawl
  --urls URL [URL ...]     Multiple URLs
  --file FILE              File containing URLs
  --max-depth N            Maximum crawl depth
  --domains DOMAIN [...]   Allowed domains
  --output DIR             Output directory (default: crawled_pages)
  --headless / --visible   Browser visibility

List mode:
  --mode list              Enable list crawl mode
  --recipe FILE            YAML recipe file
  --dry-run                Preview without saving
  --force                  Ignore previous state
  --verbose-selectors      Log CSS selector match counts

Debug:
  --dump-html URL          Save page HTML to debug_dump.html
  --screenshot URL         Save page screenshot to debug_screenshot.png
```
