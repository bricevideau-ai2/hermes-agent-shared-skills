# Discord platform enrollment — concrete recipe & gotchas

Session-tested walkthrough for wiring Discord into the gateway daemon. Complements
Step 5 of the umbrella SKILL.md. Order matters: validate the token BEFORE touching
the gateway, and fix the allowlist or the bot will connect but answer no one.

## 0. Get the token safely (if delivered by email)
If the user mailed the token (e.g. via himalaya), pull it straight into `.env`
WITHOUT echoing the value into the transcript:

    TOKEN=$(himalaya message read <ID> 2>/dev/null \
      | grep -E '^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$' | head -1)
    ENV=~/.hermes/.env
    sed -i '/^DISCORD_BOT_TOKEN=/d' "$ENV"
    printf 'DISCORD_BOT_TOKEN=%s\n' "$TOKEN" >> "$ENV"
    chmod 600 "$ENV"
    echo "wrote token, length ${#TOKEN}"   # print length only, never the value
    unset TOKEN

A Discord bot token is 3 dot-separated base64url segments (~59-72 chars). Hermes'
secret redaction will mask it as `***` in tool output — that's the redactor
working, not a write failure; grep the raw file to confirm the key line exists.

## 1. Validate the token + check server membership BEFORE gateway work
A standalone discord.py login probe answers "is the token valid?" and "is the bot
actually in a server?" in one shot — far faster than debugging via the gateway:

    VPY=~/.hermes/hermes-agent/venv/bin/python
    $VPY - <<'PY'
    import asyncio, pathlib, discord
    env = pathlib.Path.home()/".hermes"/".env"
    tok = next((l.split("=",1)[1].strip() for l in env.read_text().splitlines()
                if l.startswith("DISCORD_BOT_TOKEN=")), None)
    intents = discord.Intents.default(); intents.message_content = True
    client = discord.Client(intents=intents)
    @client.event
    async def on_ready():
        print("LOGIN OK:", client.user, "id", client.user.id, "| guilds:", len(client.guilds))
        for g in client.guilds:
            print("  ", g.name, "id", g.id, "owner_id", g.owner_id)
        await client.close()
    async def main():
        try: await asyncio.wait_for(client.start(tok), timeout=30)
        except discord.LoginFailure as e: print("BAD TOKEN:", e)
        finally:
            if not client.is_closed(): await client.close()
    asyncio.run(main())
    PY

- `LOGIN OK ... guilds: 0` => token is fine but the bot is **not in any server yet**
  (see #2). This is the most common "it logged in but does nothing" state.
- `BAD TOKEN` / LoginFailure => wrong or rotated token.
- Add `intents.members = True` and iterate `await g.fetch_members(limit=N)` to read
  the human members and their user IDs (needed for the allowlist in #3). Requires
  Server Members Intent enabled in the portal.

## 2. Server invite (discord.gg/...) is NOT the bot-add URL
A `https://discord.gg/xxxx` link is a *human* server invite — bots cannot use it.
A bot joins only via an OAuth2 authorize URL the user opens and approves. Generate
it from the bot's application/client ID:

    # least-privilege (view/send/read history/embed/attach/react + slash commands):
    https://discord.com/api/oauth2/authorize?client_id=<APP_ID>&permissions=274877991936&scope=bot%20applications.commands
    # simplest full access on the user's own server:
    https://discord.com/api/oauth2/authorize?client_id=<APP_ID>&permissions=8&scope=bot%20applications.commands

The client_id == the application/bot ID printed by the probe in #1. User must have
Manage Server on the target guild. After they authorize, re-run the probe: guilds
should now be >= 1.

## 3. THE ALLOWLIST — #1 real blocker after the bot is in the server
Even connected and in a server, the gateway DENIES every message until an
allowlist is configured. The tell in `~/.hermes/logs/gateway.log`:

    WARNING ... [Discord] Discord messages are being denied because no allowlist
    is configured. Set DISCORD_ALLOWED_USERS / DISCORD_ALLOWED_ROLES /
    DISCORD_ALLOWED_CHANNELS, or DISCORD_ALLOW_ALL_USERS=true for open access.

For a personal single-user agent, allowlist just the user's Discord ID (most
secure — the bot can be in a server yet only obey the owner):

    ENV=~/.hermes/.env
    sed -i '/^DISCORD_ALLOWED_USERS=/d' "$ENV"
    printf 'DISCORD_ALLOWED_USERS=%s\n' "<discord_user_id>" >> "$ENV"

Get the user ID from the probe in #1 (guild `owner_id`, or the members list).
Then RESTART the gateway so it re-reads `.env`. Verify the deny warning is gone.

## 4. Platform auto-enables from token presence (no config.yaml edit)
Hermes' `load_gateway_config()` marks a platform enabled when its token env var is
set — so writing `DISCORD_BOT_TOKEN` to `.env` is enough; you do NOT need a
`gateway: platforms: discord: {enabled: true}` block. Confirm without starting the
service:

    export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
    set -a; . ~/.hermes/.env; set +a
    ~/.hermes/hermes-agent/venv/bin/python -c \
      "from gateway.config import load_gateway_config as L; c=L(); print([(str(k), p.enabled) for k,p in c.platforms.items()])"

## 5. Verify the live round trip via the log
After `hermes gateway restart`, a healthy connect + working exchange looks like:

    INFO ... [Discord] Connected as <BotName>#NNNN
    INFO gateway.run: ✓ discord connected
    INFO gateway.run: inbound message: platform=discord user=<name> ... msg='Hello'
    INFO gateway.run: response ready: platform=discord ... time=3.8s api_calls=1 response=96 chars
    INFO gateway.platforms.base: [Discord] Sending response (96 chars) to <chat_id>

`inbound message` with NO following `response ready` and a `denied`/allowlist
warning => go back to #3. `Connected` but no `inbound` when the user swears they
messaged => Message Content Intent is off in the portal (see umbrella Step 5).

## Ordered checklist
1. Token into `.env` (secret-safe), confirm key line present.
2. Probe: valid token? in a server?  (#1)
3. Not in a server => generate OAuth2 authorize URL, user approves.  (#2)
4. Set `DISCORD_ALLOWED_USERS=<owner id>`, restart.  (#3)
5. Confirm platform auto-enabled + Message Content Intent on.  (#4, umbrella Step 5)
6. Restart, watch the log for the full inbound→response cycle.  (#5)
