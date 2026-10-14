import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ebook_toolbox.services.report_export import export_session_report


@dataclass(frozen=True)
class SessionEntry:
    created_at: datetime
    tool: str
    source: str
    output: str | None
    success: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    checks: tuple[tuple[str, str], ...] = ()


def sample_entry(**changes) -> SessionEntry:
    values = {
        "created_at": datetime(2026, 10, 13, 15, 0, 1),
        "tool": "EPUB 综合修复",
        "source": "D:/中文 图书/原稿.epub",
        "output": "D:/中文 图书/原稿_修复.epub",
        "success": True,
        "warnings": ("[calibre_missing] 未安装 Calibre",),
        "checks": (
            ("internal_structure", "passed"),
            ("calibre_metadata", "not_installed"),
            ("epubcheck", "not_run"),
        ),
    }
    values.update(changes)
    return SessionEntry(**values)


def test_export_writes_complete_readable_current_session_json(tmp_path: Path) -> None:
    folder = tmp_path / "中文 路径"
    folder.mkdir()
    destination = folder / "本次检测 报告.json"
    entries = (
        sample_entry(),
        sample_entry(
            tool="TXT 合并",
            source="D:/中文 图书/作者 原稿.txt",
            output=None,
            success=False,
            warnings=(),
            errors=("[invalid_source] 输入文件不可读",),
            checks=(),
        ),
    )

    result = export_session_report(
        entries,
        destination,
        exported_at=datetime(2026, 10, 13, 7, 2, 3, tzinfo=UTC),
    )

    assert result == destination
    raw = destination.read_text(encoding="utf-8")
    assert "中文 图书" in raw
    assert json.loads(raw) == {
        "schema_version": 1,
        "scope": "current_session",
        "exported_at": "2026-10-13T07:02:03+00:00",
        "entries": [
            {
                "created_at": "2026-10-13T15:00:01",
                "tool": "EPUB 综合修复",
                "source": "D:/中文 图书/原稿.epub",
                "output": "D:/中文 图书/原稿_修复.epub",
                "success": True,
                "warnings": ["[calibre_missing] 未安装 Calibre"],
                "errors": [],
                "checks": {
                    "internal_structure": "passed",
                    "calibre_metadata": "not_installed",
                    "epubcheck": "not_run",
                },
            },
            {
                "created_at": "2026-10-13T15:00:01",
                "tool": "TXT 合并",
                "source": "D:/中文 图书/作者 原稿.txt",
                "output": None,
                "success": False,
                "warnings": [],
                "errors": ["[invalid_source] 输入文件不可读"],
                "checks": {},
            },
        ],
    }


def test_export_refuses_to_overwrite_existing_original(tmp_path: Path) -> None:
    original = tmp_path / "中文 原稿.epub"
    original.write_bytes(b"original ebook bytes")

    with pytest.raises(FileExistsError):
        export_session_report((sample_entry(),), original)

    assert original.read_bytes() == b"original ebook bytes"


def test_export_refuses_hard_link_to_existing_original(tmp_path: Path) -> None:
    original = tmp_path / "原稿.epub"
    original.write_bytes(b"original ebook bytes")
    linked = tmp_path / "原稿别名.json"
    try:
        os.link(original, linked)
    except PermissionError:
        pytest.skip("Windows sandbox does not permit creating a hard-link fixture")

    with pytest.raises(FileExistsError):
        export_session_report((sample_entry(),), linked)

    assert original.read_bytes() == b"original ebook bytes"
    assert linked.read_bytes() == b"original ebook bytes"


def test_serialization_failure_does_not_create_destination(tmp_path: Path) -> None:
    destination = tmp_path / "不可序列化.json"
    malformed = sample_entry(tool=object())

    with pytest.raises(TypeError):
        export_session_report((malformed,), destination)

    assert not destination.exists()


def test_invalid_utf8_text_does_not_create_destination(tmp_path: Path) -> None:
    destination = tmp_path / "编码失败.json"
    malformed = sample_entry(warnings=("unpaired surrogate: \ud800",))

    with pytest.raises(UnicodeEncodeError):
        export_session_report((malformed,), destination)

    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows output names")
@pytest.mark.parametrize("name", ["原稿.epub:report.json", "NUL.json", "报告.json.", "报告.json "])
def test_export_rejects_windows_names_that_are_not_new_ordinary_files(
    tmp_path: Path, name: str
) -> None:
    original = tmp_path / "原稿.epub"
    original.write_bytes(b"original ebook bytes")

    with pytest.raises(ValueError, match="普通文件"):
        export_session_report((sample_entry(),), tmp_path / name)

    assert original.read_bytes() == b"original ebook bytes"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["原稿.epub"]


def test_file_created_during_export_is_never_overwritten(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "并发 报告.json"
    real_open = Path.open

    def create_competing_file(path, *args, **kwargs):
        if path == destination:
            with real_open(path, "w", encoding="utf-8") as stream:
                stream.write("另一个进程的文件")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", create_competing_file)

    with pytest.raises(FileExistsError):
        export_session_report((sample_entry(),), destination)

    with real_open(destination, encoding="utf-8") as stream:
        assert stream.read() == "另一个进程的文件"


def test_write_failure_preserves_a_replaced_destination(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "写入失败.json"
    real_open = Path.open

    class ReplacedWhileWriting:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, text):
            self.stream.write(text[:10])
            self.stream.close()
            destination.unlink()
            with real_open(destination, "w", encoding="utf-8") as replacement:
                replacement.write("别人替换的文件")
            raise OSError("模拟磁盘写入失败")

        def __exit__(self, *_exc):
            self.stream.close()

    def failing_open(path, *args, **kwargs):
        return ReplacedWhileWriting(real_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(OSError, match="模拟磁盘写入失败"):
        export_session_report((sample_entry(),), destination)

    with real_open(destination, encoding="utf-8") as stream:
        assert stream.read() == "别人替换的文件"
