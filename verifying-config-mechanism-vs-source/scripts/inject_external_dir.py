#!/usr/bin/env python3
"""Scripted $EDITOR for `hermes config edit` — inject a real YAML LIST into a
Hermes config key that `hermes config set` cannot write correctly.

WHY: `hermes config set skills.external_dirs.0 <path>` writes a dict {'0': path};
the JSON-array form writes a str; neither is a real YAML sequence, so Hermes
silently ignores them. Hermes wants a list. `hermes config edit` opens $EDITOR
on config.yaml, so a scripted editor that does a ruamel round-trip can inject a
proper CommentedSeq (list) while preserving comments/formatting.

HERMES INVOCATION CONTRACT (both are load-bearing):
  * Hermes calls subprocess.run([EDITOR, config_path]) with NO shell splitting,
    so $EDITOR MUST be a single executable path. You CANNOT do
    EDITOR="python3 script.py ARG". Put the argument (TARGET) inside the script.
  * config_path arrives as sys.argv[1].

USAGE:
  chmod +x inject_external_dir.py
  # edit TARGET below (or generalise to read from an env var), then:
  EDITOR="/abs/path/to/inject_external_dir.py" hermes config edit
  # then VERIFY the loaded type is list:
  python3 -c "import yaml; v=yaml.safe_load(open('$HOME/.hermes/config.yaml'))['skills']['external_dirs']; \
              assert isinstance(v, list), type(v).__name__; print('OK list:', v)"

Generalising to another list key: change TARGET_KEY_PATH (the nested key) and
TARGET (the value to append). Idempotent: appends only if not already present.
"""
import sys
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

# --- edit these two for your key/value ---
TARGET_KEY_PATH = ["skills", "external_dirs"]   # nested key to make a list
TARGET = "/var/lib/agent-shared/skills"          # value to append into the list
# -----------------------------------------

cfg_path = sys.argv[1]

yaml = YAML()
yaml.preserve_quotes = True
with open(cfg_path) as f:
    data = yaml.load(f) or {}

# walk/create the nested mapping down to the parent of the final key
node = data
for k in TARGET_KEY_PATH[:-1]:
    node = node.setdefault(k, {})
leaf = TARGET_KEY_PATH[-1]

seq = node.get(leaf)
if not isinstance(seq, (list, CommentedSeq)):
    # was a dict {'0': path}, a str, or None -> rebuild as a real list
    seq = CommentedSeq()
    node[leaf] = seq

if TARGET not in list(seq):
    seq.append(TARGET)

with open(cfg_path, "w") as f:
    yaml.dump(data, f)

print(f"[inject_external_dir] {'.'.join(TARGET_KEY_PATH)} now contains {TARGET}",
      file=sys.stderr)
