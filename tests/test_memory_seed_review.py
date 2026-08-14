from __future__ import annotations

import json
from pathlib import Path


def test_memory_seed_review_tracks_ten_confirmed_active_records() -> None:
    path = Path("benchmark/memory/v1/seed-review.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert set(payload) == {
        "schema_version",
        "benchmark_version",
        "status",
        "evidence_state",
        "activation_allowed",
        "tool_schema_fingerprint",
        "active_memory_records",
        "accepted_evidence",
        "seeds",
    }
    assert payload["status"] == "confirmed"
    assert payload["evidence_state"] == "ten_development_seeds_executed"
    assert payload["activation_allowed"] is True
    assert len(payload["tool_schema_fingerprint"]) == 32
    seeds = payload["seeds"]
    assert len(seeds) == 10
    assert len({seed["seed_id"] for seed in seeds}) == 10
    by_id = {seed["seed_id"]: seed for seed in seeds}
    active_ids = set(by_id)
    assert {seed["seed_id"] for seed in seeds if seed["status"] == "active"} == active_ids
    assert sum(seed["status"] == "awaiting_review" for seed in seeds) == 0
    assert all(seed["destination"] == "project_memory" for seed in seeds)
    assert all(seed["triggers"] and seed["verification"] for seed in seeds)
    assert all("evidence_refs" not in seed for seed in seeds)
    active_records = payload["active_memory_records"]
    assert {record["seed_id"] for record in active_records} == active_ids
    assert len(active_records) == 10
    assert all(record["record_id"].startswith("memory_") for record in active_records)
    assert all(len(record["content_hash"]) == 64 for record in active_records)
    evidence = payload["accepted_evidence"]
    assert {record["seed_id"] for record in evidence} == set(by_id)
    assert all(len(record["record_sha256"]) == 64 for record in evidence)
    assert all(record["recovery_opportunity_id"].startswith("recovery_") for record in evidence)
    assert by_id["seed-01-neighbor-tests"]["proposed_rule"] == (
        "修改有现有测试覆盖的行为时，先读取生产实现和最接近该行为的测试；"
        "修改后运行最窄复现测试，再运行受影响回归。"
    )
    assert by_id["seed-02-resolve-path"]["proposed_rule"] == (
        "路径读取失败后，使用目录、tree 或 glob 获取实际路径，再使用工具返回的规范路径继续操作。"
    )
    assert by_id["seed-10-output-backends"]["proposed_rule"] == (
        "修改跨后端共享的文案或输出语义时，枚举相关后端，并验证至少一个主要后端、"
        "一个替代后端及相关回归。"
    )
    assert by_id["seed-08-state-cleanup"]["proposed_rule"] == (
        "测试修改全局注册表、环境变量或缓存时，使用 try/finally、fixture 或项目既有"
        "清理机制恢复状态，并通过异常路径与完整模块测试验证隔离性。"
    )
    assert by_id["seed-09-doctest-source"]["proposed_rule"] == (
        "修改 docstring 或可执行文档示例时，运行对应 doctest 或示例测试，并同时运行"
        "相关普通回归；不能用普通单测通过替代示例验证。"
    )


def test_memory_exposure_denylist_is_unique_and_nonempty() -> None:
    path = Path("benchmark/memory/v1/exposure-denylist.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert set(payload) == {
        "schema_version",
        "benchmark_version",
        "reason_code",
        "source_dataset",
        "source_config",
        "source_split",
        "excluded_task_ids",
    }
    assert payload["reason_code"] == "candidate_titles_viewed_before_memory_freeze"
    excluded = payload["excluded_task_ids"]
    assert len(excluded) == 32
    assert len(set(excluded)) == len(excluded)
