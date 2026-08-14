# Rook mobile channels

Rook v0.5.0 can keep a local gateway running so one paired Feishu or WeChat
private chat can submit coding tasks to the Rook process on your computer. The
computer must remain online; projects, credentials, sessions, and tool execution
stay local.

## Setup

```powershell
pipx install --backend pip "git+https://github.com/Lem0nTea2002/Rook.git@v0.5.0"
pipx inject rook-agent "lark-oapi>=1.7,<2" "qrcode>=8,<9"
rook channel project add rook --path "D:\absolute\path\to\Rook"
rook channel setup feishu
rook channel login weixin
rook channel pair create --channel feishu --project rook
rook channel pair create --channel weixin --project rook
rook channel serve --channels feishu,weixin
```

For a source checkout used for development, install the optional dependencies
with `python -m pip install -e ".[im]"` instead.

Send `/pair ABC123` in each private chat using the single-use code printed by
Rook. Codes expire after ten minutes.

Feishu uses the official `lark-oapi` long connection and needs bot send/receive
permissions, the `im.message.receive_v1` event, and the `card.action.trigger`
callback. WeChat uses Tencent's official iLink QR authorization and HTTP JSON
protocol directly; no OpenClaw host or unofficial desktop hook is involved.

## Security model

- Only one explicitly paired user per channel account and private text messages.
- Only absolute, existing, non-symlink project paths on the local whitelist.
- Durable SQLite deduplication, queue leases, conversation bindings, approvals,
  and iLink sync cursor.
- Same session is serialized; total channel concurrency is two.
- Rook TUI and channels share the Agent Loop, Skills, permission policy, session
  store, and cross-process project execution lock.
- Sensitive tools pause. Feishu sends an approval card; WeChat sends a six-digit
  code. Only allow-once or deny is available.
- Approval identity includes user, project, session, tool, action, target, and
  action hash. It expires after five minutes and locks after five wrong codes.
- Expiration resumes the original tool call as denied, including after restart.
- Secrets are stored in the operating-system credential manager, not TOML,
  command-line arguments, or logs.

Run `rook channel status --json` for non-secret status. Windows current-user
autostart is available through `rook channel autostart install`; Linux and macOS
v1 are foreground-only.

See [the Chinese guide](MOBILE_CHANNELS.zh-CN.md) for the complete command and
permission reference.
