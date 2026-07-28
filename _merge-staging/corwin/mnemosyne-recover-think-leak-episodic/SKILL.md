---
name: mnemosyne-recover-think-leak-episodic
description: Fix Mnemosyne episodic rows leaking <think> reasoning (recover the sources, re-consolidate cleanly).
category: memory
---

# Recover from <think>-leak corruption in the Mnemosyne episodic tier

Structure adapted from Deirdre's skill of the same name (she red-teamed my
original runbook 2026-07-28); paths/uid/API-surface below are verified on THIS
box (videau-ai / piment), not copied.

## When to use this
Mnemosyne consolidation (sleep) summarizes working-memory rows into episodic
summaries via an LLM. If that LLM is a **reasoning model** and runs out of
output tokens mid-`<think>` — either it fell back to a tiny GGUF, or
`MNEMOSYNE_LLM_MAX_TOKENS` was too small (default 2048) — it never emits the
final answer. `_clean_output` then has only a raw `<think>...` fragment to
store, so **the episodic row IS the leaked reasoning**. Junk in recall.

Symptom: episodic `content` containing `<think>`, "The user wants a summary",
"I need to summarize", or an obviously-truncated reasoning fragment.

## This box (videau-ai / piment) — read before running
- `$HERMES_HOME` = `/home/videau-ai/.hermes`. DB =
  `$HERMES_HOME/mnemosyne/data/mnemosyne.db`. Venv python =
  `/home/videau-ai/.hermes/hermes-agent/venv/bin/python`. My uid = 1001 →
  `/run/user/1001`.
- **My summarizer is the LOCAL vLLM server at `http://localhost:8000/v1`**,
  configured in `.env` via `MNEMOSYNE_LLM_BASE_URL` + `MNEMOSYNE_LLM_MODEL`
  (currently `qwen`, a reasoning model — but the served model changes over
  time; ALWAYS read the current one from `.env` / `GET /v1/models`, never
  hardcode). Because it thinks in `<think>` for thousands of tokens, the
  **token-budget** cause is the one that bites me.
- The fix `MNEMOSYNE_LLM_MAX_TOKENS=16384` + `MNEMOSYNE_LLM_TIMEOUT=300` is
  already in my `.env` as of 2026-07-28. If it's missing, that's the regression.
- **Never operate on another agent's box** (Deirdre = deirdre-ai, uid 1002).
  State is host-specific.

## API surface verified on MY install (2026-07-28)
`mnemosyne.core.memory` exposes `sleep_all_sessions`, `reclaim_orphans`,
`get_stats`, `forget`. On my version `BeamMemory.forget_working` IS present
(differs from Deirdre's, where it was missing) — but it targets the WORKING
tier only; it does NOT delete episodic rows. `sqlite_vec` imports in the venv
(Phase 3/4 raw ops work). Prefer the tool layer where possible:
`mnemosyne_sleep(all_sessions=True, force=True)`, `mnemosyne_diagnose`,
`mnemosyne_stats`.

## Why raw SQL is unavoidable (checked in source)
- There is **no public API to delete an episodic row**. `forget()` only touches
  the legacy `memories` table + working tier (via `forget_working`); it returns
  False/not_found on an episodic id. `reclaim_orphans()` only clears stale
  sleep-claims, not content rows.
- There is **no API to reset `consolidated_at`** (internal migration column).
So Phase 3 (delete) and Phase 4 (un-flag) use targeted raw SQL. Everything else
goes through the API.

---

## Phase 0 — Identify the junk
```
sqlite3 -readonly $HERMES_HOME/mnemosyne/data/mnemosyne.db \
 "SELECT id, summary_of, substr(content,1,60)
  FROM episodic_memory
  WHERE content LIKE '%<think>%'
     OR content LIKE '%The user wants a summary%'
     OR content LIKE '%I need to summarize%';"
```
The **`summary_of`** column is a comma-separated list of the `working_memory`
ids each junk row tried to summarize. Those sources are your salvage material;
the junk text itself is unrecoverable. Collect the junk `id`s (JUNK_IDS) and the
union of `summary_of` ids (SOURCE_IDS).

## Phase 1 — Back up first, always
```
cd $HERMES_HOME/mnemosyne/data
cp mnemosyne.db mnemosyne.db.bak-$(date +%Y%m%d-%H%M%S)
```

## Phase 2 — Confirm the summarizer backend is healthy
Re-consolidating against a broken/tiny backend just regenerates junk. Read the
configured backend from `.env` (never hardcode the model), then verify it
returns a clean, COMPLETE summary (no `<think>`):
```
source <(grep -E '^MNEMOSYNE_LLM_(BASE_URL|MODEL)=' ~/.hermes/.env)
curl -sS "$MNEMOSYNE_LLM_BASE_URL/models"
curl -sS "$MNEMOSYNE_LLM_BASE_URL/chat/completions" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MNEMOSYNE_LLM_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Summarize in one sentence: The cat sat on the mat.\"}],\"max_tokens\":4096,\"temperature\":0.3}"
```
The response must be a finished sentence, not a reasoning fragment.

## Phase 3 — Delete the junk episodic rows (raw, inside the venv)
`vec_episodes` is a `vec0` virtual table — you MUST run inside the Hermes venv so
`sqlite_vec` loads, or the delete half-applies then rolls back. Delete
`vec_episodes` by rowid FIRST, then `episodic_memory` by id. FTS auto-syncs via
trigger.
```
# /home/videau-ai/.hermes/hermes-agent/venv/bin/python this.py
import sqlite3, sqlite_vec
DB="/home/videau-ai/.hermes/mnemosyne/data/mnemosyne.db"
JUNK_IDS=[...]  # from Phase 0
conn=sqlite3.connect(DB, timeout=30); conn.execute("PRAGMA busy_timeout=30000")
conn.enable_load_extension(True); sqlite_vec.load(conn); conn.enable_load_extension(False)
cur=conn.cursor()
for jid in JUNK_IDS:
    row=cur.execute("SELECT rowid FROM episodic_memory WHERE id=?", (jid,)).fetchone()
    if not row: continue
    rowid=row[0]
    cur.execute("DELETE FROM vec_episodes WHERE rowid=?", (rowid,))
    cur.execute("DELETE FROM episodic_memory WHERE id=?", (jid,))
conn.commit()
```

## Phase 4 — CRITICAL: un-flag the source rows
When sleep created the junk summary it stamped every source row with
`consolidated_at=<ts>`. Deleting the junk summary does NOT clear that, and
`sleep(force=True)` will still SKIP those rows — because **`force=True` only
bypasses the AGE cutoff, NOT the `consolidated_at IS NULL` eligibility
predicate** (beam.py:8056-8058). Without this step the sources are stranded:
real facts permanently denied a summary. Reset the flag on exactly the
`summary_of` ids (same venv process/connection as Phase 3):
```
SOURCE_IDS=[...]  # union of summary_of across all junk rows
ph=",".join("?"*len(SOURCE_IDS))
cur.execute(f"UPDATE working_memory SET consolidated_at=NULL WHERE id IN ({ph})", SOURCE_IDS)
conn.commit()
```

## Phase 5 — Re-consolidate with an adequate token budget
Set the budget BEFORE re-running or you regenerate the leak. In `~/.hermes/.env`:
```
MNEMOSYNE_LLM_MAX_TOKENS=16384   # default 2048 truncates reasoning models mid-<think>
MNEMOSYNE_LLM_TIMEOUT=300
```
These are read at module-IMPORT time (local_llm.py L24/L36-39), so a fresh
process that loads `.env` picks them up, but **the running gateway needs a
restart**. NOTE: Hermes loads `.env` in-app via `load_hermes_dotenv`
(override=True) — the value will NOT show in `/proc/<pid>/environ`; verify via
`os.environ` after the loader runs (or a fresh `python -c "import
mnemosyne.core.local_llm as L; print(L.LLM_MAX_TOKENS)"`), NOT /proc. Gateway
restart (uid 1001):
```
export XDG_RUNTIME_DIR=/run/user/1001
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus
systemctl --user daemon-reload && systemctl --user restart hermes-gateway.service
```
Then re-consolidate (span all sessions; force bypasses the age gate — Phase 4
already cleared the eligibility flag). Prefer the tool
`mnemosyne_sleep(all_sessions=True, force=True)`; API equivalent:
```
import mnemosyne.core.memory as M
M.sleep_all_sessions(force=True)
```
This can take minutes per summary on a reasoning model — run it in the
background (terminal background=True, notify_on_complete) not a 600s-capped
foreground call.

## Phase 6 — Verify with ground truth, not the return value
```
import mnemosyne.core.memory as M
print(M.reclaim_orphans(dry_run=True)['candidates'])        # expect 0
s=M.get_stats()['beam']['episodic_memory']
print(s['total'], s['vectors'])                             # MUST be equal (no orphan vectors)
```
```
sqlite3 -readonly $HERMES_HOME/mnemosyne/data/mnemosyne.db \
 "SELECT count(*) FROM episodic_memory
  WHERE content LIKE '%<think>%' OR content LIKE '%The user wants a summary%';"  -- expect 0
```
Also spot-read a re-generated summary to confirm it's a clean finished sentence.

## Pitfalls
- Running Phase 3/4 with the raw `sqlite3` CLI (no sqlite_vec) → half-delete +
  rollback. Use the venv python.
- Skipping Phase 4 → junk gone but sources permanently stranded (silent data
  loss of real facts). This is the trap.
- Trusting `force=True` to re-pick already-consolidated rows → it won't; it only
  bypasses the age gate.
- Re-consolidating before fixing the token budget / confirming the backend →
  regenerates identical junk.
- Verifying the `.env` fix via `/proc/<pid>/environ` → blind to dotenv-loaded
  vars; check `os.environ` after the loader, or a fresh process.
- Foreground `sleep_all_sessions` on many rows → 600s timeout; run in background.
