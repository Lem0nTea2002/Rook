from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readmes_publish_memory_benchmark_boundary_and_commands() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "## Project memory effectiveness benchmark" in english
    assert "## 项目记忆有效性评测" in chinese
    for text in (english, chinese):
        assert "rook benchmark memory verify" in text
        assert "pylint-dev__pylint-7114" in text
        assert "pydata__xarray-3364" in text
        assert "20-pair Formal" in text
        assert "0%" in text
        assert "not a resume metric" in text or "不能作为简历指标" in text


def test_three_minute_demo_includes_confirmed_learning_evidence_boundary() -> None:
    demo = (ROOT / "docs" / "THREE_MINUTE_DEMO.zh-CN.md").read_text(
        encoding="utf-8"
    )

    assert "恢复检测 → 用户审阅 → 项目记忆 → 后续复用" in demo
    assert "检测阶段模型调用为 0" in demo
    assert "Memory Formal 尚未执行" in demo


def test_memory_freeze_document_keeps_latest_failed_pilot_visible() -> None:
    freeze = (
        ROOT / "docs" / "benchmarks" / "MEMORY_AB_FREEZE_V1.zh-CN.md"
    ).read_text(encoding="utf-8")

    normalized = " ".join(freeze.split())
    assert "v8 定向 2-pair" in normalized
    assert "Baseline 与 Memory 成功率均为 0%" in normalized
    assert "20-pair Formal 保持暂停" in normalized
