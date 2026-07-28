---
name: mnemosyne-hermes-recall-troubleshooting
description: Diagnose and fix the case where the in-process mnemosyne_recall tool returns 0 results even though memories exist and the mnemosyne CLI recalls them fine. Covers the two cross-session mechanisms (global scope vs MNEMOSYNE_CROSS_SESSION override) and cleaning up a redundant system-python3 --user mnemosyne install so the Hermes venv is the single source of truth.
version: 1.1.0
---

# Mnemosyne + Hermes recall troubleshooting

## When to use
- `mnemosyne_recall` (the in-process Hermes tool) returns `count: 0` / empty results, but:
  - `mnemosyne_stats` shows working memories exist (e.g. working.total = 9), and
  - the `mnemosyne` CLI (`mnemosyne recall "<term>"`) DOES return those memories.
- Symptom is "restarted, integration should be good" but recall inside the agent still finds nothing.
- You suspect a stray `pip install --user` mnemosyne (system python3) is conflicting with the venv install.

## Root cause (the important insight)
Hermes binds each live BeamMemory to the LIVE session id (e.g. `hermes_YYYYMMDD_hhmmss_xxx`).
`BeamMemory.recall()` scopes results with a WHERE clause built by `_session_scope_filter()`
(`mnemosyne/core/beam.py` ~L413-423, source-verified 2026-07-28):

```
if _cross_session_enabled():        # MNEMOSYNE_CROSS_SESSION=1
    return "(1=1)"                  # no filtering — every session sees everything
return "(session_id = ? OR scope = 'global')"   # DEFAULT
```

So there are **two independent mechanisms**, and the primary one needs NO env var:

1. **`scope='global'` is cross-session BY DESIGN.** The default filter already contains
   `OR scope = 'global'`, so any row stored at global scope is recalled from every session
   with the env var OFF. This is the INTENDED per-fact durability path. Store durable facts at
   global scope (agent path: `memory.mnemosyne.default_scope: global` in `~/.hermes/config.yaml`)
   and they persist across sessions with zero env-var wiring.
2. **`MNEMOSYNE_CROSS_SESSION=1` is the BROADER OVERRIDE**, not "the fix." It replaces the whole
   filter with `(1=1)`. Its ONLY additional effect over mechanism (1) is surfacing **legacy
   session-scoped** rows (rows written before global-scope was adopted). It's read at IMPORT time
   (`_CROSS_SESSION = os.environ.get("MNEMOSYNE_CROSS_SESSION","0")=="1"`, ~L405), so a running
   Hermes needs a RESTART to pick it up.

If new durable facts recall fine cross-session, mechanism (1) is doing its job — reach for the
env var only when you also need OLD session-scoped rows to reappear.

**Config-key landmines (verified):**
- `mnemosyne config set default_scope global` writes Mnemosyne's OWN yaml, which NO write path
  reads — a no-op for the agent. The agent path reads `memory.mnemosyne.default_scope` from Hermes
  `config.yaml` via `read_hermes_config_key()`.
- A `cross_session` CONFIG key is a no-op for recall: beam.py reads only `os.environ` at import,
  never the config resolver. Only the env var flips the `(1=1)` override.

## Diagnosis steps
1. `mnemosyne_stats` → note working.total and the current session_id.
2. `mnemosyne_recall(query="<known term>")` → if 0 results, continue.
3. Confirm data is really there and its scope/session:
   ```bash
   DB=~/.hermes/mnemosyne/data/mnemosyne.db
   sqlite3 "$DB" "SELECT DISTINCT session_id, count(*) FROM working_memory GROUP BY session_id;"
   sqlite3 "$DB" "SELECT id, substr(content,1,60), scope FROM working_memory LIMIT 20;"
   ```
   Expect rows with `session_id='default'`, `scope='global'`.
4. Prove CLI works but in-process session id doesn't (decisive test), using the VENV python:
   ```bash
   VPY=~/.hermes/hermes-agent/venv/bin/python
   $VPY - <<'PY'
   from pathlib import Path
   from mnemosyne.core.beam import BeamMemory
   DB="/home/USER/.hermes/mnemosyne/data/mnemosyne.db"
   for sid in ["default","hermes_LIVE_SESSION_ID","random"]:
       b=BeamMemory(session_id=sid, db_path=Path(DB))
       print(sid, "->", len(b.recall("<term>", top_k=5)))
       b.conn.close()
   PY
   ```
   'default' returns N, the live session id returns 0 → confirms the rows are session-scoped
   (NOT global). This is expected default filtering, not a bug: the fix is to store durable facts at
   `scope='global'` (they'll then recall from any session), or enable the broader override below.
5. Confirm the fix works:
   ```bash
   MNEMOSYNE_CROSS_SESSION=1 $VPY - <<'PY'  # same script, live session id now returns N
   PY
   ```

## Fix
**Primary fix — make durable facts global-scoped (no env var, no restart-timing games):**
Set the agent default scope in Hermes config so new durable writes land at `scope='global'`:
```
# ~/.hermes/config.yaml
memory:
  mnemosyne:
    default_scope: global
```
Global rows recall across every session via the default filter. This is the sanctioned path and
persists in config.yaml. (Existing session-scoped rows can be individually re-stored at global scope
or surfaced via the override below.)

**Broader override — MNEMOSYNE_CROSS_SESSION=1 (only if you also need legacy session-scoped rows):**
Put it in `~/.hermes/.env` (verified 2026-07-28: the gateway's `run.py` calls `load_hermes_dotenv()`
at startup with `override=True`, BEFORE the Mnemosyne bridge initializes, so `.env` MNEMOSYNE_* vars
ARE loaded in time; this box runs all its MNEMOSYNE_* config from `.env`):
```
MNEMOSYNE_CROSS_SESSION=1
```
Then restart the gateway (`systemctl --user restart hermes-gateway`) — it's read at import time, so a
running process won't see it until restart.
- Verification GOTCHA: do NOT check `.env`-loaded vars via `/proc/<pid>/environ`. `.env` mutates
  `os.environ` at RUNTIME and is invisible to `/proc` (that snapshot is exec-time only) — you'll get a
  false "missing." `/proc` IS correct for systemd `Environment=` drop-in vars, just not `.env` ones.

## Cleanup: redundant system-python3 --user mnemosyne install
The Hermes venv (py3.11) already ships the full CLI suite AND the runtime provider:
`~/.hermes/hermes-agent/venv/bin/{mnemosyne,mnemosyne-auto-save,mnemosyne-browser,mnemosyne-hermes,...}`.
A separate `pip install --user` under system python3 (py3.12) is redundant and its
`~/.local/bin/mnemosyne` (shebang `/usr/bin/python3`) SHADOWS the venv CLI on PATH.

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
4. Uninstall (PEP 668 blocks plain uninstall; --break-system-packages here only touches ~/.local,
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

## Pitfalls
- `~/.hermes/.env` DOES load MNEMOSYNE_* env vars (gateway `run.py` → `load_hermes_dotenv(override=True)`
  at startup, before the bridge inits). It also holds secrets — keep credentials and MNEMOSYNE_* config
  together there; no systemd drop-in needed.
- `MNEMOSYNE_CROSS_SESSION` is read at import time → always requires a Hermes restart to take effect.
- The CLI "working" is a false comfort — it queries as default/unbound; always test the LIVE session id.
- `scope='global'` rows recall cross-session with the env var OFF (default filter includes
  `OR scope='global'`) — so a "0 durable facts" symptom means the rows are session-scoped, NOT that the
  global path is broken. Prefer storing durable facts at global scope over relying on the override.
- `--break-system-packages` is safe ONLY for undoing prior `--user` installs; never use it to modify
  actual OS `/usr/lib/python3/dist-packages`.
- After removal, venv/bin is usually NOT on the interactive PATH, so a wrapper in ~/.local/bin is needed
  to keep the bare `mnemosyne` command working.
