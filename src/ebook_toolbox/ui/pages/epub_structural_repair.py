"""PySide6 page for sequential EPUB structural repair."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ebook_toolbox.services.epub_structural_repair import (
    BatchRepairRequest,
    BatchRepairResult,
    RepairError,
    RepairWarning,
    repair_epub_batch,
)

_SKIPPED_WARNING_CODES = frozenset({"input_skipped", "non_epub_skipped", "input_scan_failed"})


class _WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _StructuralRepairWorker(QRunnable):
    def __init__(self, request: BatchRepairRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = repair_epub_batch(self.request)
        except Exception as exc:  # pragma: no cover - guarded by the GUI state test
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


def _path_identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path)).casefold()


def _format_issue(issue: RepairWarning | RepairError) -> str:
    location = f" ({issue.path})" if issue.path is not None else ""
    return f"[{issue.code}]{location} {issue.message}"


class EpubStructuralRepairPage(QWidget):
    """Collect batch inputs and render the structural repair service result."""

    repair_finished = Signal(object)

    def __init__(self, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setObjectName("epubStructuralRepairPage")
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: _StructuralRepairWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(10)

        title = QLabel("EPUB 综合修复")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel(
            "修复 EPUB 内部路径、资源引用及 OPF 结构；批量任务按顺序处理，"
            "不会自动压缩图片或重建目录。"
        )
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        add_row = QHBoxLayout()
        self.add_epub_button = QPushButton("添加 EPUB…")
        self.add_epub_button.setObjectName("addStructuralRepairEpubButton")
        self.add_epub_button.clicked.connect(self._choose_epubs)
        self.add_folder_button = QPushButton("添加文件夹…")
        self.add_folder_button.setObjectName("addStructuralRepairFolderButton")
        self.add_folder_button.clicked.connect(self._choose_folder)
        add_row.addWidget(self.add_epub_button)
        add_row.addWidget(self.add_folder_button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        self.input_list = QListWidget()
        self.input_list.setObjectName("structuralRepairInputs")
        self.input_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.input_list.setMinimumHeight(84)
        self.input_list.setMaximumHeight(126)
        layout.addWidget(self.input_list)

        input_actions = QHBoxLayout()
        self.remove_selected_button = QPushButton("移除选中")
        self.remove_selected_button.setObjectName("removeStructuralRepairInputsButton")
        self.remove_selected_button.clicked.connect(self.remove_selected_inputs)
        self.clear_inputs_button = QPushButton("清空")
        self.clear_inputs_button.setObjectName("clearStructuralRepairInputsButton")
        self.clear_inputs_button.clicked.connect(self.clear_inputs)
        input_actions.addWidget(self.remove_selected_button)
        input_actions.addWidget(self.clear_inputs_button)
        input_actions.addStretch(1)
        layout.addLayout(input_actions)

        form = QFormLayout()
        form.setSpacing(8)

        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setObjectName("structuralRepairOutputDirectory")
        self.output_directory_edit.setPlaceholderText("留空时在每本原文件旁生成 *_repaired.epub")
        self.output_directory_button = QPushButton("浏览…")
        self.output_directory_button.setObjectName("browseStructuralRepairOutputButton")
        self.output_directory_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_directory_edit, 1)
        output_row.addWidget(self.output_directory_button)
        form.addRow("统一输出目录", output_row)

        self.reference_edit = QLineEdit()
        self.reference_edit.setObjectName("structuralRepairReferencePath")
        self.reference_edit.setPlaceholderText("可选；仅按明确 manifest 路径补充缺失资源")
        self.reference_button = QPushButton("浏览…")
        self.reference_button.setObjectName("browseStructuralRepairReferenceButton")
        self.reference_button.clicked.connect(self._choose_reference)
        self.clear_reference_button = QPushButton("清除")
        self.clear_reference_button.setObjectName("clearStructuralRepairReferenceButton")
        self.clear_reference_button.clicked.connect(self.reference_edit.clear)
        reference_row = QHBoxLayout()
        reference_row.addWidget(self.reference_edit, 1)
        reference_row.addWidget(self.reference_button)
        reference_row.addWidget(self.clear_reference_button)
        form.addRow("参考 EPUB", reference_row)
        layout.addLayout(form)

        options_row = QHBoxLayout()
        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.setObjectName("structuralRepairRecursive")
        self.overwrite_check = QCheckBox("覆盖已有输出")
        self.overwrite_check.setObjectName("structuralRepairOverwrite")
        self.calibre_check = QCheckBox("运行 Calibre 外部检查")
        self.calibre_check.setObjectName("structuralRepairCalibreCheck")
        self.image_spine_check = QCheckBox("将 spine 直接图片包装为 XHTML")
        self.image_spine_check.setObjectName("structuralRepairImageSpineWrap")
        self.image_spine_check.setToolTip(
            "默认仅检测并报告；启用后生成独立 XHTML 包装页，不修改原图片。"
        )
        options_row.addWidget(self.recursive_check)
        options_row.addWidget(self.overwrite_check)
        options_row.addWidget(self.calibre_check)
        options_row.addWidget(self.image_spine_check)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        self.start_button = QPushButton("开始修复")
        self.start_button.setObjectName("startStructuralRepairButton")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_repair)
        layout.addWidget(self.start_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("structuralRepairProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("请添加至少一个 EPUB 文件或文件夹。")
        self.status_label.setObjectName("structuralRepairStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.result_table = QTableWidget(0, 7)
        self.result_table.setObjectName("structuralRepairResults")
        self.result_table.setHorizontalHeaderLabels(
            ("原文件", "状态", "输出路径", "重命名", "补充资源", "警告", "错误信息")
        )
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setAlternatingRowColors(True)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.result_table, 1)

        self.summary_label = QLabel("总数：0　成功：0　失败：0　跳过：0")
        self.summary_label.setObjectName("structuralRepairSummary")
        layout.addWidget(self.summary_label)

        self.result_details = QPlainTextEdit()
        self.result_details.setObjectName("structuralRepairDetails")
        self.result_details.setReadOnly(True)
        self.result_details.setMaximumHeight(96)
        self.result_details.setPlaceholderText("结构化 warning 和 error 详情将在这里显示。")
        layout.addWidget(self.result_details)

        self._set_list_actions_enabled()

    def input_paths(self) -> tuple[Path, ...]:
        """Return the current input paths in their visible order."""

        return tuple(
            Path(str(self.input_list.item(row).data(Qt.ItemDataRole.UserRole)))
            for row in range(self.input_list.count())
        )

    def add_inputs(self, paths: Iterable[str | Path]) -> int:
        """Add distinct files or folders and return the number of new rows."""

        existing = {_path_identity(path) for path in self.input_paths()}
        added = 0
        for raw_path in paths:
            path = Path(raw_path)
            identity = _path_identity(path)
            if identity in existing:
                continue
            kind = "文件夹" if path.is_dir() else "EPUB"
            item = QListWidgetItem(f"{kind}　{path}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.input_list.addItem(item)
            existing.add(identity)
            added += 1
        self._set_list_actions_enabled()
        return added

    @Slot()
    def remove_selected_inputs(self) -> None:
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))
        self._set_list_actions_enabled()

    @Slot()
    def clear_inputs(self) -> None:
        self.input_list.clear()
        self._set_list_actions_enabled()

    def build_request(self) -> BatchRepairRequest:
        """Convert the page controls into the public batch request model."""

        inputs = self.input_paths()
        if not inputs:
            raise ValueError("请先添加至少一个 EPUB 文件或文件夹")
        output_text = self.output_directory_edit.text().strip()
        reference_text = self.reference_edit.text().strip()
        return BatchRepairRequest(
            inputs=inputs,
            output_directory=Path(output_text) if output_text else None,
            recursive=self.recursive_check.isChecked(),
            overwrite=self.overwrite_check.isChecked(),
            reference=Path(reference_text) if reference_text else None,
            run_calibre_check=self.calibre_check.isChecked(),
            wrap_image_spine=self.image_spine_check.isChecked(),
        )

    @Slot()
    def start_repair(self) -> None:
        if self._worker is not None:
            return
        try:
            request = self.build_request()
        except ValueError as exc:
            self._show_input_error(str(exc))
            return

        self.result_table.setRowCount(0)
        self.result_details.clear()
        self._set_summary(0, 0, 0, 0)
        self._set_running(True)
        worker = _StructuralRepairWorker(request)
        worker.signals.finished.connect(self._handle_result)
        worker.signals.failed.connect(self._handle_worker_failure)
        self._worker = worker
        self._thread_pool.start(worker)

    @Slot()
    def _choose_epubs(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self, "添加 EPUB", "", "EPUB 文件 (*.epub)"
        )
        self.add_inputs(paths)

    @Slot()
    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "添加文件夹", "")
        if path:
            self.add_inputs((path,))

    @Slot()
    def _choose_output_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择统一输出目录", self.output_directory_edit.text()
        )
        if path:
            self.output_directory_edit.setText(path)

    @Slot()
    def _choose_reference(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "选择参考 EPUB", self.reference_edit.text(), "EPUB 文件 (*.epub)"
        )
        if path:
            self.reference_edit.setText(path)

    def _set_list_actions_enabled(self) -> None:
        has_inputs = self.input_list.count() > 0
        idle = self._worker is None
        self.remove_selected_button.setEnabled(has_inputs and idle)
        self.clear_inputs_button.setEnabled(has_inputs and idle)
        self.start_button.setEnabled(has_inputs and idle)

    def _set_running(self, running: bool) -> None:
        for control in (
            self.add_epub_button,
            self.add_folder_button,
            self.input_list,
            self.remove_selected_button,
            self.clear_inputs_button,
            self.output_directory_edit,
            self.output_directory_button,
            self.reference_edit,
            self.reference_button,
            self.clear_reference_button,
            self.recursive_check,
            self.overwrite_check,
            self.calibre_check,
            self.image_spine_check,
            self.start_button,
        ):
            control.setEnabled(not running)
        if running:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("正在顺序修复 EPUB，请稍候…")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self._set_list_actions_enabled()

    def _show_input_error(self, message: str) -> None:
        self.status_label.setText("无法开始修复。")
        self.result_details.setPlainText(f"[input_error] {message}")

    def _release_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        for signal, slot in (
            (worker.signals.finished, self._handle_result),
            (worker.signals.failed, self._handle_worker_failure),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._worker = None

    def _append_result_row(
        self,
        source: str,
        status: str,
        output: str = "",
        renamed: int | str = "",
        restored: int | str = "",
        warnings: int | str = "",
        errors: str = "",
    ) -> None:
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        for column, value in enumerate(
            (source, status, output, renamed, restored, warnings, errors)
        ):
            item = QTableWidgetItem(str(value))
            if column in {3, 4, 5}:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.result_table.setItem(row, column, item)

    def _set_summary(self, total: int, succeeded: int, failed: int, skipped: int) -> None:
        self.summary_label.setText(
            f"总数：{total}　成功：{succeeded}　失败：{failed}　跳过：{skipped}"
        )

    @Slot(object)
    def _handle_result(self, result: BatchRepairResult) -> None:
        self._release_worker()
        self._set_running(False)
        self.result_table.setRowCount(0)
        details: list[str] = []

        succeeded = 0
        failed = 0
        for file_result in result.files:
            repair = file_result.result
            status = "成功" if file_result.success else "失败"
            succeeded += int(file_result.success)
            failed += int(not file_result.success)
            error_text = "\n".join(_format_issue(error) for error in file_result.errors)
            self._append_result_row(
                str(file_result.source),
                status,
                str(file_result.output),
                len(repair.renamed_resources),
                len(repair.restored_resources),
                len(file_result.warnings),
                error_text,
            )
            if file_result.warnings or file_result.errors:
                details.append(f"{file_result.source}：")
                details.extend(_format_issue(warning) for warning in file_result.warnings)
                details.extend(_format_issue(error) for error in file_result.errors)

        skipped_warnings = [
            warning for warning in result.warnings if warning.code in _SKIPPED_WARNING_CODES
        ]
        for warning in skipped_warnings:
            self._append_result_row(str(warning.path or ""), "跳过", warnings=1, errors="")

        if result.errors:
            self._append_result_row(
                "批量任务",
                "失败",
                errors="\n".join(_format_issue(error) for error in result.errors),
            )
            failed += 1

        if result.warnings:
            details.append("批量警告：")
            details.extend(_format_issue(warning) for warning in result.warnings)
        if result.errors:
            details.append("批量错误：")
            details.extend(_format_issue(error) for error in result.errors)
        self.result_details.setPlainText("\n".join(details))

        skipped = len(skipped_warnings)
        total = succeeded + failed + skipped
        self._set_summary(total, succeeded, failed, skipped)
        if result.errors and not result.files:
            self.status_label.setText("综合修复失败。")
        elif failed:
            self.status_label.setText("综合修复完成，但存在失败项。")
        elif total == 0:
            self.status_label.setText("未找到可处理的 EPUB 文件。")
        elif skipped:
            self.status_label.setText("综合修复完成，部分输入已跳过。")
        else:
            self.status_label.setText("综合修复完成。")
        self.repair_finished.emit(result)

    @Slot(str)
    def _handle_worker_failure(self, message: str) -> None:
        result = BatchRepairResult(errors=[RepairError("unexpected_error", message)])
        self._handle_result(result)


__all__ = ["EpubStructuralRepairPage"]
