"""
Configuration settings for generic web crawler.
"""

import os

# Output directory - configurable via env var for API/data server integration
# If CRAWLER_DATA_DIR is set, output goes there instead of ./output/
OUTPUT_DIR = os.environ.get("CRAWLER_DATA_DIR", "output")

# Starting URLs - list of URLs to begin crawling from
# Can be empty if URLs are provided via command line
START_URLS = [
    "https://stackoverflow.com/users/322395/loktar?tab=answers",
]

# Maximum crawl depth (None = unlimited)
# Depth 0 = only start URLs
# Depth 1 = start URLs + links found on start URLs
# Depth 2 = depth 1 + links found on those pages, etc.
MAX_DEPTH = 2

# Allowed domains - restrict crawling to these domains (None or empty list = no restriction)
# Example: ["example.com", "blog.example.com"]
# Will match exact domain or subdomains
ALLOWED_DOMAINS = []

# Browser headless mode
# False = browser window visible (useful for debugging CloudFlare challenges)
# True = browser runs in background (faster, less resource intensive)
HEADLESS = False

# Rate limiting: delay in seconds between requests
# Increase this if you're getting blocked or want to be more polite
RATE_LIMIT_DELAY = 2.5

# Number of retries for failed requests
MAX_RETRIES = 3

# Retry delay in seconds
RETRY_DELAY = 5
