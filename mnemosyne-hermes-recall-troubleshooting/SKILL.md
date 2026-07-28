---
name: mnemosyne-hermes-recall-troubleshooting
description: Diagnose and fix the case where the in-process mnemosyne_recall tool returns 0 results in a session even though mnemosyne_stats / the mnemosyne CLI show the memories exist. Covers the two cross-session mechanisms (global scope vs MNEMOSYNE_CROSS_SESSION override), read-only DB inspection, and cleanup of a redundant system-python --user mnemosyne install. Mostly historical on a correctly-configured box; use if recall regresses.
version: 2.0.0
metadata:
  hermes:
    tags: [mnemosyne, memory, recall, cross-session, troubleshooting, debugging]
    related_skills: [mnemosyne-troubleshooting, verifying-config-mechanism-vs-source]
---

# Mnemosyne + Hermes recall troubleshooting

## STATUS — mostly historical on a correctly-configured box
On a correctly-configured install, cross-session recall WORKS WITHOUT any env-var or DB hack:
`config.yaml` sets `memory.mnemosyne.default_scope: global`, so durable rows are written
`scope='global'` and recall from any session. VERIFIED: a fresh live-session id recalls the same
rows as `default` with `MNEMOSYNE_CROSS_SESSION` UNSET. If your box is in that state, the "fix" and
"cleanup" sections below are already DONE — keep this skill only as a diagnostic if recall regresses.
Read the root-cause section to understand WHY, then jump to Diagnosis only if you actually see 0 results.

## When to use
- `mnemosyne_recall` (the in-process Hermes tool) returns `count: 0` / empty results, but:
  - `mnemosyne_stats` shows working memories exist (e.g. working.total = 9), and
  - the `mnemosyne` CLI (`mnemosyne recall "<term>"`) DOES return those memories.
- Symptom is "restarted, integration should be good" but recall inside the agent still finds nothing.
- You suspect a stray `pip install --user` mnemosyne (system python3) is conflicting with the venv install.

## Root cause (the important insight)
Hermes binds each live BeamMemory to the LIVE session id (e.g. `hermes_YYYYMMDD_hhmmss_xxx`).
`BeamMemory.recall()` scopes results with a WHERE clause built by `_session_scope_filter()`. Locate it
in YOUR tree (line numbers drift across releases):
`grep -n "_session_scope_filter\|_cross_session_enabled" $(python3 -c "import mnemosyne.core.beam as b; print(b.__file__)")`.
The logic (source-verified 2026-07-28):

```
if _cross_session_enabled():        # MNEMOSYNE_CROSS_SESSION=1
    return "(1=1)"                  # no filtering — every session sees everything
return "(session_id = ? OR scope = 'global')"   # DEFAULT
```

So there are **two independent mechanisms**, and the primary one needs NO env var:

1. **`scope='global'` is cross-session BY DESIGN.** The default filter already contains
   `OR scope = 'global'`, so any row stored at global scope is recalled from every session
   with the env var OFF. This is the INTENDED per-fact durability path. Store durable facts at
   global scope (agent path: `memory.mnemosyne.default_scope: global` in `config.yaml`) and they
   persist across sessions with zero env-var wiring.
2. **`MNEMOSYNE_CROSS_SESSION=1` is the BROADER OVERRIDE**, not "the fix." It replaces the whole
   filter with `(1=1)`. Its ONLY additional effect over mechanism (1) is surfacing **legacy
   session-scoped** rows (rows written before global scope was adopted). It's read at IMPORT time
   (`_CROSS_SESSION = os.environ.get("MNEMOSYNE_CROSS_SESSION","0")=="1"`), so a running Hermes needs
   a RESTART to pick it up.

If new durable facts recall fine cross-session, mechanism (1) is doing its job — reach for the env
var only when you also need OLD session-scoped rows to reappear.

**Config-key landmines (verified):**
- `mnemosyne config set default_scope global` writes Mnemosyne's OWN yaml, which NO agent write path
  reads — a no-op for the agent. The agent path reads `memory.mnemosyne.default_scope` from Hermes
  `config.yaml` via `read_hermes_config_key()`.
- A `cross_session` CONFIG key is a no-op for recall: beam.py reads only `os.environ` at import,
  never the config resolver. Only the env var flips the `(1=1)` override.

## Diagnosis steps (read-only — never write the DB)
1. `mnemosyne_stats` → note working.total and the current session_id.
2. `mnemosyne_recall(query="<known term>")` → if 0 results, continue.
3. Confirm scope config: `hermes config get memory.mnemosyne.default_scope` should be `global`.
   If not, set it with `hermes config set memory.mnemosyne.default_scope global` (never hand-edit
   config.yaml) and restart the gateway/session.
4. Read-only DB inspection to see how rows are scoped (SELECT only, no UPDATE/DELETE):
   ```bash
   DB=~/.hermes/mnemosyne/data/mnemosyne.db
   sqlite3 "$DB" "SELECT DISTINCT session_id, count(*) FROM working_memory GROUP BY session_id;"
   sqlite3 "$DB" "SELECT scope, count(*) FROM working_memory GROUP BY scope;"
   ```
   Expect the durable rows to be `scope='global'`. If they are `session`, they were stored before
   default_scope was set — RE-STORE them via `mnemosyne_remember` (which honors default_scope).
   Do NOT `UPDATE` the DB.
5. Prove the scoping behaviorally with the VENV python (read-only recall):
   ```bash
   VPY=~/.hermes/hermes-agent/venv/bin/python
   $VPY - <<'PY'
   from pathlib import Path
   from mnemosyne.core.beam import BeamMemory
   DB="/home/USER/.hermes/mnemosyne/data/mnemosyne.db"   # USER = your account
   for sid in ["default","hermes_LIVE_SESSION_ID","random"]:
       b=BeamMemory(session_id=sid, db_path=Path(DB))
       print(sid, "->", len(b.recall("<term>", top_k=5))); b.conn.close()
   PY
   ```
   'default' returns N, the live session id returns 0 → confirms the rows are session-scoped
   (NOT global). This is expected default filtering, not a bug: fix by storing durable facts at
   `scope='global'` (config, then re-store), NOT by editing the DB.
6. Only if you also need legacy session-scoped rows, confirm the override works before enabling it:
   ```bash
   MNEMOSYNE_CROSS_SESSION=1 $VPY - <<'PY'   # same script; live session id now returns N
   PY
   ```

## Fix
**Primary fix — make durable facts global-scoped (no env var, no restart-timing games):**
Set the agent default scope so new durable writes land at `scope='global'`, via the CLI (never
hand-edit config.yaml):
```
hermes config set memory.mnemosyne.default_scope global
```
which writes:
```
# ~/.hermes/config.yaml
memory:
  mnemosyne:
    default_scope: global
```
Global rows recall across every session via the default filter. This is the sanctioned path and
persists in config.yaml. Existing session-scoped rows can be re-stored at global scope, or surfaced
via the override below.

**Broader override — MNEMOSYNE_CROSS_SESSION=1 (only if you also need legacy session-scoped rows):**
It is read from the process environment at import time. Where it lives depends on your box:
- **Config-first (Hermes rubric):** behavioral toggles belong in `config.yaml`, and `.env` is
  nominally the secret store. Prefer the config-scope fix above; reach for the env var only for the
  legacy-rows case.
- **Actual loaded path (verified 2026-07-28):** `~/.hermes/.env` IS loaded into the gateway process —
  gateway `run.py` calls `load_hermes_dotenv(hermes_home=…)` at startup, which loads `~/.hermes/.env`
  with override BEFORE the Mnemosyne bridge initializes. On a box that already runs its MNEMOSYNE_*
  config from `.env` (a pre-existing arrangement), adding `MNEMOSYNE_CROSS_SESSION=1` there works and
  is load-bearing. Confirm what YOUR box uses: `grep -nE '^MNEMOSYNE_' ~/.hermes/.env` (redact values).
```
# ~/.hermes/.env  (if this box keeps MNEMOSYNE_* here)
MNEMOSYNE_CROSS_SESSION=1
```
Then restart the gateway (`systemctl --user restart hermes-gateway`) — it's read at import time, so a
running process won't see it until restart.
- Verification GOTCHA: do NOT check `.env`-loaded vars via `/proc/<pid>/environ`. `.env` mutates
  `os.environ` at RUNTIME and is invisible to `/proc` (that snapshot is exec-time only) — you'll get a
  false "missing." `/proc` IS correct for systemd `Environment=` drop-in vars, just not `.env` ones.
  To probe a `.env` var, replay the loader in a clean interpreter instead.

## Cleanup: redundant system-python3 --user mnemosyne install
The Hermes venv (py3.11) already ships the full CLI suite AND the runtime provider:
`~/.hermes/hermes-agent/venv/bin/{mnemosyne,mnemosyne-auto-save,mnemosyne-browser,mnemosyne-hermes,...}`.
A separate `pip install --user` under system python3 is redundant and its `~/.local/bin/mnemosyne`
(shebang `/usr/bin/python3`) SHADOWS the venv CLI on PATH.

Safe removal procedure:
1. Verify nothing depends on the --user install:
   ```bash
   crontab -l 2>/dev/null | grep -i mnemosyne          # expect none
   grep -rn "\.local/bin/mnemosyne\|mnemosyne-auto-save\|mnemosyne-browser" ~/.bashrc ~/.profile ~/.config ~/.hermes/config.yaml 2>/dev/null | grep -v site-packages
   ```
2. Prove the VENV CLI works against the same DB BEFORE removing anything:
   `~/.hermes/hermes-agent/venv/bin/mnemosyne recall "<term>"`
3. Backup + snapshot:
   ```bash
   BK=~/.hermes/mnemosyne-user-uninstall-backup-$(date +%Y%m%d_%H%M%S); mkdir -p "$BK"
   python3 -m pip freeze --user > "$BK/user-freeze.txt"
   ls -1 ~/.local/bin > "$BK/local-bin-before.txt"
   ```
4. Uninstall (PEP 668 blocks plain uninstall; `--break-system-packages` here only touches ~/.local,
   NOT OS dist-packages, because these were --user installs):
   ```bash
   awk -F'==' '{print $1}' "$BK/user-freeze.txt" > /tmp/pkgs.txt
   python3 -m pip uninstall -y --break-system-packages -r /tmp/pkgs.txt
   ```
   pip also removes the ~/.local/bin console-script wrappers automatically.
5. Restore a working `mnemosyne` command by wrapping the venv binary:
   ```bash
   cat > ~/.local/bin/mnemosyne <<'EOF'
   #!/usr/bin/env bash
   exec "/home/USER/.hermes/hermes-agent/venv/bin/mnemosyne" "$@"
   EOF
   chmod +x ~/.local/bin/mnemosyne
   ```
6. Verify: `python3 -c "import mnemosyne"` → ModuleNotFoundError (system clean);
   `mnemosyne recall "<term>"` → works via venv.

## Hard rules (never write the DB directly)
- NEVER write the mnemosyne DB directly (no `sqlite3 UPDATE/DELETE`, no ad-hoc `cur.execute` DELETE).
  Use the tool primitives: `mnemosyne_remember` (store/re-store, honors default_scope),
  `mnemosyne_invalidate` (retire/supersede, soft tombstone), `mnemosyne_forget` (hard delete when
  truly required).
- Fix scope via `config.yaml` `default_scope: global` (through `hermes config set`), not by promoting
  rows in SQL.
- The CLI "working" is a false comfort — it queries as default/unbound; always test the LIVE session id.

## Pitfalls
- `MNEMOSYNE_CROSS_SESSION` is read at import time → always requires a Hermes restart to take effect.
- `scope='global'` rows recall cross-session with the env var OFF (default filter includes
  `OR scope='global'`) — so a "0 durable facts" symptom means the rows are session-scoped, NOT that the
  global path is broken. Prefer storing durable facts at global scope over relying on the override.
- Don't state the env-var location as universal. Whether MNEMOSYNE_* lives in `config.yaml` vs `.env`
  is per-box: Hermes' rubric says behavioral config → config.yaml, but `.env` IS loaded at gateway
  startup and some boxes legitimately keep MNEMOSYNE_* there. Grep the box before advising.
- `--break-system-packages` is safe ONLY for undoing prior `--user` installs; never use it to modify
  actual OS `/usr/lib/python3/dist-packages`.
- After removal, venv/bin is usually NOT on the interactive PATH, so a wrapper in ~/.local/bin is needed
  to keep the bare `mnemosyne` command working.
- Cite source by SYMBOL + grep recipe, never `file:line` — beam.py line numbers drift across releases.

## Related
- `mnemosyne-troubleshooting` — consolidation / learning-loop health
- `verifying-config-mechanism-vs-source` — proving a config key is the sanctioned path vs a workaround
- `mnemosyne-memory-override` — upstream policy: durable facts → Mnemosyne, never the legacy memory tool
