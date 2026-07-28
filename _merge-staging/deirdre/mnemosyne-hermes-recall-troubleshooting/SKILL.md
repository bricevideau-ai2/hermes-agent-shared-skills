---
name: mnemosyne-hermes-recall-troubleshooting
description: Lean diagnostic for when mnemosyne_recall returns 0 results in a session even though mnemosyne_stats shows memories exist. Mostly historical now (config default_scope=global resolved the cross-session bug); use only if recall regresses.
---

# Mnemosyne + Hermes recall troubleshooting (diagnostic only)

## STATUS (2026-07-27) — mostly historical
On the current install, cross-session recall WORKS WITHOUT any env-var or DB hack:
`config.yaml` sets `memory.mnemosyne.default_scope: global`, so durable rows are written
`scope='global'` and recall from any session. VERIFIED: a fresh live-session id recalls
the same rows as `default` with `MNEMOSYNE_CROSS_SESSION` UNSET. The redundant
system-python `--user` mnemosyne install is GONE and `~/.local/bin/mnemosyne` is already
a clean wrapper around the venv binary. So the "fix" and "cleanup" procedures this skill
used to contain are DONE — they are not repeated here. Keep this skill only as a
diagnostic if recall ever regresses.

## When to use
- `mnemosyne_recall` returns `count: 0` but `mnemosyne_stats` shows memories exist.
- Recall inside a live agent session finds nothing that the `mnemosyne` CLI can find.

## Root cause (historical, for understanding)
Hermes binds each live BeamMemory to the LIVE session id. `recall()` scopes to that
session id; durable facts stored with `session_id='default'` are only rescued across
sessions when they are `scope='global'` (or when `MNEMOSYNE_CROSS_SESSION=1` rewrites
the filter to `(1=1)`). The DESIGNED fix is `default_scope: global` in config — every
durable write is global, so a new session sees them. The env var is the old
broken-install-era workaround and is no longer needed.

## Diagnosis steps (read-only — never write the DB)
1. `mnemosyne_stats` -> note working.total and the current session_id.
2. `mnemosyne_recall(query="<known term>")` -> if 0 results, continue.
3. Confirm scope config: `hermes config get memory.mnemosyne.default_scope` should be
   `global`. If not, set it with `hermes config set memory.mnemosyne.default_scope global`
   (never hand-edit config.yaml) and restart the gateway/session.
4. Read-only DB inspection to see how rows are scoped (SELECT only, no UPDATE/DELETE):
   ```bash
   DB=~/.hermes/mnemosyne/data/mnemosyne.db
   sqlite3 "$DB" "SELECT scope, count(*) FROM working_memory GROUP BY scope;"
   ```
   Expect the durable rows to be `scope='global'`. If they are `session`, they were
   stored before default_scope was set — RE-STORE them via `mnemosyne_remember`
   (which honors default_scope). Do NOT `UPDATE` the DB.
5. Prove the scoping behaviorally with the VENV python (read-only recall):
   ```bash
   VPY=~/.hermes/hermes-agent/venv/bin/python
   $VPY - <<'PY'
   from pathlib import Path
   from mnemosyne.core.beam import BeamMemory
   DB="/home/USER/.hermes/mnemosyne/data/mnemosyne.db"
   for sid in ["default","hermes_LIVE_SESSION_probe"]:
       b=BeamMemory(session_id=sid, db_path=Path(DB))
       print(sid, "->", len(b.recall("<term>", top_k=5))); b.conn.close()
   PY
   ```
   Both should return N. If the live id returns 0 while `default` returns N, scope
   is wrong -> fix via config + re-store, not via DB edit.

## Hard rules
- NEVER write the mnemosyne DB directly (no `sqlite3 UPDATE/DELETE`, no ad-hoc
  `cur.execute` DELETE). Use the tool primitives: `mnemosyne_remember` (store/re-store,
  honors default_scope), `mnemosyne_invalidate` (retire/supersede, soft tombstone),
  `mnemosyne_forget` (hard delete when truly required).
- Fix scope via `config.yaml` `default_scope: global` (through `hermes config set`),
  not by promoting rows in SQL.
- `~/.hermes/.env` is the credential store, not a process dotenv — do not put
  MNEMOSYNE_* vars there.

## Related
- `mnemosyne-memory-override` (upstream policy: durable -> Mnemosyne, never legacy memory tool)
- `mnemosyne-troubleshooting` (consolidation / learning-loop health)
- `mnemosyne-cli-reference` (CLI HOW)
