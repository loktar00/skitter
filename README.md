# Web Crawler

A browser-as-a-service platform for web crawling, scraping, and browser automation. Control it from any AI agent via MCP, CLI, or REST API.

<img width="1654" height="678" alt="image" src="https://github.com/user-attachments/assets/879d8227-caa7-4172-8f7b-0fc9d3bc140c" />


## Quick Start

### 1. Install

Run on a fresh Debian/Ubuntu container (or any server):

```bash
curl -sL https://raw.githubusercontent.com/loktar00/crawler/main/setup-crawler.sh | bash
```

This installs everything, sets up systemd services (`xvfb`, `x11vnc`, `websockify`, `crawler-api`, `crawler-data`), and prints your dashboard URL.

### 2. Connect an Agent

Pick your method — all three hit the same API:

**MCP (Hermes, or any remote MCP agent)** — just a URL, nothing to install:

```yaml
# Add to your agent's MCP config
mcp_servers:
  crawler:
    url: "http://<container-ip>:8080/mcp"
    headers:
      X-API-Key: "<your-key>"
```

**MCP (Claude Code)** — single-file stdio bridge:

```bash
claude mcp add crawler -- python /path/to/mcp_server.py \
    --api-url http://<container-ip>:8080 --api-key <your-key>
```

**CLI (any agent with shell access, or manual use):**

```bash
export CRAWLER_API_URL=http://<container-ip>:8080
export CRAWLER_API_KEY=<your-key>
python crawler_cli.py browser open
python crawler_cli.py browser navigate https://youtube.com
python crawler_cli.py browser snapshot
```

Run `python crawler_cli.py` with no args to see all available commands.

### 3. Set Up Auth (Optional)

```bash
# Edit the service to add an API key
nano /etc/systemd/system/crawler-api.service
# Add: Environment=CRAWLER_API_KEY=your-secret-key
systemctl daemon-reload && systemctl restart crawler-api
```

When set, API routes require `X-API-Key` header. Dashboard and `/health` are always public.

## What It Does

| Capability | How | AI Required? |
|------------|-----|:---:|
| Crawl websites (BFS with depth/domain control) | `crawler_run_full` | No |
| Scrape paginated lists via YAML recipes | `crawler_run_recipe` | No |
| Drive a live browser (navigate, click, type, read) | `browser_*` tools | No |
| Manage tabs (open, close, switch, list) | `browser_tab_*` tools | No |
| Record browser actions as replayable workflows | `browser_record_*` tools | Once |
| Replay saved workflows | `crawler_run_workflow` | No |
| Log into sites (YouTube, Facebook, etc.) | `crawler_login_*` tools | No |
| Build scraping recipes by inspecting pages | Agent + `browser_*` tools | Once |

## Available Tools

All tools are available via MCP, CLI, and REST API.

### Crawling

| Tool | Description |
|------|-------------|
| `crawler_run_recipe` | Start a recipe-based list crawl |
| `crawler_run_full` | Start a full HTML crawl from URLs |
| `crawler_task_status` | Check crawl progress and logs |
| `crawler_list_tasks` | List all crawl tasks |

### Recipes

| Tool | Description |
|------|-------------|
| `crawler_list_recipes` | List all scraping recipes |
| `crawler_get_recipe` | Get recipe YAML content |
| `crawler_create_recipe` | Create a new recipe |

### Browser

| Tool | Description |
|------|-------------|
| `browser_open` | Open browser session (loads saved cookies) |
| `browser_close` | Close browser, save cookies |
| `browser_status` | Check browser state and recording status |
| `browser_navigate` | Navigate to a URL |
| `browser_click` | Click by CSS selector or visible text |
| `browser_type` | Type into a form field |
| `browser_press_key` | Press a keyboard key (Enter, Tab, etc.) |
| `browser_snapshot` | Read current page as text |
| `browser_screenshot` | Take a screenshot (base64 PNG) |
| `browser_get_links` | Get all links on the page |
| `browser_scroll` | Scroll up or down |
| `browser_evaluate` | Run JavaScript, return result |

### Tabs

| Tool | Description |
|------|-------------|
| `browser_tab_open` | Open a new tab (optionally at a URL) |
| `browser_tab_close` | Close current tab |
| `browser_tab_list` | List all tabs with URL, title, active state |
| `browser_tab_switch` | Switch to a tab by index |

### Recording

| Tool | Description |
|------|-------------|
| `browser_record_start` | Start recording browser actions |
| `browser_record_stop` | Stop recording, return captured steps |
| `browser_record_save` | Save as a replayable workflow |

### Login & Sessions

| Tool | Description |
|------|-------------|
| `crawler_login_open` | Open browser for manual login |
| `crawler_login_save` | Save login session cookies |
| `crawler_login_cancel` | Cancel login session |
| `crawler_login_status` | Check login session state |
| `crawler_login_sessions` | List saved login domains |

### Files & Workflows

| Tool | Description |
|------|-------------|
| `crawler_list_files` | Browse output files |
| `crawler_get_file` | Get file contents |
| `crawler_list_workflows` | List saved workflows |
| `crawler_run_workflow` | Run a saved workflow |
| `crawler_health` | Check API server status |

## Agent Skills

The `skills/` directory contains instruction prompts that teach agents how to use the platform. Feed these to your agent as system prompts.

| Skill | What it teaches |
|-------|-----------------|
| [browser-automation](skills/browser-automation.md) | **Explore-record-replay**: figure out a task, record the clean steps, save as a workflow. Next time it runs instantly without AI. |
| [recipe-builder](skills/recipe-builder.md) | **Auto-create recipes**: inspect a page's DOM, discover CSS selectors, build and test a scraping recipe. |
| [site-login](skills/site-login.md) | **Login flow**: open a site, guide the user through VNC login, save cookies for future use. |

## Multi-VNC: Concurrent Agent Monitoring

Multiple agents can run simultaneously, each on an isolated display with real-time VNC monitoring from the dashboard.

- Non-headless crawls, agent tasks, and workflow recordings each get a dedicated Xvfb + x11vnc + websockify stack
- Dashboard shows a display selector to switch between active sessions
- Running tasks show a "VNC" button to watch them live
- Displays are automatically allocated and freed as tasks start/complete
- Up to 8 concurrent displays (configurable via `MAX_DISPLAY_SESSIONS`)

| Display | VNC Port | WebSocket Port | Notes |
|---------|----------|----------------|-------|
| `:99`   | 5999     | 6080           | Default (systemd-managed) |
| `:100`  | 6000     | 6081           | Auto-allocated per task |
| `:101`  | 6001     | 6082           | Auto-allocated per task |
| ...     | ...      | ...            | Up to `:106` / 6087 |

## Architecture

```
┌─────────────────────────────────────────────────┐
│  AI Agent (Claude, Hermes, any LLM)             │
│  connects via MCP, CLI, or REST                 │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Crawler API Server (port 8080)                 │
│  ┌──────────┬───────────┬────────────────────┐  │
│  │ REST API │ MCP /mcp  │ Dashboard UI       │  │
│  └──────────┴───────────┴────────────────────┘  │
│  ┌──────────────────────────────────────────┐   │
│  │ Display Manager (multi-VNC)              │   │
│  │ • Allocate/free Xvfb+x11vnc+websockify  │   │
│  │ • Per-task display isolation             │   │
│  │ • Zombie cleanup & graceful shutdown     │   │
│  └──────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────┐   │
│  │ Browser Session (Playwright + Chromium)  │   │
│  │ • Anti-detection  • Cookie persistence   │   │
│  │ • Tab management  • Action recording     │   │
│  └──────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────┐   │
│  │ Crawl Engine    │ Workflow Engine        │   │
│  │ • Full HTML     │ • Record from agent    │   │
│  │ • Recipe-based  │ • Replay without AI    │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Data Server (port 8081)                        │
│  Serves output/ via JSON API + browsable HTML   │
└─────────────────────────────────────────────────┘
```

## Project Structure

```
api_server.py         # FastAPI server — REST API, MCP endpoint, browser session
display_manager.py    # Multi-VNC display manager (Xvfb/x11vnc/websockify lifecycle)
mcp_server.py         # Standalone MCP bridge (stdio, for Claude Code)
crawler_cli.py        # CLI tool (shell-based agents, scripting, manual use)
crawler.py            # Core crawler (general + list modes)
list_crawler.py       # Recipe-driven list crawl engine
recipe_loader.py      # YAML recipe parser
workflow_engine.py    # Replay workflows via Playwright (no AI)
workflow_recorder.py  # Parse agent actions into workflow steps
setup-crawler.sh      # One-shot container setup
static/index.html     # Dashboard UI
skills/               # Agent instruction prompts
recipes/              # YAML scraping recipes
output/               # Crawled data (gitignored)
```

## Recipe Format

```yaml
start_urls:
  - "https://example.com/items"

list_scope_css: "div.item"          # Repeated item container
item_link_css: "a.item-link"        # Link within each item

pagination:
  type: next                        # next, all_links, or url_template
  next_css: "a.next"

limits:
  max_list_pages: 10
  max_items: 100

output:
  items_jsonl: "output/items.jsonl"
  pages_jsonl: "output/pages.jsonl"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CRAWLER_API_KEY` | *(none)* | API key for authentication |
| `CRAWLER_API_PORT` | `8080` | API server port |
| `CRAWLER_DATA_PORT` | `8081` | Data server port |
| `CRAWLER_WORKING_DIR` | `/opt/crawler` | Working directory |
| `CRAWLER_DATA_DIR` | `/opt/crawler/output` | Output directory |
| `CRAWLER_VENV_PYTHON` | `sys.executable` | Python binary path |
| `AGENT_BIN` | `claude` | Agent CLI (only for `/task` endpoints) |
| `MAX_DISPLAY_SESSIONS` | `8` | Max concurrent VNC displays |
| `DISPLAY_NUM` | `99` | Default X11 display number |

## CLI Reference

```
python crawler_cli.py                 # Show all commands
python crawler_cli.py health          # Check server status
python crawler_cli.py recipes list    # List recipes
python crawler_cli.py crawl run <recipe>
python crawler_cli.py browser open    # Start browser session
python crawler_cli.py browser navigate <url>
python crawler_cli.py browser snapshot
python crawler_cli.py browser click --text "Click me"
python crawler_cli.py browser tab open <url>
python crawler_cli.py browser tab list
python crawler_cli.py browser record start
python crawler_cli.py browser record save <name>
python crawler_cli.py workflows run <name>
python crawler_cli.py login open <url> --label "Site"
python crawler_cli.py login save
python crawler_cli.py files list
```

## Crawler CLI Reference

```
General mode:
  --url URL                Single URL to crawl
  --urls URL [URL ...]     Multiple URLs
  --file FILE              File containing URLs
  --max-depth N            Maximum crawl depth
  --domains DOMAIN [...]   Allowed domains
  --headless / --visible   Browser visibility

List mode:
  --mode list --recipe FILE    YAML recipe file
  --dry-run                    Preview without saving
  --force                      Ignore previous state
  --verbose-selectors          Log CSS selector match counts

Debug:
  --dump-html URL          Save page HTML to debug_dump.html
  --screenshot URL         Save screenshot to debug_screenshot.png
```
