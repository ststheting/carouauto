# Setup

## 1. Provision the VPS

- Spin up a 2GB RAM / 1-2 vCPU Ubuntu LTS VPS (e.g. Hetzner CX22).
- Copy this repo to `/opt/carouauto` on the VPS (e.g. `git clone` or `scp -r`).
- Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` (see step 2).
- Edit `config.yaml` to list the searches you want tracked.
- Run: `sudo bash /opt/carouauto/deploy/provision.sh`

## 2. Create your Telegram bot

1. Message **@BotFather** on Telegram, run `/newbot`, follow the prompts.
2. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Run `python scripts/get_chat_id.py`, message your bot when prompted,
   then paste the printed chat ID into `.env` as `TELEGRAM_CHAT_ID`.
4. Restart the service: `sudo systemctl restart carouauto`.

## 3. Check it's running

```bash
sudo systemctl status carouauto xvfb x11vnc novnc
sudo journalctl -u carouauto -f
```

## 4. Solving a Cloudflare challenge manually

If Carousell shows a Cloudflare challenge that the automated poller can't
clear, you'll get a Telegram message with these instructions:

```bash
ssh -L 6080:localhost:6080 <your-user>@<vps-ip>
```

Then open `http://localhost:6080/vnc.html` in your own browser, solve the
challenge in the browser window shown, and polling resumes automatically
— no restart needed.
