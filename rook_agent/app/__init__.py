"""Rook Textual/TUI 入口模块。"""

from rook_agent.app.factory import create_rook_app
from rook_agent.app.tui import RookApp, RookTuiConfig

__all__ = ["RookApp", "RookTuiConfig", "create_rook_app"]
