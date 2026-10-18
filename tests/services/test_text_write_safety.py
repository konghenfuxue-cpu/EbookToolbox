"""Write-safety contracts, exclusively against generated tmp_path files."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from ebook_toolbox.services.text_chapter import TextChapterRequest, normalize_text_file
from ebook_toolbox.services.text_merge import TextMergeRequest, merge_author_texts


@pytest.fixture
def protect(record_property):
    """Assert and record hashes AND physical identities of protected artificial files."""
    saved = {}

    def snapshot(path):
        stat = path.stat()
        return [hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_dev, stat.st_ino]

    def capture(*paths):
        for path in paths:
            saved[path] = snapshot(path)

    yield capture
    after = {path: snapshot(path) for path in saved}
    record_property(
        "integrity",
        json.dumps(
            {str(path): {"before": before, "after": after[path]} for path, before in saved.items()},
            ensure_ascii=False,
        ),
    )
    assert after == saved, "Protected file content or identity changed"


def source_file(tmp_path, name="source.txt", encoding="utf-8"):
    source = tmp_path / name
    source.write_text("第一卷 开始\n【第1章】 标题", encoding=encoding)
    return source


def merge_inputs(tmp_path, authors):
    paths = [
        source_file(tmp_path, f"[{author}] {index}.txt") for index, author in enumerate(authors)
    ]
    return paths


@pytest.mark.parametrize("encoding", ["utf-8", "gb18030", "utf-16"])
def test_T01_normal_chapter_preserves_source(tmp_path, protect, encoding):
    source = source_file(tmp_path, encoding=encoding)
    protect(source)
    result = normalize_text_file(TextChapterRequest(source))
    assert result.success
    assert result.output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert result.output.read_text(encoding="utf-8-sig") == "第一卷 开始\n第1章 标题"


def test_T02_normal_author_merge_counts_content_and_source_integrity(tmp_path, protect):
    paths = merge_inputs(tmp_path, ["A", "A", "B"])
    protect(*paths)
    updates = []
    result = merge_author_texts(TextMergeRequest(tmp_path), progress_callback=updates.append)
    assert result.success and len(result.files) == 2 and result.source_count == 3
    assert result.processed_count == 3 and result.failed_source_count == 0
    assert updates[-1].succeeded == 3
    text = (result.output_directory / "A合集.txt").read_text(encoding="utf-8-sig")
    assert text.index("0") < text.index("1")
    assert text.count("【第1章】 标题") == 2


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("alias", ["same", "relative", "parent", "case"])
def test_T03_T04_T05_source_alias_is_rejected_before_writing(
    tmp_path, protect, monkeypatch, overwrite, alias
):
    source = source_file(tmp_path, "Source.txt")
    protect(source)
    monkeypatch.chdir(tmp_path)
    output = {
        "same": source,
        "relative": Path("Source.txt"),
        "parent": tmp_path / "child/../Source.txt",
        "case": tmp_path / "source.TXT",
    }[alias]
    (tmp_path / "child").mkdir()
    from ebook_toolbox.services.text_chapter import file as chapter

    def forbidden_temp(*args, **kwargs):
        pytest.fail("Rejection must precede temporary-file creation")

    monkeypatch.setattr(chapter.tempfile, "NamedTemporaryFile", forbidden_temp)
    result = normalize_text_file(TextChapterRequest(source, output, overwrite))
    assert not result.success and result.errors[0].code == "source_output_conflict"
    assert result.completed_stages == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Source.txt", "child"]


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("alias", ["hardlink", "symlink", "parent_symlink", "junction"])
def test_T06_T07_real_same_file_alias_is_rejected(tmp_path, protect, overwrite, alias):
    source = source_file(tmp_path)
    output = tmp_path / "alias.txt"
    try:
        if alias == "hardlink":
            os.link(source, output)  # Only an artificial alias; NEVER a conversion work copy.
        elif alias == "symlink":
            output.symlink_to(source)
        elif alias == "parent_symlink":
            (tmp_path / "alias_dir").symlink_to(tmp_path, target_is_directory=True)
            output = tmp_path / "alias_dir/source.txt"
        else:
            if os.name != "nt":
                pytest.skip("Windows junction only")
            import subprocess

            # Structured arguments, no command constructed from a user path.
            script = tmp_path / "junction.ps1"
            script.write_text(
                "param([string]$LinkPath,[string]$TargetPath)\n"
                "New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath "
                "-ErrorAction Stop | Out-Null\n",
                encoding="utf-8-sig",
            )
            completed = subprocess.run(
                [
                    r"C:\Program Files\PowerShell\7\pwsh.exe",
                    "-NoProfile",
                    "-File",
                    str(script),
                    "-LinkPath",
                    str(tmp_path / "alias_dir"),
                    "-TargetPath",
                    str(tmp_path),
                ],
                capture_output=True,
            )
            if completed.returncode:
                pytest.skip(
                    "Junction creation unavailable: " + completed.stderr.decode(errors="replace")
                )
            output = tmp_path / "alias_dir/source.txt"
    except OSError as exc:
        pytest.skip(f"Real {alias} creation unavailable: {exc}")
    assert source.samefile(output)
    protect(source, output)
    result = normalize_text_file(TextChapterRequest(source, output, overwrite))
    assert not result.success and result.errors[0].code == "source_output_conflict"


@pytest.mark.parametrize(
    "kind", ["directory", "dangling", "existing_symlink", "ads", "trailing", "reserved"]
)
def test_T08_unsafe_output_type_is_rejected(tmp_path, protect, kind):
    source = source_file(tmp_path)
    protect(source)
    output = tmp_path / "output.txt"
    if kind == "directory":
        output.mkdir()
    elif kind in {"dangling", "existing_symlink"}:
        target = tmp_path / "target.txt"
        if kind == "existing_symlink":
            target.write_text("keep", encoding="utf-8")
            protect(target)
        try:
            output.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"Symlink creation unavailable: {exc}")
    else:
        output = (
            tmp_path
            / {"ads": "output.txt:stream", "trailing": "output.txt.", "reserved": "NUL.txt"}[kind]
        )
    result = normalize_text_file(TextChapterRequest(source, output, True))
    assert not result.success and result.errors[0].code == "unsafe_output"


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("service", ["chapter", "merge"])
def test_T09_T10_existing_non_source_output_requires_explicit_permission(
    tmp_path, protect, overwrite, service
):
    source = source_file(tmp_path, "[A] 1.txt")
    output = tmp_path / ("out.txt" if service == "chapter" else "A合集.txt")
    output.write_text("old", encoding="utf-8")
    if not overwrite:
        protect(output)
    if service == "chapter":
        protect(source)
        result = normalize_text_file(TextChapterRequest(source, output, overwrite))
    else:
        # Existing output belongs outside the protected input directory.
        folder = tmp_path / "inputs"
        folder.mkdir()
        source.rename(folder / source.name)
        source = folder / source.name
        protect(source)
        result = merge_author_texts(TextMergeRequest(folder, tmp_path, overwrite))
    assert result.success == overwrite
    if overwrite:
        assert "第1章" in output.read_text(encoding="utf-8-sig")
    else:
        assert result.errors[0].code == "output_exists"


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("authors", [["A", "a", "0"], ["A", "A.", "0"]])
def test_T11_T12_entire_batch_rejects_colliding_output_names(tmp_path, protect, overwrite, authors):
    protect(*merge_inputs(tmp_path, authors))
    output = tmp_path / "out"
    result = merge_author_texts(TextMergeRequest(tmp_path, output, overwrite))
    assert not result.success and result.errors[0].code == "output_collision"
    assert not output.exists() and not result.files
    assert result.planned_count == result.processed_count == result.failed_source_count == 3


def test_T12_existing_targets_with_same_identity_reject_entire_batch(tmp_path, protect):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    protect(*merge_inputs(inputs, ["A", "B"]))
    first = source_file(tmp_path, "A合集.txt")
    second = tmp_path / "B合集.txt"
    try:
        os.link(first, second)
    except OSError as exc:
        pytest.skip(f"Hardlink creation unavailable: {exc}")
    protect(first, second)
    result = merge_author_texts(TextMergeRequest(inputs, tmp_path, True))
    assert not result.success and result.errors[0].code == "output_collision"
    assert not result.files


def test_T13_unmatched_input_is_also_protected(tmp_path, protect):
    protect(*merge_inputs(tmp_path, ["A"]))
    old = source_file(tmp_path, "A合集.txt")
    protect(old)
    result = merge_author_texts(TextMergeRequest(tmp_path, tmp_path, True))
    assert not result.success and result.errors[0].code == "source_output_conflict"
    assert not result.files


def test_T14_existing_conflict_blocks_other_valid_group(tmp_path, protect):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    protect(*merge_inputs(inputs, ["0", "Z"]))
    old = source_file(tmp_path, "Z合集.txt")
    protect(old)
    result = merge_author_texts(TextMergeRequest(inputs, tmp_path))
    assert not result.files and not (tmp_path / "0合集.txt").exists()
    assert result.errors[0].code == "output_exists"
    assert result.processed_count == result.failed_source_count == 2


@pytest.mark.parametrize("failure", ["decode", "read"])
def test_T15_input_read_failure_never_publishes_incomplete_author(
    tmp_path, protect, monkeypatch, failure
):
    from ebook_toolbox.services.text_merge import merge

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    good, bad = merge_inputs(inputs, ["A", "A"])
    if failure == "decode":
        bad.write_bytes(b"\xff")
    else:
        read = merge._read_text

        def denied(path):
            if path == bad:
                raise PermissionError("injected input denial")
            return read(path)

        monkeypatch.setattr(merge, "_read_text", denied)
    old = source_file(tmp_path, "A合集.txt")
    protect(good, bad, old)
    result = merge_author_texts(TextMergeRequest(inputs, tmp_path, True))
    assert not result.success and not result.files
    assert result.failed_source_count == 2 and result.processed_count == 2


@pytest.mark.parametrize("stage", ["reading", "writing", "publishing"])
def test_T23_cancellation_preserves_original_and_existing_output(tmp_path, protect, stage):
    source = source_file(tmp_path)
    output = source_file(tmp_path, "output.txt")
    protect(source, output)
    cancelled = False

    def observe(progress):
        nonlocal cancelled
        cancelled = progress.stage == stage

    result = normalize_text_file(
        TextChapterRequest(source, output, True),
        progress_callback=observe,
        should_cancel=lambda: cancelled,
    )
    assert result.cancelled and not result.success
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.txt", "source.txt"]


@pytest.mark.parametrize("service", ["chapter", "merge"])
def test_T22_callbacks_changing_cwd_cannot_redirect_io(tmp_path, protect, monkeypatch, service):
    source = source_file(tmp_path, "[A] 1.txt")
    protect(source)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(tmp_path)

    def change_cwd(progress):
        os.chdir(other)

    if service == "chapter":
        result = normalize_text_file(
            TextChapterRequest(Path(source.name), Path("output.txt")), progress_callback=change_cwd
        )
        expected = tmp_path / "output.txt"
    else:
        result = merge_author_texts(
            TextMergeRequest(Path("."), Path("out")), progress_callback=change_cwd
        )
        expected = tmp_path / "out/A合集.txt"
    assert result.success and expected.is_file()
    assert list(other.iterdir()) == []


@pytest.mark.parametrize("service", ["chapter", "merge"])
@pytest.mark.parametrize("failure", ["write", "replace"])
def test_T16_write_or_replace_failure_preserves_files(
    tmp_path, protect, monkeypatch, service, failure
):
    import tempfile

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = source_file(inputs, "[A] 1.txt")
    output = source_file(tmp_path, "A合集.txt")
    protect(source, output)
    if failure == "replace":

        def denied(*args, **kwargs):
            raise PermissionError("injected replace denial")

        monkeypatch.setattr(os, "replace", denied)
    else:
        real_temp = tempfile.NamedTemporaryFile

        class BrokenWriter:
            def __init__(self, *args, **kwargs):
                self.wrapped = real_temp(*args, **kwargs)
                self.name = self.wrapped.name

            def __enter__(self):
                self.wrapped.__enter__()
                return self

            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)

            def fileno(self):
                return self.wrapped.fileno()

            def write(self, text):
                self.wrapped.write(text[:2])
                raise OSError("injected disk write failure")

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", BrokenWriter)
    if service == "chapter":
        result = normalize_text_file(TextChapterRequest(source, output, True))
    else:
        result = merge_author_texts(TextMergeRequest(inputs, tmp_path, True))
    assert not result.success
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A合集.txt", "inputs"]


@pytest.mark.parametrize("overwrite", [False, True])
def test_T17_link_denial_uses_windows_no_clobber_rename(tmp_path, protect, monkeypatch, overwrite):
    source = source_file(tmp_path)
    protect(source)

    def denied(*args, **kwargs):
        raise PermissionError("injected link denial")

    monkeypatch.setattr(os, "link", denied)
    result = normalize_text_file(TextChapterRequest(source, tmp_path / "output.txt", overwrite))
    if os.name == "nt":
        assert result.success
        assert result.output.read_text(encoding="utf-8-sig").endswith("第1章 标题")
    else:
        assert not result.success


def test_T18_both_no_clobber_operations_fail_safely(tmp_path, protect, monkeypatch):
    source = source_file(tmp_path)
    protect(source)

    def denied(*args, **kwargs):
        raise PermissionError("injected publish denial")

    monkeypatch.setattr(os, "link", denied)
    monkeypatch.setattr(os, "rename", denied)
    result = normalize_text_file(TextChapterRequest(source))
    assert not result.success and list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("overwrite", [False, True])
def test_T19_new_target_appearing_after_plan_is_not_overwritten(tmp_path, protect, overwrite):
    source = source_file(tmp_path)
    output = tmp_path / "output.txt"
    protect(source)

    def observe(progress):
        if progress.stage == "publishing":
            output.write_text("appeared", encoding="utf-8")
            protect(output)

    result = normalize_text_file(
        TextChapterRequest(source, output, overwrite), progress_callback=observe
    )
    assert not result.success and result.errors[0].code == "output_changed"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.txt", "source.txt"]


@pytest.mark.parametrize("replacement", ["other", "input_link"])
def test_T20_approved_target_replaced_after_plan_is_not_overwritten(tmp_path, protect, replacement):
    source = source_file(tmp_path)
    output = source_file(tmp_path, "output.txt")
    protect(source)
    if replacement == "input_link":
        candidate = tmp_path / "candidate.txt"
        try:
            os.link(source, candidate)
        except OSError as exc:
            pytest.skip(f"Real hardlink creation unavailable: {exc}")

    def observe(progress):
        if progress.stage == "publishing":
            aside = tmp_path / "old.txt"
            output.rename(aside)
            protect(aside)
            if replacement == "other":
                output.write_text("new unrelated file", encoding="utf-8")
            else:
                candidate.rename(output)
            protect(output)

    result = normalize_text_file(
        TextChapterRequest(source, output, True), progress_callback=observe
    )
    assert not result.success
    assert result.errors[0].code == (
        "source_output_conflict" if replacement == "input_link" else "output_changed"
    )


@pytest.mark.parametrize("operation", ["stat", "resolve"])
def test_T21_unknown_filesystem_identity_fails_closed(tmp_path, protect, monkeypatch, operation):
    source = source_file(tmp_path)
    output = tmp_path / "output.txt"
    protect(source)
    original = getattr(Path, operation)

    def denied(path, *args, **kwargs):
        if path == output:
            raise PermissionError("injected identity lookup denial")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, operation, denied)
        result = normalize_text_file(TextChapterRequest(source, output, True))
    assert not result.success and result.errors[0].code == "identity_unavailable"
    assert list(tmp_path.iterdir()) == [source]


def test_T24_published_output_remains_success_when_owned_temp_cleanup_fails(
    tmp_path, protect, monkeypatch
):
    source = source_file(tmp_path)
    protect(source)

    # Exercise the real link success branch, via a non-overwriting artificial copy only
    # when the sandbox cannot create links. Publication contract still runs in full.
    def create_link(source_path, destination):
        with open(destination, "xb") as handle:
            handle.write(Path(source_path).read_bytes())

    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("injected temp cleanup denial")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "link", create_link)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        result = normalize_text_file(TextChapterRequest(source))
    assert result.success and result.output.is_file()
    assert any(issue.code == "temp_cleanup_failed" for issue in result.warnings)
    assert len(list(tmp_path.glob("*.tmp"))) == 1


def test_T24_substituted_temporary_path_is_preserved_and_not_published(tmp_path, protect):
    source = source_file(tmp_path)
    protect(source)

    def observe(progress):
        if progress.stage == "publishing":
            temporary = next(tmp_path.glob("*.tmp"))
            temporary.rename(tmp_path / "owned-aside.tmp")
            temporary.write_text("other file", encoding="utf-8")
            protect(temporary)

    result = normalize_text_file(TextChapterRequest(source), progress_callback=observe)
    assert not result.success and result.errors[0].code == "temporary_changed"
    assert not (tmp_path / "source（卷章版）.txt").exists()


def test_T18_non_windows_link_failure_does_not_use_overwriting_rename(
    tmp_path, protect, monkeypatch
):
    from ebook_toolbox.services import text_write_safety as safety

    source = source_file(tmp_path)
    protect(source)
    monkeypatch.setattr(safety, "_WINDOWS", False)

    def denied(*args, **kwargs):
        raise PermissionError("injected link denial")

    monkeypatch.setattr(os, "link", denied)
    result = normalize_text_file(TextChapterRequest(source))
    assert not result.success and list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("overwrite", [False, True])
def test_T19_target_created_inside_link_failure_survives_rename_fallback(
    tmp_path, protect, monkeypatch, overwrite
):
    source = source_file(tmp_path)
    output = tmp_path / "output.txt"
    protect(source)

    def appeared(temporary, destination):
        assert Path(destination) == output
        output.write_text("racing file", encoding="utf-8")
        protect(output)
        raise PermissionError("link denied after target appeared")

    monkeypatch.setattr(os, "link", appeared)
    result = normalize_text_file(TextChapterRequest(source, output, overwrite))
    assert not result.success
    assert output.read_text(encoding="utf-8") == "racing file"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.txt", "source.txt"]


def test_T16_later_author_failure_preserves_successfully_published_author(
    tmp_path, protect, monkeypatch
):
    from ebook_toolbox.services.text_merge import merge

    protect(*merge_inputs(tmp_path, ["A", "B"]))
    original = merge.publish_text

    def fail_second(temporary, output, *, plan):
        if output.name == "B合集.txt":
            raise PermissionError("injected second publication failure")
        original(temporary, output, plan=plan)

    monkeypatch.setattr(merge, "publish_text", fail_second)
    result = merge_author_texts(TextMergeRequest(tmp_path))
    assert not result.success and len(result.files) == 1
    assert result.source_count == result.failed_source_count == 1
    assert sorted(p.name for p in result.output_directory.iterdir()) == ["A合集.txt"]


def test_T20_input_changed_before_publish_preserves_existing_output(tmp_path, protect):
    source = source_file(tmp_path)
    output = source_file(tmp_path, "output.txt")
    protect(output)

    def changed(progress):
        if progress.stage == "publishing":
            source.write_text("changed artificial input", encoding="utf-8")
            protect(source)  # The simulated external change must be preserved.

    result = normalize_text_file(
        TextChapterRequest(source, output, True), progress_callback=changed
    )
    assert not result.success and result.errors[0].code == "input_changed"


@pytest.mark.parametrize("operation", ["resolve", "stat"])
def test_T21_source_identity_lookup_failure_blocks_output(
    tmp_path, protect, monkeypatch, operation
):
    source = source_file(tmp_path)
    protect(source)
    original = getattr(Path, operation)

    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError("source identity denied")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, operation, denied)
        result = normalize_text_file(TextChapterRequest(source))
    assert not result.success and result.errors[0].code == "identity_unavailable"
    assert list(tmp_path.iterdir()) == [source]


def test_T24_cancelled_conversion_reports_temp_cleanup_failure(tmp_path, protect, monkeypatch):
    source = source_file(tmp_path)
    protect(source)
    original = Path.unlink
    cancelled = False

    def observe(progress):
        nonlocal cancelled
        cancelled = progress.stage == "publishing"

    def denied(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("injected cleanup denial on cancellation")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        result = normalize_text_file(
            TextChapterRequest(source), progress_callback=observe, should_cancel=lambda: cancelled
        )
    assert result.cancelled and not result.success
    assert any(issue.code == "temp_cleanup_failed" for issue in result.errors)
    assert len(list(tmp_path.glob("*.tmp"))) == 1
