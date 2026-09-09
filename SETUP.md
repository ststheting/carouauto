# Setup

## 1. Create your Telegram bot and pick a registration password

Do this first — the service won't start without these.

1. Message **@BotFather** on Telegram, run `/newbot`, follow the prompts.
2. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Pick a shared password for anyone you want to give access to, and put
   it in `.env` as `REGISTRATION_PASSWORD`. Anyone who knows it can
   register with the bot and track their own searches.
4. Get your own chat ID with `scripts/get_chat_id.py` (this makes you the
   admin — the only one who can `/revoke` others or run `/backup`), then
   paste it into `.env` as `TELEGRAM_CHAT_ID`.

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
- Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, and `REGISTRATION_PASSWORD` from step 1.
- Run: `sudo bash /opt/carouauto/deploy/provision.sh`

This installs everything and starts the support services (Xvfb, x11vnc, noVNC).
It enables `carouauto` but deliberately does **not** start it yet.

## 3. Start the monitor

With `.env` complete:

```bash
sudo systemctl start carouauto
```

## 4. Register and add your first search

Message your bot on Telegram:

```
/register <the password from step 1>
/add speediance
```

`/add <name>` searches Carousell for `<name>` itself — for a different
search term than the name you want to use, say `/add fitnessmachine
speediance`. If you want to track an exact Carousell URL (e.g. one with
filters applied via Carousell's own search UI) instead, use `/addurl
<name> <url>`.

Send `/help` for the full command list, or `/searches` to see what
you're tracking. Anyone else you give the password to does the same —
each person's searches and price/keyword/condition filters are their
own.

## 5. Check it's running

```bash
sudo systemctl status carouauto xvfb x11vnc novnc
sudo journalctl -u carouauto -f
```

You should see one log line per tracked search per poll cycle.

## 6. Solving a Cloudflare challenge manually

If Carousell shows a Cloudflare challenge that the automated poller
can't clear, the **admin** (the `TELEGRAM_CHAT_ID` from step 1) gets a
Telegram message with these instructions — regular users don't, since
only the admin has VPS access to act on it:

```bash
ssh -L 6080:localhost:6080 <your-user>@<vps-ip>
```

Then open `http://localhost:6080/vnc.html` in your own browser, solve the
challenge in the browser window shown, and polling resumes automatically
— no restart needed.

## 7. Backing up before a provider migration

As the admin, message the bot `/backup` — it sends the current database
(all registered users, their searches, and filters) back to you as a
Telegram file. On the new VPS, after cloning the repo and before starting
the service, drop that file in as `carouauto.sqlite3`.
