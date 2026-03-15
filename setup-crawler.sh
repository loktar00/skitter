#!/bin/bash
# Crawler Container Setup Script
# Run inside a Proxmox LXC container (or any Debian/Ubuntu server):
#   scp setup-crawler.sh root@<container-ip>:/root/
#   ssh root@<container-ip> 'bash /root/setup-crawler.sh'

set -e

CRAWLER_DIR="${CRAWLER_DIR:-/opt/crawler}"
REPO_URL="${CRAWLER_REPO:-https://github.com/loktar00/crawler.git}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
API_PORT="${CRAWLER_API_PORT:-8080}"
DATA_PORT="${CRAWLER_DATA_PORT:-8081}"

echo "=== Crawler Container Setup ==="
echo "Install dir: $CRAWLER_DIR"
echo ""

# 1. System updates
echo "[1/12] System updates..."
apt update && apt upgrade -y

# 2. Install Python 3.11+
echo "[2/12] Installing Python 3.11..."
apt install -y python3.11 python3.11-venv python3-pip git curl

# 3. Install Playwright browser dependencies
echo "[3/12] Installing browser dependencies..."
apt install -y \
    libgtk-3-0 \
    libnotify-dev \
    libgconf-2-4 \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation

# 4. Install Xvfb for virtual display
echo "[4/12] Installing Xvfb and VNC..."
apt install -y xvfb x11vnc fluxbox

# 5. Clone or update the crawler repository
echo "[5/12] Setting up crawler at $CRAWLER_DIR..."
if [ -d "$CRAWLER_DIR" ]; then
    echo "  $CRAWLER_DIR already exists, pulling latest..."
    cd "$CRAWLER_DIR" && git pull origin main
else
    git clone "$REPO_URL" "$CRAWLER_DIR"
    cd "$CRAWLER_DIR"
fi

# 6. Create virtual environment
echo "[6/12] Creating Python venv..."
python3.11 -m venv venv
source venv/bin/activate

# 7. Install Python dependencies
echo "[7/12] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
if [ -f requirements-api.txt ]; then
    pip install -r requirements-api.txt
fi

# 8. Install Playwright browsers
echo "[8/12] Installing Playwright Chromium..."
python -m playwright install chromium

# 9. Create output directories
echo "[9/12] Creating output directories..."
mkdir -p output

# 10. Set up Xvfb as a systemd service
echo "[10/12] Configuring Xvfb service..."
cat > /etc/systemd/system/xvfb.service << EOF
[Unit]
Description=X Virtual Frame Buffer
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 11. Set up crawler-api as a systemd service
echo "[11/12] Configuring crawler-api service..."
cat > /etc/systemd/system/crawler-api.service << EOF
[Unit]
Description=Crawler API Server
After=network.target xvfb.service
Requires=xvfb.service

[Service]
Type=simple
WorkingDirectory=${CRAWLER_DIR}
Environment=DISPLAY=:${DISPLAY_NUM}
Environment=CRAWLER_WORKING_DIR=${CRAWLER_DIR}
Environment=CRAWLER_DATA_DIR=${CRAWLER_DIR}/output
Environment=CRAWLER_VENV_PYTHON=${CRAWLER_DIR}/venv/bin/python
Environment=CRAWLER_API_PORT=${API_PORT}
ExecStart=${CRAWLER_DIR}/venv/bin/python -m uvicorn api_server:app --host 0.0.0.0 --port ${API_PORT}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 12. Set up crawler-data as a systemd service
echo "[12/12] Configuring crawler-data service..."
cat > /etc/systemd/system/crawler-data.service << EOF
[Unit]
Description=Crawler Data File Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${CRAWLER_DIR}
Environment=CRAWLER_DATA_DIR=${CRAWLER_DIR}/output
Environment=CRAWLER_DATA_PORT=${DATA_PORT}
ExecStart=${CRAWLER_DIR}/venv/bin/python data_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start all services
systemctl daemon-reload
systemctl enable xvfb crawler-api crawler-data
systemctl start xvfb
systemctl start crawler-api
systemctl start crawler-data

# Set DISPLAY environment variable globally
grep -q '^DISPLAY=' /etc/environment || echo "DISPLAY=:${DISPLAY_NUM}" >> /etc/environment
echo "export DISPLAY=:${DISPLAY_NUM}" > /etc/profile.d/display.sh

echo ""
echo "=== Setup Complete ==="
echo "Crawler installed at $CRAWLER_DIR"
echo "Virtual display running on :${DISPLAY_NUM}"
echo ""
echo "Services:"
echo "  xvfb          - Virtual display (:${DISPLAY_NUM})"
echo "  crawler-api   - API server on port ${API_PORT}"
echo "  crawler-data  - Data server on port ${DATA_PORT}"
echo ""
echo "Commands:"
echo "  systemctl status crawler-api     # check API server"
echo "  systemctl restart crawler-api    # restart after code changes"
echo "  journalctl -u crawler-api -f     # tail API logs"
echo ""
echo "Dashboard: http://<this-ip>:${API_PORT}/dashboard/"
echo ""
echo "Quick test:"
echo "  cd $CRAWLER_DIR && source venv/bin/activate"
echo "  DISPLAY=:${DISPLAY_NUM} python crawler.py --mode list --recipe recipes/example_quotes.yaml --dry-run"
