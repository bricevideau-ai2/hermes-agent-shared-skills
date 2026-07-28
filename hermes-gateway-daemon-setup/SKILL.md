---
name: hermes-gateway-daemon-setup
description: Set up and operate the Hermes messaging gateway as a persistent systemd-user background daemon (survives logout/reboot). Covers enabling linger, bringing up the user bus from a shell that predates it, making env vars survive unit regeneration via a drop-in, fixing partial installs missing platform deps, and enrolling a messaging platform (Discord/Telegram/Slack). Load when the user wants "daemon mode", a persistent/background agent, the gateway to survive logout, or when `hermes gateway status`/`systemctl --user` misbehaves.
---

# Hermes Gateway Daemon Setup

Run the Hermes gateway (`hermes-gateway.service`, a systemd **user** unit) as a
durable background daemon so the agent stays reachable on a messaging platform
without an interactive `hermes` session open.

Prereq: privileged actions here need working sudo — see the
`hermes-shell-privileges` skill (esp. never verify sudo with `sudo -n`; use
`sudo whoami`).

## Diagnose the current launch reality FIRST
Do not assume the daemon path is live just because the unit file exists. Check:

    echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"        # empty => no user bus in this shell
    ls -ld /run/user/$(id -u)                       # missing => user manager not running
    systemctl --user is-system-running              # "Failed to connect to bus: No medium found" => bus down
    loginctl show-user $(whoami) | grep -i linger   # Linger=no => won't run without an active login
    hermes gateway status                           # Hermes' own authoritative view
    hermes gateway list                             # which platforms are enrolled (none => daemon has nothing to do)

Common finding on a headless / SSH-only / no-graphical-login box: the systemd
**user** manager isn't running at all (no `/run/user/<uid>`, no bus, linger off).
A `hermes-gateway.service` unit can sit there having NEVER run.

## Step 1 — Enable linger (makes the user manager start at boot)
    sudo loginctl enable-linger <account>
    loginctl show-user <account> | grep -iE "Linger|State"   # expect Linger=yes
    ls -ld /run/user/<uid>                                    # now exists

Linger = the user systemd instance starts at boot and survives logout — required
for a user service to run persistently without an active session.

## Step 2 — Reach the user bus from a shell that predates it
Your current shell was likely started BEFORE the runtime dir existed, so its
`XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS` are empty and `systemctl --user`
fails. Export them for the session:

    export XDG_RUNTIME_DIR=/run/user/<uid>
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/<uid>/bus"
    systemctl --user is-system-running        # wait for "running" (poll a few times)

Re-export these in EVERY terminal() call that talks to `systemctl --user` in the
same session — exported env may not persist across separate tool invocations.

## Step 3 — Make env vars survive `hermes gateway restart` (drop-in, not base unit)
`hermes gateway restart` REGENERATES the base unit from a template, so any
`Environment=` line you hand-add to `~/.config/systemd/user/hermes-gateway.service`
is LOST on the next restart. Use a systemd **drop-in** instead — it composes on
top of the regenerated unit and survives:

    mkdir -p ~/.config/systemd/user/hermes-gateway.service.d
    # write ~/.config/systemd/user/hermes-gateway.service.d/10-<name>.conf :
    #   [Service]
    #   Environment="MNEMOSYNE_CROSS_SESSION=1"
    systemctl --user daemon-reload
    systemctl --user show hermes-gateway.service -p Environment    # verify var present
    systemctl --user show hermes-gateway.service -p DropInPaths    # verify drop-in registered

(Example: `MNEMOSYNE_CROSS_SESSION=1` must be in the gateway's process env because
mnemosyne reads it at import time — see `mnemosyne-hermes-recall-troubleshooting`.)

## Step 4 — Fix partial-install missing platform deps
An install done WITHOUT sudo (or a minimal install) often skips the pyproject
`messaging` extra, so the platform SDK is absent and the adapter silently can't
run. Verify against the venv, not system python:

    VPY=~/.hermes/hermes-agent/venv/bin/python
    $VPY -c "import discord; print(discord.__version__)"   # ModuleNotFoundError => missing

Install the EXACT pinned version Hermes declares (find it in
`~/.hermes/hermes-agent/pyproject.toml` under the `messaging` extra) into the VENV
(no sudo, no PEP 668 issue):

    ~/.hermes/hermes-agent/venv/bin/pip install "discord.py[voice]==2.7.1"

Only install deps for the platform actually being used; don't pull the whole extra
for platforms the user doesn't want.

## Step 5 — Enroll the platform (interactive; needs the user)
`hermes gateway setup` is a fully interactive wizard (no flags) — the agent cannot
drive it or safely enter a token blind. The token env keys live in
`gateway/config.py` (`Platform.<X>: "<KEY>"`), e.g. Discord = `DISCORD_BOT_TOKEN`,
stored in `~/.hermes/.env`. Two hand-off paths: user runs the wizard themselves, OR
user pastes the token and the agent writes `DISCORD_BOT_TOKEN=...` into `.env`.

Platform gotchas (from the hermes-agent skill's troubleshooting):
- Discord bot silent => enable **Message Content Intent** in the Developer Portal
  (Bot → Privileged Gateway Intents). #1 cause of a dead bot.
- Slack bot ignores channels => subscribe to `message.channels` event.

Discord-specific extras that BLOCKED a real session (full recipe in
`references/discord-enrollment.md`):
- **Allowlist denial is the #1 post-connect blocker.** A connected bot in a server
  still DENIES every message until an allowlist exists (log: "messages are being
  denied because no allowlist is configured"). Set `DISCORD_ALLOWED_USERS=<user id>`
  in `.env` (least-privilege for a personal agent) and restart.
- **`discord.gg/...` is a human server invite, NOT the bot-add URL.** Bots join only
  via an OAuth2 authorize URL built from the app/client ID
  (`.../oauth2/authorize?client_id=<id>&permissions=<n>&scope=bot%20applications.commands`).
- **Validate the token with a standalone discord.py login probe BEFORE gateway work**
  — it reveals "valid token but bot in 0 servers" instantly (see reference file).
- **Token presence auto-enables the platform** — writing `DISCORD_BOT_TOKEN` to
  `.env` is enough; no `gateway: platforms:` config block needed.

## Step 6 — Enable + start + verify
    systemctl --user enable hermes-gateway.service     # start on boot
    hermes gateway restart                             # or: systemctl --user start hermes-gateway
    hermes gateway status                              # expect active/running
    grep -i "error\|failed" ~/.hermes/logs/gateway.log | tail -20
    # crash loop? reset failed state: systemctl --user reset-failed hermes-gateway

## Pitfalls recap
- Don't trust a present unit file — verify linger + bus + enrollment first.
- `systemctl --user` from a pre-bus shell fails until you export XDG_RUNTIME_DIR +
  DBUS_SESSION_BUS_ADDRESS.
- Hand-added `Environment=` on the base unit is wiped by `hermes gateway restart`;
  use a `.service.d/*.conf` drop-in.
- `daemon-reload` failing with "No medium found" just means no bus in that shell —
  fix the env exports; it's not a systemd corruption.
- Verify platform SDK against `venv/bin/python`, not system `python3`.
- The platform-enrollment wizard is interactive → a genuine hand-off-to-user point,
  not something to fake.
