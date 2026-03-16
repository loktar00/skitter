FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    DISPLAY_NUM=99 \
    CRAWLER_API_PORT=8080 \
    CRAWLER_DATA_PORT=8081 \
    CRAWLER_WORKING_DIR=/opt/crawler \
    CRAWLER_DATA_DIR=/opt/crawler/output \
    MAX_DISPLAY_SESSIONS=8 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/crawler

# System dependencies: display stack, browser libs, supervisor
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    xvfb x11vnc fluxbox novnc websockify supervisor \
    # Chromium dependencies
    libgtk-3-0 libnotify-dev libnss3 libxss1 libasound2 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-api.txt

# Playwright browser
RUN python -m playwright install chromium \
    && python -m playwright install-deps chromium

# Application code
COPY . .

# Persistent data
RUN mkdir -p output workflows output/browser_session

# Supervisor config and entrypoint
COPY docker/supervisord.conf /etc/supervisor/conf.d/skitter.conf
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# API (8080), Data (8081), noVNC websocket (6080), VNC (5999)
EXPOSE 8080 8081 6080 5999

VOLUME ["/opt/crawler/output", "/opt/crawler/workflows"]

ENTRYPOINT ["/entrypoint.sh"]
