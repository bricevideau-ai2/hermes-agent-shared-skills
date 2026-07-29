---
name: cross-agent-gateway-restart
description: When your OWN Hermes gateway needs restarting, DON'T do it yourself — ask a peer agent to do it. An agent cannot cleanly restart the process that is currently delivering its messages: the restart kills the very session that would report the result, so the agent flails (tries, reasons it's a bad idea, or scripts a fragile self-kill and a human has to step in). This skill encodes the correct protocol — delegate the restart to another agent over Discord, and have the restarter notify the issuer when the gateway is back so a specific thread can be resumed. Load when your gateway is wedged/needs restart, OR when a peer agent asks YOU to restart theirs.
category: collaboration
metadata:
  hermes:
    tags: [gateway, restart, multi-agent, discord, systemd, delegation, self-restart-paradox]
    related_skills: [gateway-restart-procedure, hermes-gateway-daemon-setup, hermes-shell-privileges]
---

# Cross-agent gateway restart

## The problem this solves (why you must NOT restart your own gateway)

Your gateway is the systemd **user** service (`hermes-gateway.service`) that
receives your Discord/Telegram/etc. messages and hands them to you. **If you
restart it yourself, you kill the process that is currently running you** — the
in-flight session that issued the restart dies mid-command, so nothing reports
whether the restart succeeded, and any thread you were working in is orphaned.

This is a genuine paradox, not a policy: self-restart *is* a bad idea, and an
agent that reasons its way to "I shouldn't do this myself" is reasoning
correctly. But the resolution is **not** to give up, script a fragile
self-kill, or wait for a human. The resolution is: **ask another agent to
restart it for you.** A peer's gateway is a separate process on a separate uid;
it survives your restart and can report back.

Observed failure modes this replaces (each required a human to intervene):
- Agent tries `systemctl --user restart` on its own gateway, session dies, silence.
- Agent reasons "self-restart is dangerous" and stalls with no next step.
- Agent scripts a background self-kill + re-spawn; races, wedges, or double-starts.

---

## Roles

- **Issuer** = the agent whose gateway needs restarting. You know your gateway is
  wedged (e.g. peer says your bot looks offline; you saw an error before the loop
  froze; a config/env change needs the daemon reloaded — see
  `gateway-restart-procedure` for what changes require a restart).
- **Restarter** = a peer agent on the same host with sudo, whose gateway is
  healthy, who performs the restart on the issuer's units and reports back.

On the piment box the two agents are **Corwin** (uid 1001, `videau-ai`) and
**Deirdre** (uid 1002, `deirdre-ai`). Each is the other's restarter. Confirm
uids live — don't trust this doc as ground truth for uids:
`getent passwd videau-ai deirdre-ai | cut -d: -f1,3`.

---

## PROTOCOL

### Step 1 (Issuer) — Ask a peer, in Discord, BEFORE you lose the session
The moment you conclude your gateway needs a restart, send a peer agent a Discord
message asking them to do it — while you can still send. Include, so they can
finish without you (you may be gone the instant they act):

- **who** you are and your **uid** (`id -u`) — the restarter needs it to target
  your units, not their own.
- **what** you need: restart `hermes-gateway.service`.
- **whether** you need to resume a specific thread afterward, and **which thread**
  (channel/thread id or a human-readable pointer). If yes, ask them to **ping you
  in that thread once your gateway is confirmed back**, so you get re-invoked
  there and can pick up where you left off.

Send this with the tagged `hermes send` to the peer's channel, then STOP — do not
also try to restart yourself. (Threading discipline: send the first tagged
message once; the peer's reply comes back to YOUR gateway as a new turn once it's
back. Don't compose a second `hermes send` to continue.)

Example message body:
> Deirdre — my gateway (uid 1002, deirdre-ai) is wedged and needs a restart.
> Please `systemctl --user restart hermes-gateway.service` on my units. I was
> mid-task in thread <THREAD_ID>; once you've confirmed it's back (Discord
> handshake in my gateway.log), ping me in that thread so I can resume.

If you genuinely cannot send (already dead), this is the one case where a human
must intervene — but try to send FIRST.

### Step 2 (Restarter) — Restart the ISSUER's units, not your own
You act on the issuer's uid and runtime dir. Let `TUID` = the issuer's uid.
Requires working sudo (`sudo whoami`, never `sudo -n` — see
`hermes-shell-privileges`).

```bash
TUID=1002                       # <-- the ISSUER's uid (id -u on their account); verify, don't assume
TUSER=$(getent passwd "$TUID" | cut -d: -f1)

# The issuer's user bus must be up. Probe the runtime dir DIRECTLY — do NOT trust
# `loginctl show-user`, which can report Linger=no / "not logged in" while
# /run/user/$TUID is in fact present and the service is running.
sudo test -d /run/user/"$TUID" && echo "runtime dir present" || echo "NO runtime dir — see note below"

RUN="XDG_RUNTIME_DIR=/run/user/$TUID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$TUID/bus"

# Confirm you can see their unit before touching it:
sudo -u "$TUSER" env $RUN systemctl --user is-active hermes-gateway.service

# Restart it:
sudo -u "$TUSER" env $RUN systemctl --user restart hermes-gateway.service
```

If `/run/user/$TUID` does NOT exist, the issuer's user manager isn't running —
enable linger first (`sudo loginctl enable-linger "$TUSER"`), then the runtime
dir appears; see `hermes-gateway-daemon-setup` Step 1.

### Step 3 (Restarter) — VERIFY it actually came back (don't trust `active`)
`active (running)` is NOT proof. A wedged PID sits `active` for hours with the
Discord loop frozen and the bot OFFLINE. Verify against the FILE log (the
issuer's, not journalctl) and confirm the loop is live:

```bash
sudo tail -25 /home/"$TUSER"/.hermes/logs/gateway.log
# Look for the once-per-restart READY lines:
#   [Discord] Connected as <bot>#<disc>   AND   ✓ discord connected

PID=$(sudo -u "$TUSER" env $RUN systemctl --user show hermes-gateway.service -p MainPID --value)
for i in 1 2; do sudo awk '{print "ticks:",$14+$15}' /proc/$PID/stat; sleep 2; done
# Frozen utime+stime across samples = still wedged. Rising ticks / a fresh
# "✓ discord connected" = genuinely up.
```
(Full liveness rubric — STAT/wchan, live round-trip — is in
`gateway-restart-procedure`. Reuse it; don't reinvent it.)

Only report success once you've seen the fresh handshake, not off the systemd
status line alone.

### Step 4 (Restarter) — Notify the issuer so they can resume the thread
Once verified back, if the issuer asked to resume a specific thread, **ping them
in that exact thread** (via the `discord` tool / `hermes send` to the thread), so
the issuer's now-healthy gateway re-invokes them THERE and they continue in
context. Reply into the existing thread — never open a parallel one. If you can't
discover the thread id, ask the human for it rather than guessing (Brice supplies
thread ids when the agent can't discover one).

A confirmation message like:
> Corwin — your gateway is back (fresh `✓ discord connected` in gateway.log,
> ticks rising). Pinging you here in thread <THREAD_ID> so you can resume.

### Step 5 (Issuer, after revival) — Resume in the thread you were pinged in
Your gateway is back and you were re-invoked in the original thread. Continue the
task from there. If you'd saved task state (Mnemosyne task_progress / a session
note) before the wedge, reload it now.

---

## Pitfalls recap
- **Restarting your own gateway = killing your own session.** Never self-restart;
  delegate to a peer. The instinct that self-restart is "a bad idea" is correct —
  the missing step is "ask another agent," not "give up" or "script a self-kill."
- **Ask a peer BEFORE you lose the ability to send.** Send the request the moment
  you decide a restart is needed, with your uid + thread id, so they can finish
  without you.
- **Restarter must target the ISSUER's uid + runtime dir**, not their own:
  `sudo -u <issuer> env XDG_RUNTIME_DIR=/run/user/<TUID> DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<TUID>/bus systemctl --user restart hermes-gateway.service`.
- **Don't trust `loginctl show-user` for liveness** — it can say not-lingering
  while `/run/user/<uid>` is present and the service runs. Probe the runtime dir
  directly.
- **`active (running)` ≠ healthy.** Verify the fresh Discord handshake in the FILE
  log + rising CPU ticks before reporting success.
- **Resume in the SAME thread.** The restarter pings the issuer in the original
  thread; never spin up a parallel thread. Ask the human for the thread id if you
  can't discover it.
- **uids drift between hosts.** Verify uids live (`getent passwd <account>`);
  don't hardcode 1001/1002 as universal.
