from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_three_minute_demo_documents_complete_release_lifecycle() -> None:
    guide = (ROOT / "docs" / "THREE_MINUTE_DEMO.zh-CN.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "run_three_minute_demo.ps1").read_text(encoding="utf-8")

    for checkpoint in (
        "Coding Task",
        "Tool Call",
        "Skill 考试",
        "Gate",
        "人工审批",
        "部署",
        "drift",
        "rollback",
    ):
        assert checkpoint in guide
    assert "rook eval demo" in guide
    assert "external_calls" not in script
    assert "-PrepareOnly" in guide
    assert "eval demo" in script
    assert 'Join-Path $repoRoot ".rook\\forge-' in script
    assert 'Join-Path $runRoot "forge"' not in script


def test_three_minute_demo_keeps_live_and_offline_claims_separate() -> None:
    guide = (ROOT / "docs" / "THREE_MINUTE_DEMO.zh-CN.md").read_text(encoding="utf-8")

    assert "可能产生少量模型费用" in guide
    assert "不访问网络、不调用模型" in guide
    assert "不冒充真实模型效果" in guide
