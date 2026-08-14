from __future__ import annotations

import hashlib
from pathlib import Path
import zlib

from rook_agent.benchmarks.git_blob_hydration import _write_loose_blob


def test_write_loose_blob_uses_git_blob_object_format(tmp_path: Path) -> None:
    data = b"hello native\n"
    expected = hashlib.sha1(
        b"blob 13\0hello native\n",
        usedforsecurity=False,
    ).hexdigest()

    assert _write_loose_blob(tmp_path, data) is True
    payload = zlib.decompress(
        (tmp_path / expected[:2] / expected[2:]).read_bytes()
    )
    assert payload == b"blob 13\0hello native\n"
    assert _write_loose_blob(tmp_path, data) is False
