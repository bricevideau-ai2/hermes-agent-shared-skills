# hermes-agent-shared-skills

Shared [Hermes Agent](https://hermes-agent.nousresearch.com) **skills** — reusable
procedural knowledge distilled while running two cooperating Hermes agents
(Corwin & Deirdre) on a single NVIDIA DGX Spark host. Each skill is a `SKILL.md`
(plus optional `references/` and `scripts/`) that a Hermes agent loads on demand.

These were authored for our box, then generalized. They cover the operational
problems that actually bit us: restarting the gateway safely, keeping Mnemosyne
memory healthy, verifying config against source instead of trusting black-box
behavior, and sharing/de-duplicating skills between agents without losing content.

## What's here

| Skill | What it's for |
|---|---|
| `cross-agent-gateway-restart` | Restart *another* agent's gateway (or your own) safely, via a laddered set of recovery rungs. |
| `gateway-restart-procedure` | The minimal env-export + `systemctl --user restart` sequence for the gateway. |
| `hermes-gateway-daemon-setup` | Stand up the Hermes messaging gateway as a persistent systemd `--user` service; includes a Discord-enrollment reference. |
| `hermes-selfhosted-firecrawl` | Wire Hermes `web_extract` to a free self-hosted Firecrawl. |
| `hermes-shell-privileges` | Run privileged / credential-dependent shell commands from a Hermes agent. |
| `mnemosyne-hermes-recall-troubleshooting` | Diagnose & fix the case where in-process Mnemosyne recall returns nothing. |
| `mnemosyne-recover-think-leak-episodic` | Clean Mnemosyne episodic rows that leaked raw `<think>` reasoning. |
| `skill-sharing-and-dedup` | Promote, share, merge, and de-dup skills between agents through a shared external-skills dir without breaking a working skill. |
| `verifying-config-mechanism-vs-source` | Prove whether a config/env setting is the *sanctioned* mechanism or a workaround, by reading source + a clean-room truth table. |

`MAINTAINERS.md` and `REVIEW-skill-sharing.md` document ownership and the review
bar we hold shared skills to.

## Using these in your own Hermes install

Point Hermes at this directory as an external skills dir. Clone it, then add the
path to `skills.external_dirs` in `~/.hermes/config.yaml`:

```bash
git clone https://github.com/bricevideau-ai2/hermes-agent-shared-skills.git \
  ~/hermes-shared-skills
```

`skills.external_dirs` must be a real YAML **list** — and `hermes config set`
cannot author a list correctly (it writes a dict or a string that Hermes
silently ignores). Use `hermes config edit`, or the `inject_external_dir.py`
helper under `verifying-config-mechanism-vs-source/scripts/`, then verify the
loaded value is a Python `list`. (This footgun is documented in full inside that
same skill.)

## ⚠️ Local specifics — read before reusing

These skills were written against **our** host and generalized afterward. Several
still embed facts that are true for *our* box but will be wrong for yours. Adapt,
don't copy blindly:

- **User IDs / systemd `--user` scope.** Snippets export
  `XDG_RUNTIME_DIR=/run/user/1002` and
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus`. `1002` is *our*
  agent's uid. Replace it with `$(id -u)` (or your agent's actual uid). A
  `systemctl --user` command run with the wrong `XDG_RUNTIME_DIR` silently talks
  to the wrong user's manager.
- **Service names.** We use `hermes-gateway.service`, `argo-shim.service`,
  `qwen.service`. Yours may differ; grep your own `systemctl --user list-units`.
- **Two-agent assumptions.** `cross-agent-gateway-restart`, `skill-sharing-and-dedup`,
  and the group-permission / `sudo -u <other-uid>` steps assume **two agents
  sharing one host** through a setgid group and a shared external dir
  (`/var/lib/agent-shared` on our box). On a single-agent install, the
  cross-uid permission dance and the dedup-race convention don't apply — skip them.
- **Paths.** `/var/lib/agent-shared/...` and `~/.hermes/...` are our layout.
  `HERMES_HOME`/`$HOME` vary by install.
- **Model & version specifics.** References to a served model (e.g. `qwen` on
  local vLLM at `127.0.0.1:8000`, Claude via a shim at `127.0.0.1:8443`) are our
  fallback chain, not a requirement. Where a skill names a Mnemosyne/Hermes API
  symbol, treat it as version-dependent — the skills tell you to re-verify
  against *your* installed source (`grep -n "<symbol>" $(python -c 'import mod;
  print(mod.__file__)')`) rather than trusting a line number.

In short: **uid `1002`, port `8443`/`8000`, `/var/lib/agent-shared`, and specific
service/model names are this-box facts.** The *procedures* transfer; the literals
need substituting for your environment. The individual `SKILL.md` files call these
out inline where they matter.

## Provenance

Authored by the Corwin & Deirdre agents on piment (DGX Spark) and reviewed
cross-agent against source. No secrets, credentials, or private keys are present —
the only credential-shaped strings are documented placeholders.
