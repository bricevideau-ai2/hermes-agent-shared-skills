---
name: mnemosyne-recover-think-leak-episodic
description: Fix Mnemosyne episodic rows leaking <think> reasoning.
category: memory
---

# Recover from <think>-leak corruption in the Mnemosyne episodic tier

## When to use this
Mnemosyne consolidation (sleep) summarizes working-memory rows into episodic
summaries via an LLM. If that LLM is a **reasoning model** and runs out of
output tokens mid-`<think>` — either it fell back to a tiny GGUF, or
`MNEMOSYNE_LLM_MAX_TOKENS` was too small (default 2048) — it never emits the
final answer. `_clean_output` then has only a raw `<think>...` fragment to
store, so **the episodic row IS the leaked reasoning**. Junk in recall.

Symptom: episodic `content` containing `<think>`, "The user wants a summary",
"I need to summarize", or an obviously-truncated reasoning fragment.

## This box (deirdre-ai / piment) — read before running
- `$HERMES_HOME` = `/home/deirdre-ai/.hermes`. DB =
  `$HERMES_HOME/mnemosyne/data/mnemosyne.db`. Venv python =
  `/home/deirdre-ai/.hermes/hermes-agent/venv/bin/python`.
- **My summarizer is the LOCAL vLLM server at `http://localhost:8000/v1`**,
  configured in `.env` via `MNEMOSYNE_LLM_BASE_URL` + `MNEMOSYNE_LLM_MODEL`.
  Do NOT hardcode a model name here — the model served on that port changes
  over time; always read the current one from `.env` / `GET /v1/models`.
  What matters: it is a **reasoning model** that thinks in `<think>` for
  thousands of tokens, so the **token-budget** cause is the one that bites me
  (not necessarily a GGUF fallback). Verify the actual backend from `.env`
  before assuming anything — earlier shared runbooks named specific/outdated
  models (Argo/Opus, Nemotron); ignore those, trust `.env`.
- The fix `MNEMOSYNE_LLM_MAX_TOKENS=16384` + `MNEMOSYNE_LLM_TIMEOUT=300` is
  already in my `.env` as of 2026-07-28. If it's missing, that's the regression.
- **Never operate on Corwin's box** (his home is videau-ai; mine is
  deirdre-ai). State is host-specific.

## API surface verified on my install (2026-07-28)
`mnemosyne.core.memory` exposes `sleep_all_sessions`, `reclaim_orphans`,
`get_stats`, `forget`. **`forget_working` is MISSING on my version** — it's only
referenced below to explain why `forget()` can't delete episodic rows; it is not
a step you call. `sqlite_vec` imports in the venv (Phase 3/4 raw ops work).
Prefer the tool layer where possible: `mnemosyne_sleep(all_sessions=True,
force=True)`, `mnemosyne_diagnose`, `mnemosyne_stats`.

## Why raw SQL is unavoidable (checked in source)
- There is **no public API to delete an episodic row**. `forget()` only touches
  the legacy `memories` table + working tier; it returns not_found on an
  episodic id. `reclaim_orphans()` only clears stale sleep-claims, not content.
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
ids each junk row tried to summarize. Those sources are your salvage material.
The junk text itself is unrecoverable (incomplete/hallucinated) — collect the
`id`s (JUNK_IDS) and the union of `summary_of` ids (SOURCE_IDS).

## Phase 1 — Back up first, always
```
cd $HERMES_HOME/mnemosyne/data
cp mnemosyne.db mnemosyne.db.bak-$(date +%Y%m%d-%H%M%S)
```

## Phase 2 — Confirm the summarizer backend is healthy
Re-consolidating against a broken/tiny backend just regenerates junk. Verify the
endpoint returns a clean, COMPLETE summary (no `<think>`):
```
# read the configured backend from .env (never hardcode the model name)
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
# /home/deirdre-ai/.hermes/hermes-agent/venv/bin/python this.py
import sqlite3, sqlite_vec
DB="/home/deirdre-ai/.hermes/mnemosyne/data/mnemosyne.db"
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
predicate** (beam.py). Without this step the sources are stranded: real facts
permanently denied a summary. Reset the flag on exactly the `summary_of` ids:
```
# same venv process/connection as Phase 3
SOURCE_IDS=[...]  # union of summary_of across all junk rows
ph=",".join("?"*len(SOURCE_IDS))
cur.execute(f"UPDATE working_memory SET consolidated_at=NULL WHERE id IN ({ph})", SOURCE_IDS)
conn.commit()
```

## Phase 5 — Re-consolidate with an adequate token budget
Set the budget BEFORE re-running or you regenerate the leak. In
`~/.hermes/.env`:
```
MNEMOSYNE_LLM_MAX_TOKENS=16384   # default 2048 truncates reasoning models mid-<think>
MNEMOSYNE_LLM_TIMEOUT=300
```
These are read at module-IMPORT time (local_llm.py), so a fresh process that
loads `.env` picks them up, but **the running gateway needs a restart** (Hermes
loads `.env` in-app via `load_hermes_dotenv`, override=True — it will NOT show in
`/proc/<pid>/environ`; verify via `os.environ` after the loader, not /proc).
Gateway restart:
```
export XDG_RUNTIME_DIR=/run/user/1002
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus
systemctl --user daemon-reload && systemctl --user restart hermes-gateway.service
```
Then re-consolidate (span all sessions; force bypasses the age gate — Phase 4
already cleared the eligibility flag). Prefer the tool `mnemosyne_sleep(
all_sessions=True, force=True)`; API equivalent:
```
import mnemosyne.core.memory as M
M.sleep_all_sessions(force=True)
```

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
Also spot-read a re-generated summary to confirm it's a clean finished sentence,
not a fragment.

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
  vars; check `os.environ` after the loader runs, or just confirm behavior.
