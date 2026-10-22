# ofmhelpers — server runbook

Takes a freshly rented VPS to a running, HTTPS-served ofmhelpers, and keeps it
deployed. Any provider with root on a plain Ubuntu box works (production runs
on one). Everything app-side runs in Docker; the host only has Docker, nginx,
certbot and ufw.

Placeholders below: `203.0.113.10` = your server's IP, `example.com` = your
domain, `ubuntu` = the sudo user your provider gives you.

---

## 1. Rent the server

- **OS:** Ubuntu 24.04 LTS (what production and CI run).
- **Size:** 4 vCPU / 8 GB RAM / 80 GB disk. The RQ worker runs 10 jobs in
  parallel (video downloads, ffmpeg, exports); less RAM works with a smaller
  `OFM_RQ_WORKERS`.
- **Login:** add your SSH public key in the provider's panel at order time.
- Note how to reach the provider's **web console / KVM**: it is the way back in
  if you lock yourself out of SSH.

## 2. DNS

At your registrar, two A records to the server's IP:

| Name | Type | Value |
|---|---|---|
| `example.com` | A | `203.0.113.10` |
| `www.example.com` | A | `203.0.113.10` |

Certbot (step 8) needs both to resolve, so do this first. Check with
`dig +short example.com`.

## 3. SSH: keys only

```bash
ssh ubuntu@203.0.113.10
sudo apt update && sudo apt -y upgrade
```

Turn password and root login off:

```bash
sudo tee /etc/ssh/sshd_config.d/01-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

The file name must sort **before** cloud-init's `50-cloud-init.conf`: sshd keeps
the first value it reads for each option, and cloud-init's file turns
password login back on. Test a new SSH session **before** closing the
current one.

## 4. Firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Docker writes its port mappings straight into iptables, **bypassing ufw**. So
the app is published on loopback only (`127.0.0.1:8000:8000` in
`docker-compose.yml`) and reached through nginx. Never change it to
`8000:8000`: that exposes the app to the internet whatever ufw says.

## 5. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit   # log back in so the group applies
docker compose version
```

## 6. Code and data folders

```bash
git clone https://github.com/jpkrajewski/ofmhelper.git ~/ofmhelper
cd ~/ofmhelper
mkdir -p uploads downloads kieai_out
sudo chown -R 1000:1000 uploads downloads kieai_out
```

The containers run as uid 1000. These three folders are bind-mounted, so the
host folders must belong to uid 1000, or every write fails with
`PermissionError: [Errno 13]`. Postgres, Redis and the uploaded Instagram
cookies live in named Docker volumes (`postgres_data`, `redis_data`,
`cookies_data`).

## 7. `.env`

`~/ofmhelper/.env` (gitignored) holds every secret. Create it with
`nano .env` and lock it down with `chmod 600 .env`.

| Key | Required | What |
|---|---|---|
| `SESSION_SECRET` | yes | Signs the login cookie. `openssl rand -hex 32` |
| `APP_PASSWORD_ADMIN` | yes | Admin login |
| `APP_PASSWORD_VA` | yes | VA login |
| `OFM_VAULT_KEY` | yes, for /phones | Encrypts the stored phone logins and backup codes. See below |
| `SESSION_HTTPS_ONLY` | recommended | `true` once HTTPS works (step 8): the cookie is never sent over plain HTTP |
| `KIE_AI_API_KEY_ADMIN` / `_VA` | for kie.ai | Pre-filled kie.ai keys per role |
| `HF_API_KEY_ADMIN` / `_VA` | for Higgsfield | Pre-filled Higgsfield keys per role |
| `OFM_DEFAULT_PROVIDER` | no | Generation provider the pages start on (default `higgsfield`) |
| `OFM_IG_PROXIES` | for /reels, /accounts | Instagram scan proxies |
| `OFM_EXPORT_DEFAULT` / `OFM_EXPORT_MIRROR` | no | Default video export and Mirror switch |
| `OFM_GDRIVE_SERVICE_ACCOUNT` / `OFM_GDRIVE_ROOT_FOLDER` | for /drive | Google service account key (JSON on one line, in single quotes) and the shared folder's id. See below |
| `OFM_GDRIVE_POSTED_FOLDER` | no | Name of the folder inside each account folder for posted videos (default `POSTED`) |
| `OFM_GDRIVE_TIMEZONE` | no | Where /drive's "posted today" starts (default `Europe/Warsaw`) |

Every other knob has a default; `src/ofmhelpers/config/settings.py` lists them
all with their env names.

**`OFM_VAULT_KEY`.** Generate it once:

```bash
docker compose -f docker-compose.yml run --rm --no-deps ofmhelpers \
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Back the key up outside the server** (a password manager). Without it the
> /phones logins in the database, and in every backup, cannot be decrypted.
> To rotate, put the new key first and keep the old one after a comma
> (`OFM_VAULT_KEY=new,old`). Re-save the logins, then drop the old key.

**`OFM_GDRIVE_SERVICE_ACCOUNT`.** Google Cloud Console: enable the Drive
API, create a service account, add a JSON key. Share the Drive folder with
the account's `client_email` as **Editor**; its id (the end of the folder's
URL) is `OFM_GDRIVE_ROOT_FOLDER`. Put the key in on one line:

```bash
printf "OFM_GDRIVE_SERVICE_ACCOUNT='%s'\n" "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(",", ":")))' key.json)" >> .env
```

When you move servers, copy `.env` over key by key. Never paste a local
development `.env` over the production one.

## 8. nginx + HTTPS

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo rm -f /etc/nginx/sites-enabled/default
```

`/etc/nginx/sites-available/ofmhelpers`, HTTP only. Certbot adds the TLS part:

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name example.com www.example.com;

    # Reel/video uploads go through this proxy; the default 1m would reject
    # anything real.
    client_max_body_size 2048m;
    # Generation jobs are enqueued, not served synchronously, but a slow
    # upstream should not be cut off mid-response either.
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Upgrade headers kept so a future websocket route works unchanged.
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
    }

    # CSS/JS links carry ?v=<content hash> (templates_config.static_url), so
    # browsers may keep them.
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        proxy_set_header Host $host;
        expires 7d;
        add_header Cache-Control "public";
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ofmhelpers /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d example.com -d www.example.com
```

Certbot moves the server block to `listen 443 ssl` with the certificate lines.
It also adds a port-80 block that 301-redirects both names to HTTPS.
`systemctl list-timers certbot.timer` shows the automatic renewal.
`sudo certbot renew --dry-run` tests it.

The app trusts the `X-Forwarded-*` headers: the image starts uvicorn with
`--proxy-headers --forwarded-allow-ips "*"`. Without them, pages would build
`http://` links on an `https://` page, and the rate limiter would see every
client as nginx.

## 9. First start

```bash
cd ~/ofmhelper
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
curl -s http://127.0.0.1:8000/health      # {"status":"ok"}
```

Always pass `-f docker-compose.yml` on the server. `docker-compose.dev.yml` is
the local hot-reload overlay and must never apply in production.

`up` runs the `migrate` service first (`alembic upgrade head`); `ofmhelpers` and
`worker` start only once it has exited successfully. A failed migration leaves
the app down with the error in `docker compose -f docker-compose.yml logs
migrate`. Then open `https://example.com`, log in as admin, and upload
Instagram cookies at `/cookies` if you use /reels or /accounts.

## 10. Deploying

Normal path: work on `dev` → CI → `promote.yml` opens a `dev` → `main` PR → you
merge → CI on `main` → `deploy.yml` deploys.

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push/PR to `dev` or `main` | ruff, djlint, biome; pytest in the Dockerfile `test` stage against compose Postgres/Redis; Docker build |
| `promote.yml` | CI green on `dev` | Opens (or comments on) the `dev` → `main` PR. Never merges |
| `deploy.yml` | CI green on `main`, or manual | Deploys that commit over SSH, then polls `/health` |

Deploy sequence on the server (both paths):
1. `git reset --hard <sha>`
2. `build ofmhelpers worker migrate`
3. `up -d postgres redis pot-provider`
4. `stop ofmhelpers worker`
5. `up -d --no-deps --force-recreate migrate ofmhelpers worker`
6. `/health` poll

Old code is stopped before the migration runs, so it never sees the new
schema. Data is never touched: only `stop`, `build` and `up -d` run, never
`down -v`. In-flight RQ jobs are killed by the swap. kie.ai work resumes on its
own; re-run an Instagram stats sweep.

**Repository secrets** (Settings → Secrets and variables → Actions,
environment `production`):

| Secret | Example |
|---|---|
| `OFM_DEPLOY_HOST` | `ubuntu@203.0.113.10` |
| `OFM_DEPLOY_DIR` | `/home/ubuntu/ofmhelper` |
| `OFM_DEPLOY_SSH_KEY` | private key, whole PEM. Its public half goes in the server's `~/.ssh/authorized_keys` |
| `OFM_DEPLOY_KNOWN_HOSTS` | optional: `ssh-keyscan -H 203.0.113.10` |

New server = update `OFM_DEPLOY_HOST` (and `OFM_DEPLOY_KNOWN_HOSTS`).

**Manual escape hatch:** `./scripts/deploy.sh "commit message"` from a checkout
on `main`. It commits, pushes, then runs the same sequence. It reads
`deploy.local.env` (gitignored; copy `deploy.local.env.example`).

## 11. Backups

```bash
cd ~/ofmhelper
docker compose -f docker-compose.yml exec -T postgres \
  sh -c 'pg_dump -U $POSTGRES_USER -d $POSTGRES_DB --no-owner --no-privileges' \
  | gzip > ~/ofmhelpers-$(date +%F).sql.gz
```

Copy the dump off the server. A full restore needs four things: this dump,
`.env` (above all `OFM_VAULT_KEY`), `uploads/`, and `kieai_out/`.

## 12. Moving to a new server

1. Steps 1–8 on the new box.
2. On the old box: run the backup from step 11, and copy `.env`,
   `uploads/` and `kieai_out/` over, e.g.
   `rsync -a uploads kieai_out new:~/ofmhelper/`.
3. On the new box: start Postgres, load the dump, then start everything:
   ```bash
   docker compose -f docker-compose.yml up -d postgres
   gunzip -c ofmhelpers-DATE.sql.gz | docker compose -f docker-compose.yml exec -T postgres \
     sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB'
   docker compose -f docker-compose.yml up -d
   ```
4. Point DNS at the new IP, then run certbot (step 8).
5. Update the `OFM_DEPLOY_*` secrets.

## 13. Local development

Every check runs in Docker with the dev overlay:

```bash
C="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
$C up -d                                   # stack with hot reload (./src mounted)
$C --profile test run --rm tests           # pytest
$C --profile test run --rm tests uv run --frozen pytest -m e2e   # browser suite, stack up
```

`CLAUDE.md` lists the lint and type checks. Worker code changes need
`$C restart worker`: the worker has no reloader.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Domain does not load at all | DNS not pointing here yet, or ufw missing 80/443 | `dig +short example.com`; `sudo ufw status` |
| `502 Bad Gateway` | App container down | `docker compose -f docker-compose.yml ps`; `curl -I http://127.0.0.1:8000/health`; logs |
| App and worker never start after a deploy | `migrate` failed | `docker compose -f docker-compose.yml logs migrate` |
| Container restarts at boot | Required `.env` key missing (`SESSION_SECRET`, `APP_PASSWORD_*`) | `docker compose -f docker-compose.yml logs ofmhelpers` |
| /phones shows "OFM_VAULT_KEY is not set" or "do not decrypt" | Key missing, or not the key the logins were saved with | Put the original key back in `.env` (first in the list), `up -d` |
| /drive says the bot cannot open the folder | Folder not shared with the service account, or wrong `OFM_GDRIVE_ROOT_FOLDER` | Share it with the email /drive names, as Editor |
| `PermissionError: [Errno 13]` on uploads/downloads | Host folder not owned by uid 1000 | `sudo chown -R 1000:1000 uploads downloads kieai_out` |
| Uploads fail with `413` | nginx body limit | `client_max_body_size` in the site file, `sudo systemctl reload nginx` |
| Mixed-content warnings / `http://` links on the HTTPS site | Forwarded headers not reaching the app | nginx `X-Forwarded-Proto`; image CMD keeps `--proxy-headers` |
| Locked out of SSH | sshd change | Provider web console / KVM, fix `/etc/ssh/sshd_config.d/` |
