# Deployment Guide

Three ways to run this in production, plus a pre-launch checklist. All of
them serve the same Flask app through `wsgi.py` (waitress) instead of
`app.py`'s dev server.

## Pre-launch checklist

- [ ] Set `SECRET_KEY`, `BMY_ENCRYPTION_KEY`, `BMY_BLIND_INDEX_KEY` as
      environment variables (don't rely on the auto-generated
      `database/.keys/` files once you're running more than one process,
      or once you care about being able to redeploy without losing the
      ability to decrypt existing data)
- [ ] Run `python setup_admin.py` to create your first `superadmin` account,
      then create `admin`/`agent` accounts for the team from the dashboard's
      Team tab instead of sharing the superadmin login
- [ ] Put a reverse proxy in front for HTTPS - waitress only serves plain
      HTTP (see `deploy/nginx.conf.example`)
- [ ] Set `CORS_ORIGINS` to your actual site domain instead of the `*`
      default once the widget is embedded on a real site
- [ ] Confirm `database/`, `backups/`, and `uploads/` are on a volume/disk
      that's actually backed up at the infrastructure level too - the
      built-in daily backup (`scheduler.py`) protects against bad data/
      accidental deletion, not disk failure
- [ ] If running more than one app process/worker, set `BMY_REDIS_URL` so
      rate limiting is shared across processes (see `rate_limit.py`) -
      without it, each process has its own independent counter

## Option 1: Windows (matches local dev)

```powershell
pip install -r requirements.txt
python setup_admin.py
python wsgi.py
```

To keep it running as a background service instead of a terminal window,
use [NSSM](https://nssm.cc/) (Non-Sucking Service Manager):

```powershell
nssm install BMyMarketerChatbot "C:\path\to\venv\Scripts\python.exe" "C:\path\to\BMY-Marketer-Chatbot\wsgi.py"
nssm set BMyMarketerChatbot AppDirectory "C:\path\to\BMY-Marketer-Chatbot"
nssm set BMyMarketerChatbot AppEnvironmentExtra SECRET_KEY=your-key BMY_ENCRYPTION_KEY=your-key BMY_BLIND_INDEX_KEY=your-key
nssm start BMyMarketerChatbot
```

Put IIS or Caddy in front for HTTPS (IIS's Application Request Routing
module, or Caddy's automatic HTTPS, both work as a reverse proxy to
`127.0.0.1:5000`).

## Option 2: Linux (systemd + nginx)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup_admin.py
```

Then install the systemd service and nginx reverse proxy:

```bash
sudo cp deploy/bmy-chatbot.service /etc/systemd/system/
# edit the file first - set WorkingDirectory, User, and the three keys
sudo systemctl daemon-reload
sudo systemctl enable --now bmy-chatbot

sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/bmy-chatbot
# edit server_name, then:
sudo ln -s /etc/nginx/sites-available/bmy-chatbot /etc/nginx/sites-enabled/
sudo certbot --nginx -d chat.yourdomain.com   # issues + wires up the TLS cert
sudo systemctl reload nginx
```

## Option 3: Docker

```bash
docker compose up -d --build
docker exec -it bmy-marketer-chatbot-bmy-chatbot-1 python setup_admin.py
```

`docker-compose.yml` mounts `./database`, `./uploads`, and `./backups` as
volumes so data (and the auto-generated encryption keys) survive container
rebuilds. Set the three key env vars in `docker-compose.yml` for anything
beyond local testing. Put nginx (or a managed load balancer) in front of
the container for HTTPS, same as the bare-metal Linux option.

## Scaling beyond one process

Everything in this project defaults to single-process assumptions (the
in-memory rate limiter, and SQLite itself). If you need multiple app
workers:

1. Set `BMY_REDIS_URL` so rate limiting is shared (`rate_limit.py` falls
   back to in-memory automatically if Redis isn't reachable, so this is
   safe to leave unset until you actually need it).
2. SQLite handles moderate concurrent load fine for a support chatbot, but
   if you outgrow it, `database/db.py` is the only place that talks to the
   database - swapping the storage engine means changes there, not
   throughout the app.
