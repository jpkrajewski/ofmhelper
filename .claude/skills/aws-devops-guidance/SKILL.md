---
name: aws-devops-guidance
description: Look up the AWS Well-Architected DevOps Guidance (196 indicators, 104 FOUNDATIONAL) for the part of this app being changed. Use when a change touches CI or deploy workflows, compose/Docker, pre-commit hooks, branching or review flow, test strategy or test environments, secrets and server access, backups, data retention, migrations, dependency or licence policy, logging, telemetry, alerts or incident handling. Searches a local index and reads one indicator at a time; never load the whole guide.
---

# AWS DevOps Guidance, searched not loaded

Source: https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/ (PDF, 236 pages). Indicator ids: `[PILLAR.CAPABILITY.N]`, pillars `OA` organization, `DL` development lifecycle, `QA` quality assurance, `AG` automated governance, `O` observability. Categories: FOUNDATIONAL, RECOMMENDED, OPTIONAL.

Files here (`.claude/skills/aws-devops-guidance/`):
- `foundational.md` every FOUNDATIONAL indicator as a rule for this app, where the app does it, and the known gaps. ~130 lines.
- `index.md` all 196: `ID | CATEGORY | title | page slug`. One line each.
- `cache/<ID>.md` AWS's full text of one indicator (3-13 KB each). Gitignored: AWS's text is all rights reserved and this repo is public. Never commit it, never paste long passages into committed files.

## Steps
1. Grep `foundational.md` for the area (`DL.CD`, `backup`, `rollback`, `secret`, ...). Often enough.
2. More detail, or a RECOMMENDED/OPTIONAL one: Grep `index.md` by keyword, pick the ids.
3. Read only those `cache/<ID>.md`. Full-text search: Grep the `cache/` folder for the term, `files_with_matches` first, then Read the one or two hits. The tail of a capability's last indicator also holds that capability's anti-patterns and metrics.
4. `cache/` missing or AWS published a new version (about 30 s, also rewrites `index.md`):
   `MSYS_NO_PATHCONV=1 $C run --rm --no-deps -v "$(pwd -W)/.claude/skills/aws-devops-guidance:/skill" tests uv run --no-project --with pypdf python /skill/build_cache.py`
   (`C="docker compose -f docker-compose.yml -f docker-compose.dev.yml"`, from the repo root in Git Bash). No Docker at hand: WebFetch `https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/<slug>.html`, slug from `index.md`.

## How to use what you find
- The owner's rules in `CLAUDE.md` win over the guide. Where they differ on purpose, `foundational.md` "Deviations" says so; do not "fix" those.
- The guide is for the thing being changed. It never widens the task: a gap you notice is one line in the handoff, not extra work.
- Written for AWS organizations with many teams. This app is one owner, AI agents, one VPS, Docker Compose, GitHub Actions: translate (account = environment, pipeline = `ci.yml`/`promote.yml`/`deploy.yml`, IAM = SSH keys + `.env`), skip what has no counterpart.
- After a change that closes a gap or adds a deviation, update that line in `foundational.md`.
