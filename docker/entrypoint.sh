#!/bin/bash
set -e

# Pass API key to supervisor environment if set
if [ -n "$CRAWLER_API_KEY" ]; then
    export CRAWLER_API_KEY
fi

# Ensure data dirs exist
mkdir -p /opt/crawler/output /opt/crawler/workflows /opt/crawler/output/browser_session

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/skitter.conf
