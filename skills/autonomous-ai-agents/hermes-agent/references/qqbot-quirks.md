# QQ Bot Platform Quirks

## WebSocket Session Timeout (code 4009)

QQ's platform disconnects idle WebSocket sessions roughly every 30 minutes with
close code 4009 ("Session timed out"). The adapter handles this automatically:
it clears the session ID and re-identifies. You'll see this in logs:

```
WARNING [QQBot:XXXXXX] WebSocket closed: code=4009 reason=Session timed out
INFO    [QQBot:XXXXXX] Reconnecting in 2s (attempt 1)...
INFO    [QQBot:XXXXXX] Ready, session_id=<new-id>
```

This is normal and requires no action. If reconnect fails (e.g. `500 Internal
Server Error` from `api.sgroup.qq.com/gateway`), run:

```bash
hermes gateway restart
```

The 500 is transient on QQ's side — a restart almost always succeeds immediately.

## "Channel directory built: 0 target(s)"

Hermes only learns a QQ channel/openid when the bot receives an inbound message.
Until then, proactive delivery (cron jobs, notifications) cannot reach the user.

Fix: have the user send any message to the bot in QQ. After that message arrives,
the channel is registered and all subsequent proactive pushes work.

## Credentials

Stored in `~/.hermes/.env`:
- `QQ_APP_ID` — numeric app ID from QQ Bot developer portal
- `QQ_CLIENT_SECRET` — client secret
- `QQ_ALLOWED_USERS` — comma-separated openid allowlist (or `QQ_ALLOW_ALL_USERS=true`)
- `QQBOT_HOME_CHANNEL` — default delivery channel (openid of the home user/group)

Token validity test:
```bash
curl -s -X POST "https://bots.qq.com/app/getAppAccessToken" \
  -H "Content-Type: application/json" \
  -d '{"appId":"<APP_ID>","clientSecret":"<SECRET>"}'
# Healthy response: {"access_token":"...","expires_in":"3000"}
```

Gateway URL fetch (requires valid token):
```bash
curl -s "https://api.sgroup.qq.com/gateway" \
  -H "Authorization: QQBot <access_token>"
# Returns: {"url":"wss://api.sgroup.qq.com/websocket","shards":1,...}
```

## Proactive Push via Cron

Once the channel is registered, cron jobs can push to QQ by including the
QQ platform in the job's delivery targets. The cron prompt should instruct
the agent to use the `messaging` toolset's `send_message` tool targeting
the qqbot platform, or rely on the gateway's default home channel delivery.

Typical setup: configure `QQBOT_HOME_CHANNEL` to the user's openid, then
cron results auto-deliver there alongside any other configured channels (email, etc.).
