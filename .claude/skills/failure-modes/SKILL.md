---
name: failure-modes
description: The 7 ways code in this app breaks under abuse, each with its rule, the repo pattern to copy and its test. Load BEFORE writing or changing a route, a page's submit or poll JS, a background job, or an outside call (web/routers, web/static/js, features, integrations), and as the reviewer's checklist in ship step 2.
---

# Failure modes

Before the code: name, for each mode below, what your change does. After the code: one test per mode that applies. As a reviewer: report every mode that breaks in one pass, each with file:line and the fix.

Bugs reviewers found in one session (2026-11-20, idempotency, 8 review rounds): crash after paying, the key lost on an error, unbounded 409 retries, a non-JSON 2xx, one card stopping every poll, `a=b` hashing, a missing JSONB index. Each is a mode below.

## 1. Service or DB down or slow
- Rule: a timeout inside the caller's budget, a typed refusal (`features/refusals.py`), the job row says why. No bare except, no default in place of an error.
- Copy: `features/jobs/runner.py` `run_job` (`refused`, `resumable`, `store.record_failure`, span exception).
- Test: integration test with the dependency raising; assert the job row text and the logged trace.

## 2. Backend answers 5xx, HTML or non-JSON
- Rule: the page never throws on a bad body: parse in try/catch, a 2xx that is not JSON is a failure too. The button comes back and a plain message shows.
- Copy: `web/static/js/idempotency.js` `send`, `refused`.
- Test: e2e `route.fulfill` of a 500, an HTML 200 and an abort (`tests/e2e/test_download_cards_page.py`); assert the message, the enabled button and `js_errors == []`.

## 3. Crash, cancel or deploy after a side effect
- Rule: record the outcome before releasing the lock or the key; a crashed request is never run again (the first may have paid and queued), it answers "did not finish, reload". Replay the stored answer, do not rerun.
- Copy: `web/idempotency.py` `IdempotentRoute` (`_keep`, `_settle_crash`, `ABANDONED`); `features/jobs/runner.py` `run_job` (cost kept in `_keep_cost` even on failure); jobs rerun from stored params.
- Test: kill the handler after the side effect, repeat the key, assert one side effect and the stored answer.

## 4. 50 clicks, two tabs, retries
- Rule: every action that starts work or pays carries an Idempotency-Key (`IdempotentRoute`) or goes through `put_once`. The browser keeps the key until the answer is final, drops it after a refusal, and retries a busy (409) with bounded backoff, never forever.
- Copy: `web/idempotency.py` `IdempotentRoute`; `web/static/js/idempotency.js` `keyFor`, `send`, `holdUntilSettled`; `cache/queue.py` `JobQueue.put_once` (a job already in line is not queued twice).
- Test: `tests/integration/web/test_idempotency.py` (20 at once through a thread pool: one 202, the rest 200); `tests/idempotent.py` `keyed()` gives a request its header.

## 5. Junk input
- Rule: Pydantic at the edge, length caps, structured hashing: hash name-value pairs, never `name=value` joined strings (`a=b` and `a`,`=b` collide).
- Copy: `web/idempotency.py` `_field` and `_fields` (fields hashed as pairs).
- Test: 10k characters, emoji, RTL, `<script>`, empty; `scripts/ui_check.py` does this on the page (skill `ui-check`).

## 6. Scale
- Rule: an index for every column a query filters or sorts on (JSONB keys included: a missing JSONB index was a finding); a retention sweep for every table that only grows; polls back off and pause while the tab is hidden; one slow row never blocks the list.
- Copy: `web/static/js/repurpose.js` `followUploads` (backoff x1.5 to a cap, waits on `visibilitychange`); `.claude/rules/database.md`.
- Test: query plan or index assertion in an integration test; a sweep test with old rows.

## 7. JS crash
- Rule: null-safe DOM access; one failing card never stops the others (`Promise.allSettled`, a 4xx drops only that card from the poll); every e2e asserts `js_errors == []`.
- Copy: `web/static/js/repurpose.js` `followUploads` (`gone` set, `allSettled`); `tests/e2e/e2e_session.py` (`pageerror` collected into `js_errors`).
- Test: one card answering 404 or 500 while the others still update; `assert page.js_errors == []`.
