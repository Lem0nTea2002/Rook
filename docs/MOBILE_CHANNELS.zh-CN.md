# Rook 手机渠道：飞书与微信

Rook v0.5.0 可以在电脑上运行一个本地常驻 Gateway，让已配对的手机飞书或
微信私聊把 Coding Task 交给本机 Rook。电脑必须在线；代码、项目凭据、会话和
工具执行都留在本机。

```text
手机私聊
   ↓
官方 Channel Adapter（飞书长连接 / 微信 iLink）
   ↓
配对用户 + 项目白名单 + 消息去重
   ↓
持久任务队列
   ↓
本地 Rook Agent Loop / Skill / Permission
   ↓
IM 单次审批 → 结果回传
```

## 安装

```powershell
pipx install --backend pip "git+https://github.com/Lem0nTea2002/Rook.git@v0.5.0"
pipx inject rook-agent "lark-oapi>=1.7,<2" "qrcode>=8,<9"
```

源码开发环境可使用：

```powershell
python -m pip install -e ".[im]"
```

## 1. 添加本地项目白名单

路径必须是已存在的绝对目录，不能是符号链接。手机只能切换到此列表中的项目。

```powershell
rook channel project add rook --path "D:\path\to\Rook"
```

非白名单路径不能通过手机消息、`/project` 或审批卡片临时加入。

## 2. 配置飞书

首次配置直接运行：

```powershell
rook channel setup feishu
```

Rook 使用飞书官方扫码注册流程创建独立应用，并把返回的 App ID 和 App Secret
直接写入操作系统凭据库。Secret 不会显示在终端，也不会进入 `channels.toml`、
启动参数或日志。

扫码后，在飞书开放平台为新应用启用机器人，并确认：

- 应用身份权限：`im:message.p2p_msg:readonly`
- 应用身份权限：`im:message:send_as_bot`
- 事件：`im.message.receive_v1`
- 事件接收方式：使用长连接
- 回调：`card.action.trigger`（用于审批卡片）

如果必须复用已有应用，则执行：

```powershell
rook channel setup feishu --app-id cli_xxx
```

此时 App Secret 只通过本机隐藏输入读取。不要通过聊天、Issue、命令行参数或
环境变量传递 Secret；一旦误发，应先在飞书开发者后台重置，再重新配置 Rook。
Rook 会先向飞书凭据接口验证 App ID 与 Secret 匹配，验证失败时不会写入凭据库。

飞书没有面向机器人的原生“正在输入”接口。Rook 会先发送“正在处理”进度卡，
任务结束后原地更新为“处理完成”，再回复最终结果；不会把普通消息伪装成原生
输入状态。

## 3. 登录微信

```powershell
rook channel login weixin
```

终端会显示腾讯官方 iLink 授权二维码。用手机微信扫码并确认后，`bot_token`、
Bot ID、iLink User ID 和服务端地址写入操作系统凭据库。Rook 直接实现官方
HTTP JSON 协议，不需要 OpenClaw，也不会回退到非官方 Hook 或桌面自动化。

如果 iLink 返回会话过期（`errcode=-14`），Gateway 会停止该渠道并要求重新扫码，
不会无限重试旧 Token。

## 4. 配对手机账号

电脑生成一个 10 分钟有效、只能使用一次的配对码：

```powershell
rook channel pair create --channel feishu --project rook
rook channel pair create --channel weixin --project rook
```

在对应手机私聊发送：

```text
/pair ABC123
```

v1 只接受配对用户的私聊文本。群聊、图片、语音、文件和未配对账号均拒绝。

## 5. 启动 Gateway

前台运行：

```powershell
rook channel serve --channels feishu,weixin
```

真实渠道、零模型费用联调：

```powershell
rook channel smoke --channels feishu,weixin
```

`smoke` 会连接真实渠道，但使用本地 Fake Runner，不读取 Provider 配置。手机发送
任意任务后，Rook 会请求一次写入 `rook-mobile-smoke.txt` 的权限；只有手机
`allow_once` 后才写入。随后用 `/diff` 查看，再用 `/cancel` 验证取消入口。请只在
专用临时 Git 仓库执行，完成后自行删除 marker。

查看非敏感状态：

```powershell
rook channel status
rook channel status --json
```

Windows 当前用户登录后自动启动（不需要管理员权限）：

```powershell
rook channel autostart install --channels feishu,weixin
rook channel autostart status
rook channel autostart remove
```

安装命令发现同名计划任务时会拒绝覆盖。请先用 `status` 确认，再显式
`remove` 后重装。

Linux 和 macOS 的 v1 只支持前台 `serve`，不安装系统服务。

## 手机命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看允许的手机命令 |
| `/projects` | 查看本机白名单别名 |
| `/project <alias>` | 切换到白名单项目 |
| `/new` | 创建隔离的新会话 |
| `/status` | 查看渠道、项目、会话和审批状态 |
| `/diff` | 查看安全的 Git diff 统计 |
| `/transcript` | 查看最近的可见输出 |
| `/cancel` | 请求取消当前任务 |
| `/approve <6位码>` | 仅允许当前敏感动作一次 |
| `/deny <6位码>` | 拒绝当前敏感动作 |

普通文本会作为 Coding Task。手机入口不提供 `!shell`、FULL、永久授权、项目
配置或白名单修改能力。

## 审批与恢复

- 飞书发送交互审批卡；微信发送 6 位审批码。
- 审批绑定渠道账号、用户、项目、Session、Tool、Action、Target 和动作哈希。
- 审批 5 分钟失效，错误输入最多 5 次。
- 手机只能选择 `allow_once` 或 `deny`，不能保存永久 Grant。
- 超时会按拒绝恢复 Agent Loop，补齐原 Tool Call 的 Tool Result。
- Gateway 重启后从 SQLite 和 JSONL Session 恢复挂起审批，不会隐式执行工具。
- TUI 与手机渠道使用同一个跨进程项目锁；同一项目不会被两个入口同时修改。

## 本地文件

```text
Windows:
  %APPDATA%\rook\channels.toml
  %APPDATA%\rook\channels.sqlite3
  %APPDATA%\rook\channel-queue.sqlite3
  %APPDATA%\rook\channel.log

Linux/macOS:
  ~/.config/rook/channels.toml
  ~/.config/rook/channels.sqlite3
  ~/.config/rook/channel-queue.sqlite3
  ~/.config/rook/channel.log
```

`channels.toml` 只保存项目别名和绝对路径。渠道 Secret 始终由操作系统凭据库保存。
日志轮转且不记录正文、Token、审批码或 Secret。

## 故障排查

先运行：

```powershell
rook channel status --json
rook channel serve --channels feishu,weixin
Get-Content "$env:APPDATA\rook\channel.log" -Tail 100
```

- 飞书收不到消息：确认应用已启用机器人、长连接、`im.message.receive_v1` 和
  `card.action.trigger`，并已发布当前应用版本。
- 飞书能收不能发：检查 `im:message:send_as_bot`，不要把用户身份权限误当成
  应用身份权限。
- 微信提示 `errcode=-14`：iLink 凭据已过期，重新执行
  `rook channel login weixin`，Rook 不会重试旧 Token。
- 微信扫码后仍未配置：运行 `rook channel status --json`，确认
  `weixin.configured=true`；二维码只在五分钟内有效。
- 手机提示未配对：重新创建一次性配对码；旧码使用一次或十分钟后即失效。
- 任务一直排队：确认本机仍联网且 `channel serve` 进程存在；同一项目会与
  TUI 和其他手机会话串行执行。
- 计划任务已存在：Rook 会拒绝覆盖同名任务。先检查任务来源，确认属于 Rook 后
  再执行 `rook channel autostart remove`。

## 明确边界

- 支持：飞书企业自建应用、微信官方 iLink、一个本机用户、私聊文本。
- 暂缓：企业微信 Adapter、群聊、媒体与附件、云端 Gateway、Web 管理后台。
- 默认测试使用 Fake Channel、Fake Provider 和 Fake Process，不连接真实渠道，
  不调用模型，也不产生模型费用。
