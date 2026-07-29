---
name: gateway-restart-procedure
description: Restart gateway for Mnemosyne cross-session visibility.
trigger: |
  Run when the Hermes gateway service must be restarted after modifying environment variables (e.g., MNEMOSYNE_CROSS_SESSION) or configuration files to apply the changes to cross‑session memory visibility.
---

> **STOP — is this YOUR OWN gateway on a multi-agent host?** Do NOT self-restart:
> `systemctl --user restart hermes-gateway.service` kills the very process
> delivering your session, so nothing reports the result and any thread you were
> in is orphaned. Ask a PEER agent to restart it for you and ping you back in the
> thread. Full protocol + cross-uid escalation ladder (reset-failed → hard
> stop→kill→start → `systemctl restart user@<uid>.service` → `loginctl
> terminate-user`) lives in the shared skill `cross-agent-gateway-restart`. The
> procedure below is for restarting a gateway you are NOT running inside (a peer's,
> or from a separate non-gateway session).

Procedure
----
1. Export the runtime environment variables (uid 1002 = deirdre-ai; when
   restarting a PEER's gateway use the TARGET agent's uid, not this literal):
   ```bash
   export XDG_RUNTIME_DIR=/run/user/1002
   export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus
   ```
2. Reload systemd user units:
   ```bash
   systemctl --user daemon-reload
   ```
3. Restart the Hermes gateway service:
   ```bash
   systemctl --user restart hermes-gateway.service
   ```
4. Verify the service is active:
   ```bash
   systemctl --user status hermes-gateway.service
   ```

   PITFALL — `active (running)` is NOT proof the gateway works. The PID can be
   wedged (blocked in `ep_poll`, CPU frozen) with stale ESTAB sockets for hours
   while systemd still says "active (running)" and Discord shows the bot OFFLINE.
   Do not report the gateway healthy off the systemd status line alone. Verify
   the REAL boot + Discord handshake in the FILE logs (not journalctl — the
   journal can be stale/miss the process's stdout while the file logs are
   authoritative):

   ```bash
   # authoritative source of truth is the file log, not journalctl
   tail -25 /home/deirdre-ai/.hermes/logs/gateway.log
   ```

   Look for the exact READY lines, once per restart:
     `[Discord] Connected as <bot>#<disc>`  and  `✓ discord connected`
   Then confirm the process is actually WORKING, not idle-blocked:
   ```bash
   PID=$(systemctl --user show hermes-gateway.service -p MainPID --value)
   ps -o pid,stat,wchan:20,%cpu -p "$PID"     # STAT Ssl + ep_poll alone is ambiguous
   for i in 1 2; do awk '{print "ticks:",$14+$15}' /proc/$PID/stat; sleep 2; done
   ```
   Frozen utime+stime ticks across samples = wedged, NOT healthy. Rising ticks
   or a fresh `✓ discord connected` in gateway.log = genuinely up. Final proof
   is a live round-trip: have someone send a Discord message and confirm it
   lands in gateway.log — connectedness of the socket ≠ liveness of the loop.

   PITFALL — do NOT trust `loginctl show-user <acct>` for the runtime dir /
   liveness. Verified on piment 2026-07-29: it reported `Linger=no` / "User ID
   1001 is not logged in or lingering" while `/run/user/1001` WAS present and the
   gateway service was `active`. Probe the runtime dir directly
   (`sudo test -d /run/user/<uid>`) instead of inferring it from loginctl.