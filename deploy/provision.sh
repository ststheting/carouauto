#!/usr/bin/env bash
set -euo pipefail

apt-get update
apt-get install -y python3.11 python3.11-venv xvfb x11vnc git

if [ ! -d /opt/novnc ]; then
  git clone https://github.com/novnc/noVNC.git /opt/novnc
fi

id -u carouauto &>/dev/null || useradd -r -m -d /opt/carouauto -s /usr/sbin/nologin carouauto

sudo -u carouauto python3.11 -m venv /opt/carouauto/.venv
sudo -u carouauto /opt/carouauto/.venv/bin/pip install -e "/opt/carouauto[dev]"
sudo -u carouauto /opt/carouauto/.venv/bin/playwright install --with-deps chrome

cp /opt/carouauto/deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xvfb x11vnc novnc carouauto
