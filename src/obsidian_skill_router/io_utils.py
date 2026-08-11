from __future__ import annotations

from pathlib import Path


SKILL_METADATA_MAX_BYTES = 4 * 1024 * 1024
GOVERNANCE_INPUT_MAX_BYTES = 8 * 1024 * 1024
HOOK_INPUT_MAX_CHARS = 1024 * 1024


def read_bytes_bounded(path: Path, max_bytes: int, *, label: str = "file") -> bytes:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
    return data


def read_text_bounded(path: Path, max_bytes: int, *, label: str = "file") -> str:
    return read_bytes_bounded(path, max_bytes, label=label).decode("utf-8")
