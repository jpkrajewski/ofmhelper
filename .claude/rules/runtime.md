---
paths:
  - "docker-compose*.yml"
  - "Dockerfile*"
  - "src/ofmhelpers/config/**"
  - ".github/workflows/**"
  - "scripts/deploy.sh"
---

# Runtime and config: MUST

Source: The Twelve-Factor App (https://12factor.net/, one page per factor: `https://12factor.net/<slug>`). Deploy, CI, backups, secrets, alerts: the `aws-devops-guidance` skill.

1. **Codebase** (`/codebase`): one repo, many deploys. Dev and prod run the same commit history; nothing exists only on the server.
2. **Dependencies** (`/dependencies`): all declared and pinned (`pyproject.toml`, `uv.lock`, the image). Nothing assumed to be on the host.
3. **Config** (`/config`): what differs between dev and prod is an environment variable read through `config/settings.py` (one settings group per module). No secret, host, key or URL in code. The server `.env` is changed key by key.
4. **Backing services** (`/backing-services`): Postgres, Redis, Drive, providers are attached by URL or credentials from config; swapping one is a config change.
5. **Build, release, run** (`/build-release-run`): the image is built from a commit, named by it (`ofmhelpers-app:<sha>`), then run; the one before is kept, and going back is deploying that sha. Both deploys take only a commit of `origin/main`. Never edit code on the running server.
6. **Processes** (`/processes`): web, sweeps and workers keep nothing in memory or on local disk that another process or the next deploy needs (a cache file goes under the uploads root, never the container's temp dir). State lives in Postgres, Redis or a mounted volume.
7. **Port binding** (`/port-binding`): the app serves HTTP itself on its port; nginx only proxies. Published on loopback only.
8. **Concurrency** (`/concurrency`): more load = more worker processes per queue (`InfraSettings.queues`), not threads inside one. Web is HTTP only (the sweeps are their own service, exactly one); worker services have no `container_name`, so they scale.
9. **Disposability** (`/disposability`): a process can be killed at any moment. Fast start, clean stop on SIGTERM, jobs resumable or safely repeatable.
10. **Dev/prod parity** (`/dev-prod-parity`): same images, same services, same versions; dev differs only by the overlay file and runs on prod data.
11. **Logs** (`/logs`): the app writes events to stdout through `get_logger`; it never manages log files. Collection is Logfire's job.
12. **Admin processes** (`/admin-processes`): one-off work (migrations, backfills) runs from the same image and code as the app (`migrate` service, `python -m ...`), never a script outside the repo.
