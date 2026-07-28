# Worked example: Mnemosyne cross-session recall (2026-07-25)

The case this skill was distilled from. Package: `mnemosyne-memory==3.14.0`.

## Question asked
"Is `MNEMOSYNE_CROSS_SESSION=1` the right way to get cross-session recall, or just a workaround?"

## Verdict (proven)
- **`default_scope: global` is the PRIMARY, sanctioned mechanism.** The recall SQL filter is
  `(session_id = ? OR scope = 'global')`, so a `global`-scoped row is visible across all sessions
  with NO env var, and it persists in config.yaml.
- **`MNEMOSYNE_CROSS_SESSION=1` is the BROADER override.** It replaces the filter with `(1=1)`; its
  only *additional* effect is exposing legacy `session`-scoped rows.
- **The `cross_session` CONFIG KEY is a silent no-op for recall** — a real upstream bug. It's env-var-only.

## Source evidence (paths under the installed package `mnemosyne/core/`)
- `config.py`: `ENV_VAR_MAP` maps `"cross_session" -> "MNEMOSYNE_CROSS_SESSION"`; `DEFAULTS` has
  `cross_session: False`. Seeded config.yaml header states `# Precedence: config.yaml > env vars >
  hardcoded defaults`. There is even a `mnemosyne config migrate` (export env vars into config.yaml).
- `beam.py` (~line 401-423) — the CONSUMER bypasses the resolver:
  ```python
  _CROSS_SESSION = os.environ.get("MNEMOSYNE_CROSS_SESSION", "0") == "1"   # import-time, os.environ only
  def _cross_session_enabled() -> bool:
      return _CROSS_SESSION or os.environ.get("MNEMOSYNE_CROSS_SESSION", "0") == "1"
  def _session_scope_filter(...):
      if _cross_session_enabled(): return "(1=1)"
      return "(session_id = ? OR scope = 'global')"
  ```
- `profiles.py` (~line 531) "Rule 3": `cross_session=1` with `default_scope='session'` is flagged a
  CONFIG ERROR — "cross-session visibility requires global scope". Confirms the two are meant to pair,
  with global scope as the base.

## Decisive resolver-vs-consumer probe
```python
import mnemosyne.core.config as c
from mnemosyne.core import beam
c.get_config().set("cross_session", True)
print(c.get_config().get("cross_session"))     # -> True   (resolver sees it)
print(beam._cross_session_enabled())            # -> False  (consumer never consulted resolver) == BUG
```

## Clean-room differential truth table
Real API: `from mnemosyne.core.beam import BeamMemory` (NOT `BEAM`). `session_id` is set at
CONSTRUCTION: `BeamMemory(session_id=..., db_path=...)`; `.remember(content, scope='global'|'session',
importance=...)`; `.recall(query, top_k=...)` -> `List[Dict]`. Isolate with a tempdir + `MNEMOSYNE_HOME`
/`MNEMOSYNE_DATA_DIR`, `rm -rf` before/after. Write in session A, recall from a *different* session B.

| Stored scope | env UNSET | `MNEMOSYNE_CROSS_SESSION=1` |
|---|---|---|
| `global`  | RECALLED cross-session | RECALLED |
| `session` | NOT recalled           | RECALLED |

## Real commands verified
- `mnemosyne config set default_scope global`  -> `Set default_scope = global` (round-trips via
  `mnemosyne config get default_scope` -> `default_scope = global`).
- `mnemosyne config <reload|get|set|migrate>` are the real config subcommands.

## Fix applied
- Reversed the tutorial's §9.3 (and companion §3b/§3c): global-scope PRIMARY, env var as the
  legacy-row override, with an explicit "config key is a no-op" callout.
- Migrated the running box from `default_scope: session` (+ forced env var) to `default_scope:
  global`; verified a fresh canary recalls and the DB shows new stores landing at `scope=global`.
- Upstream bug report drafted for `AxDSan/mnemosyne` (canonical repo per PyPI project_urls). Filing
  blocked: fine-grained PAT for a non-owned repo lacks Issues:write -> `Resource not accessible by
  personal access token (createIssue)`. Report handed to the repo owner instead.
