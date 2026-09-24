from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer

from ebook_toolbox.services.epub_structural_repair import (
    BatchRepairResult,
    FileRepairResult,
    RenamedResource,
    RepairError,
    RepairResult,
    RepairWarning,
)
from ebook_toolbox.ui.pages import epub_structural_repair as page_module
from ebook_toolbox.ui.pages.epub_structural_repair import EpubStructuralRepairPage


def _cell_text(page: EpubStructuralRepairPage, row: int, column: int) -> str:
    item = page.result_table.item(row, column)
    assert item is not None
    return item.text()


def test_page_initializes_with_safe_defaults(qtbot) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)

    assert page.objectName() == "epubStructuralRepairPage"
    assert page.input_list.count() == 0
    assert page.output_directory_edit.text() == ""
    assert page.reference_edit.text() == ""
    assert not page.recursive_check.isChecked()
    assert not page.overwrite_check.isChecked()
    assert not page.calibre_check.isChecked()
    assert not page.image_spine_check.isChecked()
    assert not page.start_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert not page.remove_selected_button.isEnabled()
    assert not page.clear_inputs_button.isEnabled()
    assert page.progress_bar.minimum() == 0
    assert page.progress_bar.maximum() == 1
    assert page.result_table.rowCount() == 0
    assert page.summary_label.text() == "总数：0　成功：0　失败：0　跳过：0　取消：0"
    assert page.result_details.isReadOnly()


def test_add_epub_button_accepts_multiple_files(qtbot, tmp_path, monkeypatch) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    first = tmp_path / "第一本.epub"
    second = tmp_path / "第二本.epub"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getOpenFileNames",
        lambda *_args, **_kwargs: ([str(first), str(second)], "EPUB 文件 (*.epub)"),
    )

    qtbot.mouseClick(page.add_epub_button, Qt.MouseButton.LeftButton)

    assert page.input_paths() == (first, second)
    assert page.start_button.isEnabled()


def test_add_folder_button_adds_directory(qtbot, tmp_path, monkeypatch) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    folder = tmp_path / "书库"
    folder.mkdir()
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(folder),
    )

    qtbot.mouseClick(page.add_folder_button, Qt.MouseButton.LeftButton)

    assert page.input_paths() == (folder,)
    assert "文件夹" in page.input_list.item(0).text()


def test_inputs_are_deduplicated_and_can_be_removed_or_cleared(qtbot, tmp_path) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    first = tmp_path / "one.epub"
    second = tmp_path / "two.epub"
    third = tmp_path / "three.epub"

    assert page.add_inputs((first, str(first), second, third, second)) == 3
    assert page.input_paths() == (first, second, third)

    page.input_list.item(0).setSelected(True)
    page.input_list.item(1).setSelected(True)
    qtbot.mouseClick(page.remove_selected_button, Qt.MouseButton.LeftButton)
    assert page.input_paths() == (third,)

    qtbot.mouseClick(page.clear_inputs_button, Qt.MouseButton.LeftButton)
    assert page.input_paths() == ()
    assert not page.start_button.isEnabled()
    assert not page.remove_selected_button.isEnabled()
    assert not page.clear_inputs_button.isEnabled()


def test_controls_map_to_public_batch_request(qtbot, tmp_path) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    source = tmp_path / "source.epub"
    folder = tmp_path / "collection"
    output_directory = tmp_path / "repaired"
    reference = tmp_path / "reference.epub"
    folder.mkdir()
    page.add_inputs((source, folder))

    default_request = page.build_request()
    assert default_request.output_directory is None
    assert default_request.reference is None

    page.output_directory_edit.setText(str(output_directory))
    page.reference_edit.setText(str(reference))
    page.recursive_check.setChecked(True)
    page.overwrite_check.setChecked(True)
    page.calibre_check.setChecked(True)
    page.image_spine_check.setChecked(True)

    request = page.build_request()

    assert request.inputs == (source, folder)
    assert request.output_directory == output_directory
    assert request.reference == reference
    assert request.recursive
    assert request.overwrite
    assert request.run_calibre_check
    assert request.wrap_image_spine


def test_missing_inputs_cannot_start_worker(qtbot, monkeypatch) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    called = False

    def unexpected_call(_request):
        nonlocal called
        called = True
        raise AssertionError("service must not be called")

    monkeypatch.setattr(page_module, "repair_epub_batch", unexpected_call)

    assert not page.start_button.isEnabled()
    page.start_repair()

    assert not called
    assert page._worker is None
    assert page.status_label.text() == "无法开始修复。"
    assert "[input_error] 请先添加至少一个 EPUB 文件或文件夹" in (page.result_details.toPlainText())


def test_background_run_keeps_gui_responsive_and_restores_controls(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    page.add_inputs((source,))
    worker_started = threading.Event()
    release_worker = threading.Event()
    main_thread = threading.get_ident()

    def repair_in_background(request, **kwargs):
        assert threading.get_ident() != main_thread
        assert request.inputs == (source,)
        worker_started.set()
        assert release_worker.wait(timeout=5)
        output = tmp_path / "source_repaired.epub"
        repair = RepairResult(source=source, output=output, success=True)
        return BatchRepairResult(files=[FileRepairResult(source, output, repair)])

    monkeypatch.setattr(page_module, "repair_epub_batch", repair_in_background)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)

    assert page._worker is not None
    assert page.progress_bar.maximum() == 0
    assert page.cancel_button.isEnabled()
    controls = (
        page.add_epub_button,
        page.add_folder_button,
        page.input_list,
        page.remove_selected_button,
        page.clear_inputs_button,
        page.output_directory_edit,
        page.output_directory_button,
        page.reference_edit,
        page.reference_button,
        page.clear_reference_button,
        page.recursive_check,
        page.overwrite_check,
        page.calibre_check,
        page.image_spine_check,
        page.start_button,
    )
    assert all(not control.isEnabled() for control in controls)

    gui_responded = threading.Event()
    QTimer.singleShot(0, gui_responded.set)
    qtbot.waitUntil(gui_responded.is_set, timeout=1000)

    with qtbot.waitSignal(page.repair_finished, timeout=5000):
        release_worker.set()

    assert page._worker is None
    assert all(control.isEnabled() for control in controls)
    assert page.progress_bar.maximum() == 1
    assert not page.cancel_button.isEnabled()
    assert page.status_label.text() == "综合修复完成。"


def test_result_table_renders_success_failure_warning_and_summary(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    page.show()
    good_source = tmp_path / "good.epub"
    bad_source = tmp_path / "bad.epub"
    skipped_source = tmp_path / "notes.txt"
    page.add_inputs((good_source, bad_source))

    def mixed_result(_request, **_kwargs):
        good_output = tmp_path / "good_repaired.epub"
        bad_output = tmp_path / "bad_repaired.epub"
        good = RepairResult(
            source=good_source,
            output=good_output,
            success=True,
            renamed_resources=[RenamedResource("Text/a?.xhtml", "Text/a_.xhtml")],
            restored_resources=["Images/missing.jpg"],
            warnings=[RepairWarning("language_added", "已补充语言", Path("book.opf"))],
        )
        bad = RepairResult(
            source=bad_source,
            output=bad_output,
            warnings=[RepairWarning("manifest_pruned", "移除失效资源")],
            errors=[RepairError("invalid_container", "container.xml 无效")],
        )
        return BatchRepairResult(
            files=[
                FileRepairResult(good_source, good_output, good),
                FileRepairResult(bad_source, bad_output, bad),
            ],
            warnings=[RepairWarning("non_epub_skipped", "不是 EPUB，已跳过", skipped_source)],
        )

    monkeypatch.setattr(page_module, "repair_epub_batch", mixed_result)

    with qtbot.waitSignal(page.repair_finished, timeout=5000):
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)

    assert page.result_table.rowCount() == 3
    assert [_cell_text(page, 0, column) for column in range(7)] == [
        str(good_source),
        "成功",
        str(tmp_path / "good_repaired.epub"),
        "1",
        "1",
        "1",
        "",
    ]
    assert _cell_text(page, 1, 0) == str(bad_source)
    assert _cell_text(page, 1, 1) == "失败"
    assert _cell_text(page, 1, 5) == "1"
    assert "[invalid_container] container.xml 无效" in _cell_text(page, 1, 6)
    assert _cell_text(page, 2, 0) == str(skipped_source)
    assert _cell_text(page, 2, 1) == "跳过"
    assert page.summary_label.text() == "总数：3　成功：1　失败：1　跳过：1　取消：0"
    assert page.status_label.text() == "综合修复完成，但存在失败项。"
    details = page.result_details.toPlainText()
    assert "[language_added] (book.opf) 已补充语言" in details
    assert "[manifest_pruned] 移除失效资源" in details
    assert "[invalid_container] container.xml 无效" in details
    assert f"[non_epub_skipped] ({skipped_source}) 不是 EPUB，已跳过" in details


def test_background_exception_restores_page_and_displays_structured_error(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    page.add_inputs((source,))

    def raise_unexpectedly(_request, **_kwargs):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(page_module, "repair_epub_batch", raise_unexpectedly)

    with qtbot.waitSignal(page.repair_finished, timeout=5000) as completed:
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)

    result = completed.args[0]
    assert page._worker is None
    assert page.start_button.isEnabled()
    assert page.add_epub_button.isEnabled()
    assert page.input_list.isEnabled()
    assert result.errors == [RepairError("unexpected_error", "worker exploded")]
    assert page.status_label.text() == "综合修复失败。"
    assert page.result_table.rowCount() == 1
    assert _cell_text(page, 0, 1) == "失败"
    assert "[unexpected_error] worker exploded" in _cell_text(page, 0, 6)
    assert page.summary_label.text() == "总数：1　成功：0　失败：1　跳过：0　取消：0"
    assert "[unexpected_error] worker exploded" in page.result_details.toPlainText()


def test_cancel_button_requests_safe_stop_and_renders_cancelled_count(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubStructuralRepairPage()
    qtbot.addWidget(page)
    page.show()
    sources = (tmp_path / "a.epub", tmp_path / "b.epub")
    page.add_inputs(sources)
    worker_started = threading.Event()
    release_current = threading.Event()

    def cancellable_batch(request, *, progress_callback, should_cancel):
        progress_callback(page_module.BatchRepairProgress(2, 0, 0, 0, sources[0]))
        worker_started.set()
        assert release_current.wait(timeout=5)
        output = tmp_path / "a_repaired.epub"
        repair = RepairResult(source=sources[0], output=output, success=True)
        result = BatchRepairResult(
            files=[FileRepairResult(sources[0], output, repair)], planned_count=2
        )
        result.cancelled = should_cancel()
        return result

    monkeypatch.setattr(page_module, "repair_epub_batch", cancellable_batch)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)
    qtbot.waitUntil(lambda: page.progress_bar.maximum() == 2, timeout=1000)
    assert page.status_label.text().startswith("正在处理 1/2")

    qtbot.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)
    assert not page.cancel_button.isEnabled()
    assert page.status_label.text().startswith("正在取消")

    with qtbot.waitSignal(page.repair_finished, timeout=5000):
        release_current.set()

    assert page.status_label.text() == "综合修复已取消；已完成 1 项，未处理 1 项。"
    assert page.summary_label.text() == "总数：2　成功：1　失败：0　跳过：0　取消：1"
    assert page.progress_bar.maximum() == 2
    assert page.progress_bar.value() == 1
