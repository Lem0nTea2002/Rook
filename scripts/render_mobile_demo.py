from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "images" / "rookie-tui.png"
OUTPUT = ROOT / "docs" / "images" / "rook-mobile-demo.gif"
WIDTH = 1280
HEIGHT = 720


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def _bubble(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    top: int,
    user: bool,
    accent: str,
) -> int:
    font = _font(20)
    left = 822 if user else 754
    right = 1198 if user else 1130
    lines = text.splitlines()
    height = 28 * len(lines) + 30
    color = accent if user else "#243147"
    draw.rounded_rectangle((left, top, right, top + height), 18, fill=color)
    draw.multiline_text(
        (left + 18, top + 14),
        text,
        font=font,
        fill="#F6FAFF",
        spacing=6,
    )
    return top + height + 14


def _frame(
    desktop: Image.Image,
    *,
    step: str,
    accent: str,
    messages: list[tuple[bool, str]],
    status: str,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#07111F")
    draw = ImageDraw.Draw(image)
    draw.text((48, 35), "Rook Mobile Channel", font=_font(36, bold=True), fill="#F4F8FF")
    draw.text(
        (48, 87),
        "本地 Coding Agent · 项目白名单 · IM 单次审批",
        font=_font(21),
        fill="#9CB0C9",
    )
    draw.rounded_rectangle((43, 137, 712, 652), 24, fill="#101B2D", outline="#2C415F", width=2)
    draw.rounded_rectangle((735, 125, 1225, 681), 36, fill="#0C1524", outline=accent, width=3)
    draw.rounded_rectangle((870, 140, 1090, 170), 14, fill="#1D2A3E")
    draw.text((773, 190), "飞书 / 微信私聊", font=_font(23, bold=True), fill="#F4F8FF")
    draw.text((1100, 194), step, font=_font(18), fill=accent)

    preview = desktop.copy()
    preview.thumbnail((625, 390), Image.Resampling.LANCZOS)
    image.paste(preview, (65, 160))
    draw.rounded_rectangle((66, 560, 688, 629), 14, fill="#08111E", outline="#273A53")
    draw.text((87, 575), status, font=_font(19), fill="#79E2BD")

    top = 240
    for user, text in messages:
        top = _bubble(draw, text=text, top=top, user=user, accent=accent)
    draw.text(
        (760, 643),
        "流程演示 · Fake Provider · 不调用真实模型",
        font=_font(16),
        fill="#70839C",
    )
    return image


def main() -> None:
    desktop = Image.open(SOURCE).convert("RGB")
    scenes = [
        (
            "1 / 6",
            "#45B8FF",
            [(True, "/pair ABC123"), (False, "配对成功。当前项目：rook")],
            "Gateway 在线  ·  两个渠道已连接",
        ),
        (
            "2 / 6",
            "#45B8FF",
            [(True, "/projects"), (False, "项目白名单：rook"), (True, "/project rook")],
            "身份已绑定  ·  项目路径仅能在电脑登记",
        ),
        (
            "3 / 6",
            "#79E2BD",
            [(True, "修复 README 中的安装命令并运行测试"), (False, "任务已进入本地队列")],
            "SQLite 已持久入队  ·  同一项目串行执行",
        ),
        (
            "4 / 6",
            "#FFB86B",
            [
                (False, "Rook 请求权限\n工具：write\n目标：README.md"),
                (True, "允许一次"),
            ],
            "Agent 已暂停  ·  5 分钟超时自动拒绝",
        ),
        (
            "5 / 6",
            "#79E2BD",
            [(False, "修改完成，相关测试 12/12 通过"), (True, "/diff")],
            "审批绑定用户、会话、项目与动作哈希",
        ),
        (
            "6 / 6",
            "#79E2BD",
            [
                (False, "README.md | 4 +++-\n1 file changed"),
                (True, "/cancel"),
                (False, "当前任务已安全停止"),
            ],
            "结果已回传  ·  日志不记录正文或凭据",
        ),
    ]
    frames = [
        _frame(desktop, step=step, accent=accent, messages=messages, status=status)
        for step, accent, messages, status in scenes
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=[1300, 1300, 1500, 1600, 1500, 2600],
        loop=0,
        optimize=True,
    )


if __name__ == "__main__":
    main()
