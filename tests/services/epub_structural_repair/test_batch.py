from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

from ebook_toolbox.services.epub_structural_repair import (
    BatchRepairProgress,
    BatchRepairRequest,
    RepairResult,
    repair_epub_batch,
)
from ebook_toolbox.services.epub_structural_repair import (
    batch as batch_module,
)


def make_epub(path: Path) -> None:
    opf_path = "OEBPS/content.opf"
    chapter_path = "OEBPS/Text/chapter.xhtml"
    chapter_href = posixpath.relpath(chapter_path, posixpath.dirname(opf_path))
    container = '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'''
    opf = f'''<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试</dc:title>
      <dc:language>zh-CN</dc:language></metadata><manifest>
      <item id="chapter" href="{chapter_href}" media-type="application/xhtml+xml"/>
      </manifest><spine><itemref idref="chapter"/></spine></package>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr(opf_path, opf)
        archive.writestr(chapter_path, "<html xmlns='http://www.w3.org/1999/xhtml'/>")


def test_file_list_is_deduplicated_and_stably_sorted(tmp_path: Path) -> None:
    first = tmp_path / "b.epub"
    second = tmp_path / "A.epub"
    make_epub(first)
    make_epub(second)

    result = repair_epub_batch(BatchRepairRequest(inputs=(first, second, first)))

    assert [item.source for item in result.files] == [second.resolve(), first.resolve()]
    assert result.succeeded_count == 2


def test_folder_scan_respects_recursive_and_ignores_non_epub(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "nested"
    nested.mkdir(parents=True)
    top = root / "top.epub"
    child = nested / "child.epub"
    make_epub(top)
    make_epub(child)
    (root / "notes.txt").write_text("ignore", encoding="utf-8")

    shallow = repair_epub_batch(
        BatchRepairRequest(
            inputs=(root,), recursive=False, output_directory=tmp_path / "shallow-output"
        )
    )
    recursive = repair_epub_batch(
        BatchRepairRequest(
            inputs=(root,), recursive=True, output_directory=tmp_path / "recursive-output"
        )
    )

    assert [item.source for item in shallow.files] == [top.resolve()]
    assert [item.source for item in recursive.files] == [child.resolve(), top.resolve()]


def test_one_failure_does_not_stop_later_books(tmp_path: Path) -> None:
    broken = tmp_path / "a-broken.epub"
    valid = tmp_path / "b-valid.epub"
    broken.write_bytes(b"bad zip")
    make_epub(valid)

    result = repair_epub_batch(BatchRepairRequest(inputs=(valid, broken)))

    assert [item.source for item in result.files] == [broken.resolve(), valid.resolve()]
    assert not result.files[0].success
    assert result.files[1].success
    assert result.failed_count == 1
    assert result.succeeded_count == 1


def test_unified_output_directory_is_created_and_used(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    output_directory = tmp_path / "repaired"
    make_epub(source)

    result = repair_epub_batch(
        BatchRepairRequest(inputs=(source,), output_directory=output_directory)
    )

    assert result.success
    assert result.files[0].output == output_directory / "source_repaired.epub"
    assert result.files[0].output.is_file()


def test_same_named_sources_are_reported_as_output_conflicts(tmp_path: Path) -> None:
    first = tmp_path / "one" / "book.epub"
    second = tmp_path / "two" / "book.epub"
    output_directory = tmp_path / "output"
    first.parent.mkdir()
    second.parent.mkdir()
    make_epub(first)
    make_epub(second)

    result = repair_epub_batch(
        BatchRepairRequest(
            inputs=(second, first),
            output_directory=output_directory,
            overwrite=True,
        )
    )

    assert [item.source for item in result.files] == [first.resolve(), second.resolve()]
    assert all(not item.success for item in result.files)
    assert all(item.errors[0].code == "batch_output_conflict" for item in result.files)
    assert not (output_directory / "book_repaired.epub").exists()


def test_scheduler_continues_after_service_level_failure(tmp_path: Path, monkeypatch) -> None:
    sources = [tmp_path / "a.epub", tmp_path / "b.epub"]
    for source in sources:
        make_epub(source)
    calls: list[Path] = []

    def controlled_repair(request):
        calls.append(request.source)
        if request.source.name == "a.epub":
            raise RuntimeError("first failed outside the service boundary")
        repair = RepairResult(source=request.source, output=request.output or Path("unused"))
        repair.success = True
        return repair

    monkeypatch.setattr(batch_module, "repair_epub_structure", controlled_repair)

    result = repair_epub_batch(BatchRepairRequest(inputs=tuple(reversed(sources))))

    assert calls == [source.resolve() for source in sources]
    assert [item.success for item in result.files] == [False, True]
    assert result.files[0].errors[0].code == "unexpected_error"


def test_progress_reports_discovery_current_file_and_completion(
    tmp_path: Path, monkeypatch
) -> None:
    sources = [tmp_path / "a.epub", tmp_path / "b.epub"]
    for source in sources:
        make_epub(source)
    updates: list[BatchRepairProgress] = []

    def successful_repair(request):
        return RepairResult(
            source=request.source,
            output=request.output or Path("unused"),
            success=True,
        )

    monkeypatch.setattr(batch_module, "repair_epub_structure", successful_repair)

    result = repair_epub_batch(
        BatchRepairRequest(inputs=tuple(sources)), progress_callback=updates.append
    )

    assert result.planned_count == 2
    assert [(item.completed, item.current_source) for item in updates] == [
        (0, None),
        (0, sources[0].resolve()),
        (1, None),
        (1, sources[1].resolve()),
        (2, None),
    ]
    assert updates[-1].succeeded == 2
    assert updates[-1].failed == 0


def test_cancellation_stops_before_next_file_and_preserves_completed_result(
    tmp_path: Path, monkeypatch
) -> None:
    sources = [tmp_path / "a.epub", tmp_path / "b.epub", tmp_path / "c.epub"]
    for source in sources:
        make_epub(source)
    calls: list[Path] = []

    def successful_repair(request):
        calls.append(request.source)
        return RepairResult(
            source=request.source,
            output=request.output or Path("unused"),
            success=True,
        )

    monkeypatch.setattr(batch_module, "repair_epub_structure", successful_repair)

    result = repair_epub_batch(
        BatchRepairRequest(inputs=tuple(sources)),
        should_cancel=lambda: len(calls) == 1,
    )

    assert calls == [sources[0].resolve()]
    assert result.cancelled
    assert result.planned_count == 3
    assert result.cancelled_count == 2
    assert result.succeeded_count == 1
    assert not result.success
