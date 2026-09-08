#!/usr/bin/env bash
set -euo pipefail

# Run as root on a fresh Ubuntu 24.04 LTS (or newer) VPS, after the repo has
# been placed at /opt/carouauto. See SETUP.md.

apt-get update
# Ubuntu 24.04 ships python3.12, which satisfies the project's >=3.11 floor.
# websockify is required by noVNC's novnc_proxy (see deploy/novnc.service).
apt-get install -y python3 python3-venv xvfb x11vnc git websockify curl gnupg

# Install Google Chrome (the real browser channel="chrome" expects to find on
# PATH). This needs root, so it happens here rather than via Playwright's
# --with-deps under an unprivileged sudo -u.
if ! command -v google-chrome-stable &>/dev/null; then
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
  apt-get update
  apt-get install -y google-chrome-stable
fi

if [ ! -d /opt/novnc ]; then
  git clone https://github.com/novnc/noVNC.git /opt/novnc
fi

id -u carouauto &>/dev/null || useradd -r -m -d /opt/carouauto -s /usr/sbin/nologin carouauto

# useradd -m does not chown a pre-existing home, and the repo was placed here
# as root — so every sudo -u carouauto step below would fail without this.
chown -R carouauto:carouauto /opt/carouauto

sudo -u carouauto python3 -m venv /opt/carouauto/.venv
sudo -u carouauto /opt/carouauto/.venv/bin/pip install -e "/opt/carouauto[dev]"
# Chrome is already installed system-wide above; this just registers/verifies it
# for Playwright, so it needs no root and no --with-deps.
sudo -u carouauto /opt/carouauto/.venv/bin/playwright install chrome

cp /opt/carouauto/deploy/*.service /etc/systemd/system/
systemctl daemon-reload

# Start the support services now, but only ENABLE carouauto: it will crash-loop
# until .env has a real bot token and chat ID. SETUP.md's final step starts it.
systemctl enable --now xvfb x11vnc novnc
systemctl enable carouauto

echo
echo "Provisioning done. Finish the Telegram bot setup (SETUP.md step 2), then:"
echo "  sudo systemctl start carouauto"
