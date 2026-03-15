#!/bin/bash
# =============================================================================
# Crawler Setup Script
#
# Run this directly on your container to install everything from scratch.
# Safe to re-run — it will pull the latest code and restart services.
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/loktar00/crawler/main/setup-crawler.sh | bash
#   # or if already on the machine:
#   bash setup-crawler.sh
# =============================================================================

set -e

CRAWLER_DIR="${CRAWLER_DIR:-/opt/crawler}"
REPO_URL="${CRAWLER_REPO:-https://github.com/loktar00/crawler.git}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
API_PORT="${CRAWLER_API_PORT:-8080}"
DATA_PORT="${CRAWLER_DATA_PORT:-8081}"

# Get container IP for final output
CONTAINER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "============================================"
echo "  Crawler Setup"
echo "============================================"
echo ""
echo "  Install dir:  $CRAWLER_DIR"
echo "  API port:     $API_PORT"
echo "  Data port:    $DATA_PORT"
echo "  Container IP: ${CONTAINER_IP:-unknown}"
echo ""
echo "--------------------------------------------"
echo ""

# Stop existing services if re-running
if systemctl is-active --quiet crawler-api 2>/dev/null; then
    echo "Stopping existing crawler services..."
    systemctl stop crawler-api crawler-data 2>/dev/null || true
fi
# Clean up old claude-named services if they exist
if [ -f /etc/systemd/system/claude-api.service ]; then
    echo "Removing old claude-api/claude-data services..."
    systemctl stop claude-api claude-data 2>/dev/null || true
    systemctl disable claude-api claude-data 2>/dev/null || true
    rm -f /etc/systemd/system/claude-api.service /etc/systemd/system/claude-data.service
fi

# 1. System packages
echo "[1/9] Installing system packages..."
apt update -qq
apt install -y -qq \
    python3.11 python3.11-venv python3-pip git curl \
    xvfb x11vnc fluxbox \
    libgtk-3-0 libnotify-dev libgconf-2-4 libnss3 libxss1 libasound2 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 fonts-liberation \
    > /dev/null 2>&1
echo "  Done."

# 2. Clone or update repository
echo "[2/9] Setting up repository..."
if [ -d "$CRAWLER_DIR/.git" ]; then
    cd "$CRAWLER_DIR"
    git fetch origin
    git reset --hard origin/main
    echo "  Updated to latest."
else
    git clone "$REPO_URL" "$CRAWLER_DIR"
    cd "$CRAWLER_DIR"
    git branch --set-upstream-to=origin/main main
    echo "  Cloned fresh."
fi

# 3. Python virtual environment
echo "[3/9] Setting up Python venv..."
python3.11 -m venv venv
source venv/bin/activate

# 4. Python dependencies
echo "[4/9] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
if [ -f requirements-api.txt ]; then
    pip install -r requirements-api.txt -q
fi
echo "  Done."

# 5. Playwright browser
echo "[5/9] Installing Playwright Chromium..."
python -m playwright install chromium > /dev/null 2>&1
echo "  Done."

# 6. Create directories
echo "[6/9] Creating directories..."
mkdir -p output
mkdir -p workflows

# 7. Xvfb service (virtual display)
echo "[7/9] Configuring Xvfb service..."
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

# 8. Crawler API service
echo "[8/9] Configuring crawler-api service..."
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

# 9. Crawler Data service
echo "[9/9] Configuring crawler-data service..."
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

# Set DISPLAY globally
grep -q '^DISPLAY=' /etc/environment 2>/dev/null || echo "DISPLAY=:${DISPLAY_NUM}" >> /etc/environment
echo "export DISPLAY=:${DISPLAY_NUM}" > /etc/profile.d/display.sh

# Start everything
echo ""
echo "Starting services..."
systemctl daemon-reload
systemctl enable xvfb crawler-api crawler-data --quiet
systemctl start xvfb
sleep 1
systemctl start crawler-api
systemctl start crawler-data
sleep 2

# Verify
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""

# Check each service
for svc in xvfb crawler-api crawler-data; do
    if systemctl is-active --quiet "$svc"; then
        echo "  [OK]  $svc"
    else
        echo "  [FAIL] $svc — run: journalctl -u $svc --no-pager -n 20"
    fi
done

echo ""
echo "--------------------------------------------"
echo ""
echo "  Dashboard:  http://${CONTAINER_IP:-<your-ip>}:${API_PORT}/dashboard/"
echo "  Data files: http://${CONTAINER_IP:-<your-ip>}:${DATA_PORT}/"
echo ""
echo "  Useful commands:"
echo "    systemctl status crawler-api"
echo "    systemctl restart crawler-api"
echo "    journalctl -u crawler-api -f"
echo ""
echo "  Quick test:"
echo "    cd $CRAWLER_DIR && source venv/bin/activate"
echo "    DISPLAY=:${DISPLAY_NUM} python crawler.py --mode list --recipe recipes/example_quotes.yaml --dry-run"
echo ""
