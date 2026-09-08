# Setup

## 1. Create your Telegram bot

Do this first — the service won't start without a working token and chat ID.

1. Message **@BotFather** on Telegram, run `/newbot`, follow the prompts.
2. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Get your chat ID with `scripts/get_chat_id.py`, message your bot when
   prompted, then paste the printed ID into `.env` as `TELEGRAM_CHAT_ID`.

   You can run it either way:

   - **From your own machine, before deploying** (simplest — the script only
     needs `httpx` and `python-dotenv`, no Playwright and no VPS):
     `python scripts/get_chat_id.py`
   - **On the VPS, after provisioning:**
     `sudo -u carouauto /opt/carouauto/.venv/bin/python scripts/get_chat_id.py`

## 2. Provision the VPS

- Spin up a 2GB RAM / 1-2 vCPU **Ubuntu 24.04 LTS or newer** VPS (e.g. Hetzner
  CX22). The provisioning script uses the distro's system Python, which must be
  3.11 or newer.
- Copy this repo to `/opt/carouauto` on the VPS (e.g. `git clone` or `scp -r`).
- Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` from step 1.
- Edit `config.yaml` to list the searches you want tracked.
- Run: `sudo bash /opt/carouauto/deploy/provision.sh`

This installs everything and starts the support services (Xvfb, x11vnc, noVNC).
It enables `carouauto` but deliberately does **not** start it yet.

## 3. Start the monitor

With `.env` complete:

```bash
sudo systemctl start carouauto
```

## 4. Check it's running

```bash
sudo systemctl status carouauto xvfb x11vnc novnc
sudo journalctl -u carouauto -f
```

You should see one log line per search per poll cycle.

## 5. Solving a Cloudflare challenge manually

If Carousell shows a Cloudflare challenge that the automated poller can't
clear, you'll get a Telegram message with these instructions:

```bash
ssh -L 6080:localhost:6080 <your-user>@<vps-ip>
```

Then open `http://localhost:6080/vnc.html` in your own browser, solve the
challenge in the browser window shown, and polling resumes automatically
— no restart needed.
