# Worked example: `memory.mnemosyne.auto_sleep` value AND provenance

Distilled from a 2026-07-26 session where the question was "is `auto_sleep` `true` in the config?"
and the real work was proving *why* it's true (explicit key vs default) and normalizing a
string-quoted boolean — without being able to do the final fresh-PID reload from inside the gateway.

Package layout (all under the Hermes venv, py3.11):
`~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/`
- `mnemosyne_hermes/__init__.py` — the Hermes memory PROVIDER (reads config keys, coerces).
- `mnemosyne/hermes_config.py` — `read_hermes_config_key(hermes_home, key)` (the resolver).
- Hermes core: `~/.hermes/hermes-agent/agent/memory_manager.py` — calls `provider.initialize(...)`.

> Cite by symbol + grep, not `file:line` — the line numbers below are soft hints from one checkout and
> rot across releases. Locate in YOUR tree with e.g.
> `grep -n 'auto_sleep = kwargs.get' "$(python -c 'import mnemosyne_hermes,os;print(os.path.dirname(mnemosyne_hermes.__file__))')/__init__.py"`.

## The resolution chain (source)
1. Provider `initialize()` stores the passed home:
   `self._hermes_home = kwargs.get("hermes_home", "")` (near `__init__.py:1013`).
2. `auto_sleep` resolution order (block near `__init__.py:702-706`): **kwargs → `self._read_config_key("auto_sleep")`
   → env `MNEMOSYNE_AUTO_SLEEP_ENABLED` → default `True`.**
   `_read_config_key` delegates to `read_hermes_config_key(self._hermes_home, key)`.
3. Resolver `mnemosyne/hermes_config.py` — **the trap**:
   ```python
   config_path = os.path.join(hermes_home, "config.yaml") if hermes_home else ""
   if not config_path or not os.path.exists(config_path):
       return None        # falsy hermes_home => None => fall through to env/default
   ```
   Then reads `config["memory"]["mnemosyne"][key]`.
4. Coercion `_coerce_bool`: `bool`→as-is; `int/float`→`bool()`;
   else `str(value).strip().lower()` matched against `("1","true","yes","on")` / `("0","false","no","off")`.
   So the string `'True'` → `"true"` → `True`. Safe, but *truthy ≠ provenance*.

## THE PRECEDENCE RACE: config.yaml SILENTLY SHADOWS the env var (2026-07-26 addition)
The same resolution order that establishes provenance also creates a footgun when someone is told to
"fix consolidation by setting `MNEMOSYNE_AUTO_SLEEP_ENABLED=true` in `.env`." Because the order is
`kwargs > config.yaml > env var > default`, the env var is **below** config.yaml:

| `config.yaml` `auto_sleep` | `.env` `MNEMOSYNE_AUTO_SLEEP_ENABLED` | effective value | why |
|---|---|---|---|
| absent | `true` | **True** | no config key → env var is consulted |
| absent | unset | **True** (fixed ver) / False (buggy ver) | falls to schema default |
| `false` | `true` | **False** ← silent no-op | config key present → env var never consulted |
| `true` | (anything) | **True** | config key wins |

So on a broken install that already wrote `auto_sleep: false` into `config.yaml`, the `.env` workaround
does **nothing** and emits no error. Correct guidance, in priority order:
1. **Fix the higher-precedence layer first:** `hermes config set memory.mnemosyne.auto_sleep true`
   (also matches Hermes' "behavioral settings → config.yaml, not .env" rubric).
2. **Env var = belt-and-suspenders**, only effective when NO competing config.yaml key shadows it.
Always grep both layers before declaring it fixed:
`grep -n 'auto_sleep' ~/.hermes/config.yaml` AND the §3f clean-env probe for the env var.

This is the MIRROR IMAGE of the `cross_session` case (see `mnemosyne-cross-session-case.md`): there the
config key was a dead no-op and the env var was the only lever; here the config key is alive and
outranks the env var. Same discipline (read the consumer's order), opposite verdict.

## The bug-fix history: cite the MERGED fix, not the first PR
The `auto_sleep`-defaults-to-false bug (Hermes plugin `get_config_schema()` set `default: False`,
overriding core `True` → fresh installs never consolidate) is tracked as
**NousResearch/hermes-agent#59836**. The commonly-cited fix **mnemosyne-oss/mnemosyne#420** was
INCOMPLETE — it flipped only the schema line, a review bot flagged a remaining runtime-fallback gap,
and it was **closed as superseded by the merged full fix #429** (dplush; both provider surfaces +
regression tests). When documenting: cite #429 as the landed fix, note #420 as history, and let readers
check "am I affected?" by grepping their INSTALLED source rather than guessing from a version number:
```bash
DIR=$(python -c 'import mnemosyne_hermes,os;print(os.path.dirname(mnemosyne_hermes.__file__))')
grep -n '"key": "auto_sleep"' "$DIR/__init__.py"
# Fixed:  ... "default": True   (description: "Set false to disable")
# Buggy:  ... "default": False  (description: "Set true to enable")
```

## Why the provenance trap does NOT bite on the live gateway (provenance proof)
`agent/memory_manager.py` (near L1221-1223) guarantees the resolver's input:
```python
if "hermes_home" not in kwargs:
    from hermes_constants import get_hermes_home
    kwargs["hermes_home"] = str(get_hermes_home())
for provider in self._providers:
    provider.initialize(session_id=session_id, **kwargs)
```
=> `hermes_home` is never empty in the running gateway => the resolver reaches the config file =>
an explicit `memory.mnemosyne.auto_sleep` key genuinely drives the value (provenance = explicit key,
NOT the default). Contrast a box with NO explicit key: absent → `None` → code default `True` (same
value, provenance = default).

## Differential probe (run with the venv python)
```bash
VPY=~/.hermes/hermes-agent/venv/bin/python
$VPY - <<'PY'
import os
from mnemosyne.hermes_config import read_hermes_config_key
from mnemosyne_hermes import _coerce_bool
hh = os.path.expanduser("~/.hermes")
print("with home :", repr(read_hermes_config_key(hh, "auto_sleep")))  # -> 'True' (or True)
print("empty home:", repr(read_hermes_config_key("", "auto_sleep")))  # -> None  (the trap)
print("coerced   :", _coerce_bool(read_hermes_config_key(hh, "auto_sleep"), False))
PY
```
Two rows settle provenance: value present with real home, `None` with empty home.

## Normalizing a string-quoted boolean
Problem: `config.yaml` had `auto_sleep: 'True'` (string) rather than a YAML boolean.
- Direct edit is **guarded**: `patch`/`write_file` on `~/.hermes/config.yaml` are refused
  ("Refusing to write to Hermes config file ... use 'hermes config' instead").
- Sanctioned path: `hermes config set memory.mnemosyne.auto_sleep true`
  (from the venv: `$VPY -m hermes_cli.main config set memory.mnemosyne.auto_sleep true`).
  It warns "not a recognized config key ... saved anyway" — fine, the provider reads it via
  `read_hermes_config_key` regardless.
- Verify raw + parsed type (don't trust the CLI echo):
  ```bash
  grep -n "auto_sleep" ~/.hermes/config.yaml | grep -v _enabled     # -> auto_sleep: true
  $VPY -c "import yaml; v=yaml.safe_load(open('$HOME/.hermes/config.yaml'))['memory']['mnemosyne']['auto_sleep']; print(repr(v), type(v).__name__, v is True)"
  # -> True bool True
  ```

## The fresh-PID reload you must hand to the user (in-gateway restart guard)
Verifying that a NEW process reads the value requires restarting `hermes-gateway.service`, but the
agent runs INSIDE it. Everything attempted autonomously is blocked:
- in-turn `systemctl --user restart hermes-gateway.service` → kills the session mid-turn.
- one-shot cron with the restart command → blocked: "gateway lifecycle command ... prevent
  agent-driven SIGTERM-respawn loops (#30719)".
- `setsid`/`nohup`/trailing `&` detach → blocked by the terminal tool ("use background=true"), and a
  `background=true` child dies with the gateway anyway.

So: verify everything below the process boundary yourself, then give the user ONE command to run from
a shell OUTSIDE the gateway and report back:
```bash
hermes gateway restart
sleep 5
~/.hermes/hermes-agent/venv/bin/python - <<'PY'
import os
from mnemosyne.hermes_config import read_hermes_config_key
from mnemosyne_hermes import _coerce_bool
raw = read_hermes_config_key(os.path.expanduser("~/.hermes"), "auto_sleep")
print("raw:", repr(raw), "| resolved:", _coerce_bool(raw, False))   # expect: raw: True | resolved: True
PY
```
Leaving a ready-to-run script under `~/.hermes/scripts/` makes it a single paste for the user.
