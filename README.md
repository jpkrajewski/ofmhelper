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

```bash
docker compose up -d --build
docker compose ps                    # both services should show "Up"
docker compose logs -f ofmhelpers    # watch for "Application startup complete"
```

Sanity check from the box itself:
```bash
curl -I http://127.0.0.1:8000
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

```bash
cd ~/ofmhelper
git pull
docker compose up -d --build
docker compose logs -f ofmhelpers   # confirm clean startup
```

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
