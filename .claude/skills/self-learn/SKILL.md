---
name: self-learn
description: The self-improvement loop. Reads the saved logs of every agent run since the last pass (Claude Code session logs incl. subagents, the hook audit), finds each place an agent failed, wasted tokens or got corrected by the owner, and writes each lesson where the next agent cannot miss it. Use after every successful deploy (ship step 10; the self_learn_after_deploy guard holds the session open until it ran) and when the owner asks to self-learn, reflect or "stop making the same mistakes".
---

# Self-learn

The loop: an agent does a job, the logs keep every step, this pass reads them, finds where it failed, and writes the lesson down so the next run starts smarter. The next run must never repeat a mistake. Lessons come from the logs, not from what the agent remembers of its session (memory is lossy after compaction and never sees other sessions).

## 1. Digest
`PYDANTIC_DISABLE_PLUGINS=__all__ uv run --frozen --no-sync python scripts/self_learn.py`
(since the last pass, i.e. the last deploy's pass; first run 7 days; `--days N` to look further back). It writes `reports/self-learn/<stamp>.md`:
- every prompt the owner typed, interrupts, tool calls the owner rejected;
- failed tool calls and guard denies/blocks, grouped and counted;
- tokens per session (subagents included), files read 3x+ in one session, the biggest outputs.
Read the whole digest.

## 2. Find the lessons
- **Owner corrections.** A prompt that corrects, repeats an earlier ask, asks "why", "what did I tell you", swears or shouts; an interrupt; a rejected call. The agent did something the owner did not want: open that session's log (`~/.claude/projects/<project>/<session>.jsonl`, search the timestamp) and find the step just before it.
- **Failures.** The same failure 3+ times is a lesson. A guard that keeps denying: agents do not know the rule early enough, or the guard is wrong (a false positive).
- **Token waste.** A file read 3x+ in one session, outputs over ~15k characters, a session far above the others: name the brief, doc or tool that would have saved it.
One lesson per cause, not per occurrence.

## 3. Write each lesson once, in the strongest place
1. **A check**, when a check can catch it: a test, a guard in `.claude/hooks/guards/` (+ its case in `tests/unit/hooks/test_guards.py`), a startup refusal. Delete the BRAIN line it replaces.
2. **A wrong guard or rule**: fix or delete it (a false positive = fix the guard and its test).
3. **`BRAIN.md`**, only when no check can catch it: the line says "no check can catch it because ...". Not memory, because `@BRAIN.md` in `CLAUDE.md` loads it whole into every session and subagent, while memory guarantees only its one-line index (the rule and its why sit in files an agent may never open). Memory keeps who the owner is and how they like to work. Line format: `<added> / hit <date>: <the rule> -- why: <the incident in one clause>`. Max 40 lines; at 40 the line hit longest ago goes. A line that helped this period gets today as its hit date; a line that turned out wrong is deleted.
4. **A module fact**: the narrowest module `CLAUDE.md`.
5. **Code or doc the owner should pick**: one `IMPROVEMENTS.md` line with its `[agent can do]` / `[owner decides]` / `[needs owner check]` tag.
Every lesson names the check (guard, test or skill) it became; a lesson with no named check and no "no check can catch it because" is not finished.
Skip what `CLAUDE.md`, a rule file or a guard already says. Never edit the owner's `CLAUDE.md` sections (they stay word for word). Never copy owner prompts, secrets, hosts or customer data into tracked files.

## 3b. Who reflects
After a ship the reflection is a fresh agent that reads all the session's logs (the main transcript, the subagent logs, `reports/hooks/audit.jsonl`, the digest) and plans the hooks and skills that would have prevented each failure. The main session turns that plan into a plan for the owner; it does not write the checks unasked.

## 4. Report and mark
- Report to the owner: one line per lesson (what happened, count, where it went), the biggest token waste and its fix, what was left for the owner.
- Then mark the pass: `PYDANTIC_DISABLE_PLUGINS=__all__ uv run --frozen --no-sync python scripts/self_learn.py --done`. The next pass starts there, after the next deploy.
