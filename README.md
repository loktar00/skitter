# Web Crawler

A flexible web crawling and browser automation platform with CloudFlare bypass, recipe-driven scraping, workflow replay, and HTTP API access.

## Features

- **General-purpose crawling** — BFS link discovery with depth control and domain filtering
- **Recipe-driven list crawling** — YAML-configured scraping of paginated list pages
- **CloudFlare bypass** — dual-engine approach using curl-cffi and Playwright
- **Anti-detection** — webdriver property override, randomized viewport, human-like delays
- **Browser automation tools** — CLI helper and workflow replay engine
- **HTTP API** — FastAPI server for remote task execution via AI agent
- **Data server** — file server with directory listing and JSON API for crawled output
- **Session persistence** — saved cookies for authenticated scraping
- **Resume capability** — state tracking to pick up interrupted crawls

## Installation

```bash
pip install -r requirements.txt
playwright install
```

For the API server:

```bash
pip install -r requirements-api.txt
```

## Project Structure

```
crawler.py            # Core crawler (general + list modes)
crawler_config.py     # Default configuration
list_crawler.py       # List crawl mode engine
recipe_loader.py      # YAML recipe parser
validate_recipe.py    # Recipe schema validator
api_server.py         # FastAPI task execution server (port 8080)
data_server.py        # FastAPI file server for output (port 8081)
browser_helper.py     # CLI for browser interactions
workflow_engine.py    # Workflow replay engine
workflow_models.py    # Workflow data models (Pydantic)
workflow_recorder.py  # Records agent MCP calls into replayable workflows
static/index.html     # Dashboard UI for the API server
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

### Browser Helper

Standalone CLI for browser interactions with anti-detection:

```bash
python browser_helper.py screenshot https://example.com /tmp/shot.png
python browser_helper.py dump-text https://example.com
python browser_helper.py dump-links https://example.com
python browser_helper.py click https://example.com "button.submit"
python browser_helper.py evaluate https://example.com "document.title"
```

## API Server

The API server wraps an AI agent behind an HTTP interface, allowing n8n workflows, other LLMs, or any HTTP client to send tasks.

```bash
# Start the server
python -m uvicorn api_server:app --host 0.0.0.0 --port 8080

# Or via systemd
systemctl start crawler-api
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/task` | Submit an agent task |
| `GET` | `/task/{id}` | Get task status and result |
| `GET` | `/tasks` | List recent tasks |
| `POST` | `/crawl` | Run a recipe-based crawl |
| `GET` | `/recipes` | List available recipes |
| `POST` | `/auth-prepare` | Launch visible browser for login |
| `GET` | `/` | Dashboard UI |

### Example

```bash
# Submit a task
curl -X POST http://localhost:8080/task \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List all YAML recipes in the recipes directory"}'

# Run a crawl
curl -X POST http://localhost:8080/crawl \
  -H "Content-Type: application/json" \
  -d '{"recipe": "example_quotes", "headless": true}'
```

## Data Server

Serves the `output/` directory over HTTP with a browsable UI and JSON API.

```bash
# Start the server
python data_server.py

# Or via systemd
systemctl start crawler-data
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/files` | List files as JSON |
| `GET` | `/api/files/{path}` | List subdirectory or download file |
| `GET` | `/browse/` | Browsable HTML directory listing |

## Workflow System

Record browser interactions as replayable workflows:

1. **Record** — The workflow recorder captures Playwright MCP tool calls from agent CLI stream-json output and converts them into step sequences.
2. **Replay** — The workflow engine executes saved workflows with template interpolation (`{{input.field_name}}`), human-like delays, and anti-detection.

Workflow files are JSON with a defined schema (see `workflow_models.py`).

## Recipe System

Recipes are YAML files that define how to scrape paginated list pages. They support:

- **Three pagination strategies**: next button, all page links, URL template
- **CSS selectors** for scoping items and extracting links
- **Configurable limits** for pages and items
- **Custom output paths** (JSONL format)

Example recipes are provided in `recipes/`.

## Configuration

Edit `crawler_config.py` for default settings:

```python
START_URLS = ["https://example.com"]
MAX_DEPTH = 2
ALLOWED_DOMAINS = ["example.com"]
HEADLESS = False
RATE_LIMIT_DELAY = 2.5
```

Environment variables for servers:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_BIN` | `claude` | Path to agent CLI binary |
| `CRAWLER_WORKING_DIR` | `/opt/crawler` | Working directory for tasks |
| `CRAWLER_DATA_DIR` | `/opt/crawler/output` | Directory served by data server |
| `CRAWLER_DATA_PORT` | `8081` | Data server port |

## Authenticated Scraping

For sites requiring login:

1. Start a visible browser session: `python crawler.py --mode list --recipe recipes/your_recipe.yaml --visible`
2. Complete login in the browser window (connect via VNC to display `:99`)
3. Cookies are saved to `output/browser_session/` for reuse in headless runs

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
