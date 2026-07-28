---
name: verifying-config-mechanism-vs-source
description: "When you must state whether a config/env/setting is the SANCTIONED way or a workaround — or whether a documented precedence/behavior actually holds — verify against SOURCE and prove it with a clean-room differential truth table, not black-box behavior or self-report. Load when asked 'is X the right way or a workaround', when a doc claims a config precedence, when two settings seem to do the same thing, or before documenting a config mechanism as canonical."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [configuration, source-verification, documentation, truth-table, root-cause, debugging]
    related_skills: [systematic-debugging, test-driven-development]
---

# Verifying a Config Mechanism Against Its Source

## When to use
- Someone asks **"is this env var / config key the right way, or just a workaround?"**
- A doc, README, or config header **claims a precedence** ("config.yaml > env vars > defaults") and you're about to rely on or document it.
- **Two settings appear to do the same thing** (e.g. a config key AND an env var) and you need to know which is canonical and whether they're interchangeable.
- You're about to **write documentation** that names one mechanism as "primary" / "sanctioned" / "the fix".
- Black-box behavior "works" but you don't know *why* — and the wrong explanation would mislead the next reader.

## The core principle
**A setting that "works" is not evidence of HOW it works.** Behavior can be produced by a
different code path than the one the docs credit. The only way to state "X is the sanctioned
mechanism" vs "X is a workaround forced by a bug" is to (1) read the code path that consumes the
setting, and (2) prove the behavior with a differential experiment in a clean room. Never sign off
on "the right way" from black-box behavior, from a doc claim, or from another agent's self-report.

## Procedure

### 1. Find where the setting is DECLARED vs CONSUMED — they can differ
A setting can be a first-class config key in the config layer yet be read somewhere that bypasses
that layer entirely.
- Locate the declaration: config map / schema / defaults table (e.g. an `ENV_VAR_MAP`, a settings
  dataclass, a JSON schema). This tells you the *intended* surface and any stated precedence.
- Locate every CONSUMER: `search_files` for the key name AND its env-var alias across the source.
- **Compare.** If the consumer reads `os.environ[...]` / a raw file / a hardcoded default directly
  instead of going through the config resolver, the declared precedence DOES NOT APPLY to that
  setting — that's the classic "config key is a silent no-op" bug.

```bash
# find declaration and all consumers
search_files "SETTING_NAME"            # config key form
search_files "ENV_VAR_ALIAS"           # env var form
# read the actual consumer line, not just the map entry
```

Watch for **import-time capture**: `X = os.environ.get("...")` at module top-level is read ONCE at
import. Setting it later in-process, or via a config-reload, won't take effect — the process must be
restarted. This alone makes an env var "load-bearing" in a way a config key isn't.

### 2. Build a clean-room DIFFERENTIAL truth table
Prove behavior for each combination that matters, in a FRESH/throwaway store — never against your
already-provisioned box (its existing state hides under-specified steps).
- Isolate state: point data dirs at a tempdir; `rm -rf` before and after.
- Exercise the REAL API (discover class/method signatures first — don't guess names).
- Vary ONE axis at a time (setting on/off × scope A/scope B × env set/unset) and record a table.

Example shape (Mnemosyne cross-session recall, the case this skill was distilled from):

| Stored scope | no env var | `ENV_OVERRIDE=1` |
|---|---|---|
| `global`  | ✅ recalled cross-session | ✅ |
| `session` | ❌ not recalled            | ✅ |

The table instantly separates the two mechanisms: one setting (global scope) works alone via the
normal filter; the override only adds the second row. That's the difference between "primary
sanctioned path" and "broader belt-and-suspenders override".

### 3. Confirm the resolver-vs-consumer split directly
The decisive probe: set the config key the documented way, then read BOTH the resolver value and the
consumer's effective gate.

```python
cfg.set("setting", True)
print(cfg.get("setting"))          # -> True  (resolver sees it)
print(module._effective_gate())    # -> False (consumer never consulted the resolver) == BUG
```

If these disagree, the config key is a no-op and the env var (or whatever the consumer actually
reads) is the ONLY working lever — a workaround forced by a bug, not the design.

### 3b. Provenance ≠ presence: prove the key is REACHED, not just present
A config key can be present in the file AND hold the correct value, yet NOT be what drives the
effective setting — because the consumer never reaches it. "The explicit key is in the file" does not
prove "the explicit key is driving the value." To claim provenance you must show the read path
actually resolves the key.
- Trace the reader's guard conditions. Example (Hermes `memory.mnemosyne.<key>`): the resolver
  `read_hermes_config_key(hermes_home, key)` returns `None` the instant `hermes_home` is falsy
  (`config_path = os.path.join(hermes_home, "config.yaml") if hermes_home else ""` → `if not
  config_path or not exists: return None`). A `None` return silently falls through to env → default —
  so the value can be correct while its *provenance* is the default, not your key.
- Prove the input the reader depends on is actually supplied on the LIVE path, not just in tests.
  For that Hermes case the guarantee is `agent/memory_manager.py` (~L1221-1223): core injects
  `kwargs["hermes_home"] = str(get_hermes_home())` before every `provider.initialize()`, so
  `hermes_home` is never empty in the running gateway. THAT is what upgrades "key present" to "key
  drives the value."
- Differential confirmation: call the resolver with the real input vs the falsy input.
  `read_hermes_config_key(hermes_home, key)` → your value; `read_hermes_config_key("", key)` → `None`.
  Two rows, and provenance is settled.

### 3c. When the "fresh-PID reload" check can't be done from inside the process
The last rung of config verification — "a NEW process actually reads the new value" — is structurally
UNREACHABLE when you are running inside the very process you'd need to restart (e.g. the agent runs
inside `hermes-gateway.service` and the change is to that gateway's config).
- An in-turn `systemctl --user restart` kills your session mid-turn before you can read the result.
- Both autonomous fallbacks are hard-guarded against agent-driven restart loops: a one-shot cron with
  a gateway lifecycle command is **blocked** ("prevent SIGTERM-respawn loops #30719"), and shell-level
  detach (`setsid`/`nohup`/`&`) is **blocked** by the terminal tool.
- Correct move: verify everything BELOW the process boundary yourself (raw file value, parsed type,
  resolver return, coercion), then hand the user ONE out-of-gateway command to run and report back —
  `hermes gateway restart` from a shell outside the gateway, followed by a fresh-interpreter read of
  the resolved value. Leave a ready-to-run script (e.g. under `~/.hermes/scripts/`) so it's one paste.

### 4. State the verdict precisely, then act
- "**X is the sanctioned mechanism**" — only if the consumer reads it through the intended layer and
  the truth table shows it working alone.
- "**Y is a workaround forced by a bug**" — if Y works only because the consumer bypasses the
  intended layer / the canonical key is a no-op. File the upstream bug WITH the resolver-vs-consumer
  repro and a suggested fix (route the consumer through the resolver, falling back to env).
- **Fix your own docs AND your own box.** If you documented the workaround as primary, reverse it,
  and migrate your running config to the sanctioned path so you're not documenting-one-thing-running-
  another. Grep the docs for the old framing ("load-bearing", "the real fix") to catch every echo.

## Pitfalls
- **Don't trust the config header's stated precedence.** It's a promise the config layer makes; a
  consumer that bypasses the layer breaks it silently. Verify at the consumer.
- **Don't guess API names for the truth-table harness.** Introspect: `inspect.signature`,
  `dir(cls)`, find the real class (e.g. it was `BeamMemory`, not `BEAM`). A wrong signature wastes a
  round-trip and can produce a misleading "it doesn't work".
- **Don't run the truth table against your provisioned box.** Existing rows/scope/env mask the very
  under-specification you're testing for. Fresh store, isolated dirs, cleaned up after.
- **Self-report ≠ verification.** Another agent's "I ran it and it passed" (or your own from a prior
  turn) is a claim, not proof. For a crux fact both parties should reproduce independently on
  separate harnesses; convergence of two clean runs is the bar.
- **Filing upstream needs write scope.** A fine-grained PAT for repos you DON'T own typically lacks
  `Issues: write`; `gh issue create` fails with `Resource not accessible by personal access token
  (createIssue)`. Have the report ready as markdown and hand it to the repo owner rather than
  fabricating a filed issue.
- **Presence isn't provenance.** A correct value in the config file does NOT prove the key drives the
  behavior — verify the reader actually reaches it (see §3b). A quoted `'True'` coerces fine, but
  whether it's the *source* of the effective value is a separate claim from whether it's truthy.
- **Editing a guarded config file:** the agent cannot `patch`/`write_file` `~/.hermes/config.yaml`
  directly (security guard). Use `hermes config set <dotted.key> <value>`; it accepts unrecognized
  keys with a warning and writes a real YAML type (bare `true`, not the string `'True'`). Verify the
  raw line and the parsed type afterward — don't trust the CLI's echo.
- **`hermes config set` CANNOT write a LIST-valued key correctly — use scripted `config edit`.**
  For keys Hermes expects to be a YAML *list* (e.g. `skills.external_dirs`), `config set` produces the
  WRONG type and the key is silently ignored:
    - `hermes config set skills.external_dirs.0 <path>` writes a **dict** `{'0': path}` (dotted `.0`
      becomes a nested map key, not a list index).
    - passing a JSON-array string writes a **str**.
    - Hermes only honors a real YAML sequence, so both forms are dead — no error, just not loaded.
  Correct fix: inject a real list via a scripted `$EDITOR` and `hermes config edit`, then verify the
  *loaded Python type is `list`*. TWO gotchas in the recipe: (1) `hermes config edit` calls
  `subprocess.run([EDITOR, config_path])` with **NO shell word-splitting** — `$EDITOR` must be a
  single executable path, so you cannot pass `EDITOR="python3 script.py ARG"`; put any argument INSIDE
  the script and read the config path from `argv[1]` (hermes appends it). (2) Do a ruamel round-trip
  so comments/formatting survive. Verify with a fresh parse, not the CLI echo:
  ```python
  import yaml; v = yaml.safe_load(open(cfg))['skills']['external_dirs']
  assert isinstance(v, list), f"NOT A LIST: {type(v).__name__}"
  ```
  A ready-to-adapt injector lives at `scripts/inject_external_dir.py` in this skill (hardcodes the
  target dir, reads cfg path from `argv[1]`, appends idempotently as a `CommentedSeq`). Set
  `EDITOR=<script>` then run `hermes config edit`. This same pattern applies to ANY list-valued
  Hermes config key, not just `external_dirs`.
- **Cite source by SYMBOL + grep recipe, never `file:line`, in any doc you write.** Line numbers rot
  across releases: in one session a `run.py:1509` citation had drifted +82 lines against a checkout
  only two days old (both were the same `0.19.0` version, different commits). God-files (`run.py`,
  `cli.py`, `run_agent.py`) churn fastest and rot first; small modules happen to survive longer but
  are not safe. When you document \"the code does X at Y\", give a `grep -n \"<symbol>\" <file>` recipe (or
  `python -c \"import mod; print(mod.__file__)\"` to locate it) so the reader finds it in THEIR tree.
  A soft `(near line ~N)` hint is fine; a hard `file:NNN` that a reader will trust verbatim is the
  defect. Audit before claiming zero: `grep -cE '\\.py:[0-9]' <doc>` should return 0.
- **Audit the COMMITTED artifact, not your on-disk working copy.** When checking whether a claim
  (\"the doc is clean\", \"the fix is merged\") holds, grep the committed blob — `git show
  origin/main:path/to/file` — not the file on disk. A dirty/uncommitted working tree produced a
  false \"the merged doc still has the defect\" accusation in one session; `git checkout -B x
  origin/main` reset it and the committed version was clean all along. Self-report of \"I verified it\"
  is a claim; the committed blob is the proof.
- **A `config.yaml` key can SILENTLY SHADOW the env var you were told to set.** The consumer's\n  resolution order matters as much as which layers exist. When precedence is `kwargs > config.yaml >\n  env var > default` (verify it — `grep -n '<key> = kwargs.get' <consumer>`, read the block), a\n  broken install that already wrote `<key>: false` into `config.yaml` makes `SET_THE_ENV_VAR=true`\n  in `.env` a **silent no-op** — the config key wins and nothing errors. So \"just set the env var\"\n  advice is NOT a universal fix. Before documenting an env var as THE workaround: (1) confirm the\n  precedence at the consumer, (2) check whether a competing `config.yaml` key exists\n  (`grep -n '<key>' ~/.hermes/config.yaml`), (3) recommend the higher-precedence layer FIRST\n  (`hermes config set <dotted.key> <value>` — which also matches Hermes' \"behavioral settings →\n  config.yaml, not .env\" rubric), and present the env var as belt-and-suspenders \"only when no\n  competing config key shadows it.\" This is the mirror image of the `cross_session` no-op case: there\n  the config key was dead and the env var was the only lever; here the config key is very much alive\n  and outranks the env var. Same discipline (read the consumer), opposite verdict.\n- **When documenting a bug + its fix, trace the fix history to the MERGED commit, not the first PR.**\n  A widely-cited \"the fix\" PR is often incomplete or superseded. Read the PR thread to its end: the\n  `auto_sleep`-defaults-to-false case pointed everyone at PR #420, but #420 only flipped a schema\n  line, a bot flagged a remaining runtime-fallback gap, and it was closed as **superseded by the\n  merged full fix #429** (both provider surfaces + regression tests). Cite the PR that actually\n  landed the complete fix (and note the incomplete one as history), and state the earliest version\n  that carries it so readers can check \"am I affected?\" by grepping their installed source, not by\n  version-number guessing.\n- **`/proc/<pid>/environ` is invisible to runtime-loaded `.env` vars.** It's an exec-time snapshot;
  a loader that mutates `os.environ` at runtime (e.g. `load_hermes_dotenv`) won't show there. So a
  `.env`-sourced var reads as \"missing\" in `/proc` even though the process has it — `/proc` is only
  reliable for vars set via a systemd `Environment=` drop-in. Correct probe: replay the loader in a
  clean env (`env -i HOME=... HERMES_HOME=... ./venv/bin/python -c 'load…; print(os.environ[...])'`).

## References
- `references/mnemosyne-cross-session-case.md` — the worked example: the `cross_session` config-key
  no-op bug, the `default_scope: global` vs `MNEMOSYNE_CROSS_SESSION` distinction, exact source
  lines, and the reproduction commands.
- `references/hermes-config-key-provenance.md` — worked example for §3b/§3c: verifying
  `memory.mnemosyne.auto_sleep` value AND provenance (the `hermes_home` fall-through trap, the
  `memory_manager.py` live-path guarantee, `_coerce_bool`), plus the in-gateway self-restart guard and
  the hand-off script pattern.
