---
paths:
  - "pyproject.toml"
  - "uv.lock"
  - "renovate.json"
  - "Dockerfile*"
  - ".claude/skills/fastapi/**"
---

# Dependencies

Moved word for word from `CLAUDE.md` "Libraries & Tools" (2026-11-11).

- Dependency bumps: Renovate (`renovate.json`) opens grouped PRs into dev, label `deps` (monthly, exact pins in pyproject so a PR installs what it names; yt-dlp/gallery-dl daily; security fixes at once; majors one PR each, never Postgres/Redis majors). Dependabot = GitHub's security alerts only (its PRs would target main). Every `ship` merges the green non-major ones, fixes or reports the red ones, and upgrades any alerted package with no PR (the skill's step 3). Never merge a bot PR into main.
- `.claude/skills/fastapi/` is FastAPI's own skill, a copy of what the pinned version ships. After upgrading FastAPI: `$C run --rm --no-deps tests uvx library-skills --claude --yes --copy --no-tool-skill --skill fastapi`, then delete the `.agents/` it also writes. Where our code goes against it on purpose, the reason is in `web/routers/CLAUDE.md`.
