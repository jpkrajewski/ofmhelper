# Deployment Runbook — ofmhelpers

Everything needed to deploy this app from zero on a fresh Oracle Cloud (OCI)
VM: instance creation, networking, Docker, reverse proxy, free HTTPS, and
fixes for every gotcha we hit the first time through.

---

## 1. Create the OCI instance

**Compute → Instances → Create Instance**

| Setting | Value |
|---|---|
| Image | Ubuntu 22.04 |
| Shape | `VM.Standard.A1.Flex` (Always Free) or a paid shape (`E4.Flex`, `E5.Flex`) if Always Free capacity is unavailable in your region |
| Networking | Create new VCN + new **public** subnet (or reuse an existing public subnet) |
| SSH keys | Generate or paste your own public key — save the `.pem` |

### If you hit "Out of capacity"
This is common on free-tier shapes and happens per-Availability-Domain, not
per-region. Fix, in order:
1. Try a different AD (`AD-1`, `AD-2`, `AD-3` if your region has them).
2. Try a different shape generation (`E4.Flex` → `E5.Flex` → `E3.Flex`, or
   `A1.Flex`).
3. If you have OCI's $300 trial credit, a **paid** shape draws from a
   separate, much larger capacity pool and usually provisions immediately.
4. Otherwise just retry later — capacity fluctuates through the day.

Set a **budget alert** if you're spending trial credit: Billing → Budgets →
Create Budget (a small threshold is enough to catch runaway spend early).

---

## 2. Networking — required for the instance to be reachable at all

### a) Confirm/assign a public IP
Instance → **Networking** tab → click the VNIC → **IP administration** →
if Public IP shows "Not Assigned", use the row's `...` menu (or click
directly on "Not Assigned") → **Assign Public IP Address** → Ephemeral.

### b) Open the firewall — Security List
**Networking → your VCN → Security Lists → Default Security List →
Add Ingress Rules**. Add TCP rules for:
- `80` (HTTP)
- `443` (HTTPS)

(`22` for SSH is open by default.)

### c) Confirm Internet Gateway + route table exist
**Networking → your VCN → Internet Gateways** — should have one. If not,
create it, then **Route Tables → Default Route Table** → add a rule:
destination `0.0.0.0/0` → target **Internet Gateway**.

The "create new VCN" wizard usually sets both of these up automatically for
a public subnet — only check this if something's not reachable.

---

## 3. SSH in

```bash
ssh -i /path/to/your-key.pem ubuntu@<PUBLIC_IP>
```

### Open the instance's own firewall (separate from the Security List above)
Both layers must be open — the Security List controls OCI's edge, iptables
controls the box itself:

```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 4. Install Docker + Compose

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker $USER
newgrp docker

docker compose version   # sanity check
```

---

## 5. Get the code

```bash
git clone https://github.com/jpkrajewski/ofmhelper.git
cd ofmhelper
```

### Create the directories the compose file bind-mounts
These must exist before first run, or Docker creates them as `root`-owned
and your app (running as a non-root user inside the container) can't write
to them:

```bash
mkdir -p uploads downloads kieai_out secrets/cookies
```

### Fix ownership to match the container's user (uid 1000)
```bash
sudo chown -R 1000:1000 uploads downloads kieai_out secrets
```

If you ever see `PermissionError: [Errno 13] Permission denied` in the app
logs after a fresh clone on a *new* machine, this is almost always the
cause — re-run the `chown` above.

### Google Drive token
The todo list's "Upload to Drive" button (see step 6) needs a token file at
`secrets/google-drive-token.json` — it's never committed (`secrets/` is
gitignored) and can't be generated on a headless server (the one-time
consent needs a browser), so generate it on your own machine first and copy
it over:

```bash
scp -i /path/to/your-key.pem /path/to/google-drive-token.json ubuntu@<PUBLIC_IP>:~/ofmhelper/secrets/google-drive-token.json
```

Then, on the server, re-fix ownership — `scp` (especially if you ever ran it
via `sudo`, or the destination already existed root-owned) can easily land
the file as `root:root`, which the container's non-root user can't write
back to when the token refreshes:

```bash
sudo chown -R 1000:1000 ~/ofmhelper/secrets
```

Do this every time you replace the token file, not just on first setup.

---

## 6. Environment variables

```bash
nano .env
```

Required:
```
APP_PASSWORD_ADMIN=choose-an-admin-password   # shared admin login
APP_PASSWORD_VA=choose-a-va-password          # shared VA login
SESSION_SECRET=<output of: openssl rand -hex 32>
```

Generate the session secret:
```bash
openssl rand -hex 32
```

Optional — pre-fills the kie.ai API key field on the Seedance / Kling 3.0 /
Nano Banana Pro forms based on which of the two passwords above was used to
log in, so VAs don't have to paste a key in by hand:
```
KIE_AI_API_KEY_ADMIN=...
KIE_AI_API_KEY_VA=...
```
The field stays editable either way — if these aren't set, or you need a
different key for a one-off job, just paste over the pre-filled value.

Same idea for ElevenLabs (the `/helpers/elevenlabs` form and the "Subject's
speech" panel on the `/replicate` review page) — one key, not one per role:
```
ELEVENLABS_API_KEY=...
```

Required for the todo list's "Upload to Drive" feature:
```
GOOGLE_DRIVE_TOKEN_FILE=secrets/google-drive-token.json
GOOGLE_DRIVE_FOLDER_ID=<the destination Drive folder's id>
```
Auth is OAuth as your own Google account, not a service account — a service
account has zero storage quota of its own, so it can't upload to a personal
(non-Workspace) Drive even if the folder is shared with it. Uploading as you
spends your own quota instead, which is what you want anyway.

One-time setup, done **locally** (needs a browser — not on the server):
1. In [Google Cloud Console](https://console.cloud.google.com/): create (or
   reuse) a project → **APIs & Services → Library** → enable the **Google
   Drive API**.
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → application type **Desktop app**. Download the JSON and save it as
   `secrets/google-oauth-client.json`.
3. Run the consent flow — it opens a browser, you approve access, and it
   writes the resulting token:
   ```bash
   uv run python -m ofmhelpers.gdrive.authorize
   ```
   This writes `secrets/google-drive-token.json`. That's the only file the
   server needs — the OAuth client JSON from step 2 isn't used again after
   this.
4. Copy the token file onto the server (see step 5 above) and find the
   destination folder's id — the last segment of its Drive URL:
   `drive.google.com/drive/folders/<THIS_PART>`.

Required for the todo list's Discord approval notifications (see
`web/stores/approval_tokens.py` / `web/routers/workflow/approve.py`) — whenever a VA/admin
uploads an asset for review, a Discord message goes out with a preview and a
one-tap, no-login approval link:
```
DISCORD_WEBHOOK_URL=<Discord channel → Edit Channel → Integrations → Webhooks>
APP_BASE_URL=https://your-domain-or-sslip-io-host
```
`APP_BASE_URL` is the origin reviewers reach the app at from outside (what
you set up in steps 8–9 below) — it's used to build the absolute preview/
approval links Discord needs, since the app isn't configured to trust
proxy headers for scheme/host detection.

Plus whatever other provider keys the app needs (ElevenLabs, etc.).

**Missing `SESSION_SECRET`, `APP_PASSWORD_ADMIN`, or `APP_PASSWORD_VA` will
crash the app on startup** (`KeyError`) — if the container keeps restarting
or `curl localhost:8000` resets the connection, check
`docker compose logs ofmhelpers` first; this is the most common cause.

---

## 7. Run it

`docker-compose.yml` should map the app to the **internal** port only —
Nginx (below) owns port 80/443 on the host:

```yaml
ports:
  - "8000:8000"
```

The stack is one `docker-compose.yml` (dev and prod): `ofmhelpers` (API),
`worker` (RQ background jobs), `postgres` (durable job/todo/token store),
`redis` (RQ broker), and `pot-provider`. Postgres/Redis persist to named
volumes, so no extra host directories are needed for them.

```bash
docker compose up -d --build
docker compose ps                    # api healthy; postgres/redis healthy; worker up
docker compose logs -f ofmhelpers    # watch for "Application startup complete"
```

**Apply the database schema** (first run, and after any migration — idempotent):
```bash
docker compose exec ofmhelpers alembic upgrade head
```

**One-time backfill** of any pre-existing JSON state (`uploads/jobs.json`,
`todos.json`, `approval_tokens.json`) into Postgres. Idempotent (upserts by id)
and archives the originals to `uploads/archive/`:
```bash
docker compose exec ofmhelpers python -m ofmhelpers.web.db.backfill
```
(`scripts/deploy.sh` runs both of the above for you on every deploy, so you only
need to run them by hand on a manual first bring-up.)

Sanity check from the box itself:
```bash
curl -I http://127.0.0.1:8000
```

Optional dev-only RQ job dashboard (off by default; localhost:9181):
```bash
docker compose --profile dev up -d rq-dashboard
```

---

## 8. Nginx reverse proxy

```bash
sudo apt install -y nginx
sudo nano /etc/nginx/sites-available/ofmhelpers
```

```nginx
server {
    listen 80;
    server_name YOUR_HOSTNAME_HERE;   # see step 9 for what goes here

    client_max_body_size 500M;        # forms upload video/image files

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;      # long-running video gen jobs
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ofmhelpers /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

**If `systemctl restart nginx` fails**, it's almost always a port conflict —
something else (usually Docker still mapped to `80:8000`) is already bound
to port 80:
```bash
sudo ss -tlnp | grep :80
```
Fix by making sure `docker-compose.yml` maps `8000:8000`, not `80:8000`,
then `docker compose up -d`.

---

## 9. Free HTTPS — no domain purchase needed

Let's Encrypt won't issue a cert for a bare IP, but **sslip.io** gives you a
free hostname that resolves straight back to your IP with zero signup:

```
<your-ip-with-dashes>.sslip.io
```
e.g. IP `145.241.160.124` → `145-241-160-124.sslip.io` (already resolves,
no setup required).

Use that as `server_name` in the Nginx config above, then:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 145-241-160-124.sslip.io
```

Certbot edits the Nginx config for you (HTTP→HTTPS redirect) and sets up
auto-renewal via a systemd timer. Result: `https://145-241-160-124.sslip.io`
with a real, trusted cert, entirely free, forever.

---

## 10. Updating / redeploying after code changes

**From your local machine** (recommended) — commits, pushes, pulls on the
server, rebuilds, migrates, and backfills in one shot:

```bash
./scripts/deploy.sh "your commit message"
```

It reads the server connection details from `deploy.local.env` (gitignored; see
`deploy.local.env.example`) and, on the server, runs:
`git pull && docker compose up -d --build && alembic upgrade head &&
python -m ofmhelpers.web.db.backfill`, then polls `/health`.

**Or manually on the server:**
```bash
cd ~/ofmhelper
git pull
docker compose up -d --build
docker compose exec -T ofmhelpers alembic upgrade head
docker compose logs -f ofmhelpers   # confirm clean startup
```

The `worker` container has its own `restart: unless-stopped` policy,
independent of the API — a worker crash won't take down the web app, and vice
versa.

### CI/CD (GitHub Actions)

Three workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push/PR to `dev` or `main` | ruff check + ruff format, pytest against real Postgres 16 + Redis 7 service containers, and a Docker build |
| `promote.yml` | CI green on `dev` | Opens (or comments on) a PR `dev` → `main`. Never merges — you do |
| `deploy.yml` | CI green on `main`, or manual | SSHes to the server, `git reset --hard origin/main`, `docker compose up -d --build`, `alembic upgrade head`, then polls `/health` |

Normal flow: work on `dev` → CI runs → promotion PR appears → you review and
merge → `main` CI runs → deploy. `scripts/deploy.sh` still works as the manual
escape hatch and is unchanged.

**Required repository secrets** (Settings → Secrets and variables → Actions).
These mirror `deploy.local.env`; without them the deploy job fails fast with an
explicit message rather than a confusing SSH error:

| Secret | Example | Notes |
|---|---|---|
| `OFM_DEPLOY_HOST` | `ubuntu@203.0.113.10` | user@host |
| `OFM_DEPLOY_DIR` | `/home/ubuntu/ofmhelper` | repo path on the server |
| `OFM_DEPLOY_SSH_KEY` | *(private key)* | full contents of the PEM, including header/footer lines |
| `OFM_DEPLOY_KNOWN_HOSTS` | *(optional)* | output of `ssh-keyscan -H <host>`. Omitted → the runner trusts the key on first connect and logs a warning |

The deploy job runs under a `production` GitHub Environment, so you can add a
required reviewer there if you want a second gate before anything reaches the
server.

---

## 11. Running the tests

The test suite is Postgres- and Redis-backed (the durable stores and the RQ
queue), so bring those up first, then run pytest from the repo root:

```bash
docker compose up -d postgres redis
uv run pytest
```

A throwaway `ofmhelpers_test` database is created automatically and truncated
between tests — it never touches dev/prod data. Point the suite at a different
instance with `OFM_TEST_DATABASE_URL` / `OFM_TEST_REDIS_URL` if needed.

Before considering a change done (see `CLAUDE.md`): run
`pre-commit run --files <changed>` and `uv run pytest`.

---

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser times out entirely | Public IP not assigned / propagating | Check VNIC → IP administration; wait a few min after assigning |
| Times out only on your PC, works on phone (mobile data) | Local network/router/firewall blocking the port | Use port 80/443 instead of high ports; they're rarely blocked |
| `curl localhost:8000` hangs on the box itself | App/container not actually running | `docker compose ps`, `docker compose logs` |
| `curl localhost:8000` works, external access doesn't | Security List / iptables / route table gap | Re-check steps 2b, 2c, 3 |
| `502 Bad Gateway` from Nginx | App container down, or Nginx pointed at wrong port | `docker compose ps`; `curl -I http://127.0.0.1:8000` |
| `nginx: ... failed to restart` | Port 80 already bound (usually Docker) | `sudo ss -tlnp \| grep :80`; fix compose port mapping |
| `PermissionError: [Errno 13]` writing to downloads/uploads/etc | Bind-mounted host folder owned by root | `sudo chown -R 1000:1000 uploads downloads kieai_out secrets` |
| Container keeps restarting / `Connection reset by peer` | Missing required env var (`APP_PASSWORD_ADMIN`, `APP_PASSWORD_VA`, `SESSION_SECRET`) crashing app on boot | Check `docker compose logs ofmhelpers`; verify `.env` |
| Form submit shows "Method Not Allowed" | Red herring — `fetch()` follows a failed response's URL via GET. Check the Network tab for the *first* request's real status, not this one | — |
| "Upload to Drive" fails with `FileNotFoundError: No Google Drive token` | `secrets/google-drive-token.json` missing on the host, or `secrets/` not bind-mounted | Run `uv run python -m ofmhelpers.gdrive.authorize` locally, `scp` the token to `secrets/google-drive-token.json` (step 5); confirm `docker-compose.yml` mounts `./secrets:/app/secrets` |
| "Upload to Drive" fails with `[Errno 30] Read-only file system` on the token file | The OAuth token refreshes itself and rewrites the file on disk (unlike the old service-account key, which was static) — the `secrets/` mount must be writable, not `:ro` | Make sure `docker-compose.yml`'s volume line reads `./secrets:/app/secrets` (no `:ro`), then `docker compose up -d` |
| "Upload to Drive" fails with `PermissionError: [Errno 13]` writing `google-drive-token.json` specifically | The mount is writable, but the token file itself is `root`-owned (common after `scp`-ing a fresh token, especially via `sudo`) — the container runs as uid 1000, so it can't overwrite a `root:root` file on refresh even though the directory looks fine | `sudo chown -R 1000:1000 secrets/`; no restart needed, next refresh just works |
| "Upload to Drive" fails with `storageQuotaExceeded` | Using a service-account key instead of the OAuth token flow — service accounts have no storage quota on a personal Drive | Follow step 6's OAuth setup instead (`ofmhelpers.gdrive.authorize`), not a service account key |
| "Upload to Drive" fails with a 404 from the Drive API | Destination folder doesn't belong to (or isn't shared with) the account you authorized | Re-run the authorize flow with the Google account that owns/has access to the target folder |
