"""Export a current-session report without reading ebooks or replacing files."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol


class SessionReportEntry(Protocol):
    """Data required by the exporter, independent of any GUI report store."""

    created_at: datetime
    tool: str
    source: str
    output: str | None
    success: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    checks: tuple[tuple[str, str], ...]


def _validate_destination(target: Path) -> None:
    if os.name != "nt":
        return
    # ADS writes can alter an existing ebook even when the stream name is new;
    # device names and normalized trailing dots/spaces are not new report files.
    parts = target.parts[1:] if target.anchor else target.parts
    if (
        str(target).startswith(("\\\\?\\", "\\\\.\\"))
        or target.is_reserved()
        or any(
            part not in {".", ".."}
            and (
                part.endswith((".", " "))
                or any(char in '<>:"|?*' or ord(char) < 32 for char in part)
            )
            for part in parts
        )
    ):
        raise ValueError("报告目标必须是使用安全文件名的新普通文件")


def export_session_report(
    entries: Iterable[SessionReportEntry],
    destination: str | Path,
    *,
    exported_at: datetime | None = None,
) -> Path:
    """Write UTF-8 JSON to a new file, propagating serialization and I/O errors.

    Serialization finishes before creation, and exclusive creation refuses any
    existing directory entry, including ebook files and links. A write failure
    may leave a partial report; never unlink the path, which could have been
    replaced by another process.
    """
    payload = {
        "schema_version": 1,
        "scope": "current_session",
        "exported_at": (exported_at or datetime.now().astimezone()).isoformat(),
        "entries": [
            {
                "created_at": entry.created_at.isoformat(),
                "tool": entry.tool,
                "source": entry.source,
                "output": entry.output,
                "success": entry.success,
                "warnings": list(entry.warnings),
                "errors": list(entry.errors),
                "checks": dict(entry.checks),
            }
            for entry in entries
        ],
    }
    target = Path(destination)
    _validate_destination(target)
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with target.open("xb") as stream:
        stream.write(serialized)
    return target


__all__ = ["export_session_report"]
