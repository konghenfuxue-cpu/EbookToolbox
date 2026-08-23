"""PySide6 page for the EPUB table-of-contents repair service."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ebook_toolbox.services.epub_toc_repair import (
    TocEntry,
    TocRepairRequest,
    TocRepairResult,
    repair_epub_toc,
)


class _WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _TocRepairWorker(QRunnable):
    def __init__(self, request: TocRepairRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = repair_epub_toc(self.request)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.finished.emit(result)


def _format_issue(issue: object) -> str:
    code = getattr(issue, "code", "unknown")
    message = getattr(issue, "message", str(issue))
    path = getattr(issue, "path", None)
    location = f" ({path})" if path is not None else ""
    return f"[{code}]{location} {message}"


def _count_leaf_entries(entry: TocEntry) -> int:
    if not entry.children:
        return 1
    return sum(_count_leaf_entries(child) for child in entry.children)


def _entry_counts(entries: list[TocEntry]) -> tuple[int, int]:
    volumes = sum(bool(entry.children) for entry in entries)
    chapters = sum(_count_leaf_entries(entry) for entry in entries)
    return volumes, chapters


class EpubTocRepairPage(QWidget):
    """Collect one TOC repair request and render its structured result."""

    repair_finished = Signal(object)

    def __init__(self, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setObjectName("epubTocRepairPage")
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: _TocRepairWorker | None = None
        self._setting_output_suggestion = False
        self._output_is_suggestion = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("EPUB 目录修复")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel(
            "重建单个 EPUB 的 NCX 或 EPUB3 nav 目录，原始文件不会被修改。"
        )
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(12)

        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("tocRepairSourcePath")
        self.source_edit.setPlaceholderText("选择要修复的 .epub 文件")
        self.source_edit.textChanged.connect(self._suggest_output_path)
        self.source_button = QPushButton("浏览…")
        self.source_button.setObjectName("browseTocRepairSourceButton")
        self.source_button.clicked.connect(self._choose_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(self.source_button)
        form.addRow("EPUB 文件", source_row)

        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("tocRepairOutputPath")
        self.output_edit.setPlaceholderText("留空时在原文件旁生成 *_toc_fixed.epub")
        self.output_edit.textChanged.connect(self._track_output_change)
        self.output_button = QPushButton("浏览…")
        self.output_button.setObjectName("browseTocRepairOutputButton")
        self.output_button.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        form.addRow("输出文件", output_row)

        self.replace_existing_check = QCheckBox("重建已有目录")
        self.replace_existing_check.setObjectName("replaceExistingToc")
        self.replace_existing_check.setChecked(True)
        form.addRow("目录策略", self.replace_existing_check)
        layout.addLayout(form)

        self.start_button = QPushButton("开始修复")
        self.start_button.setObjectName("startTocRepairButton")
        self.start_button.clicked.connect(self.start_repair)
        layout.addWidget(self.start_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("tocRepairProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("请选择一个 EPUB 文件。")
        self.status_label.setObjectName("tocRepairStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.output_result = QLineEdit()
        self.output_result.setObjectName("tocRepairOutputResult")
        self.output_result.setReadOnly(True)
        self.output_result.setPlaceholderText("成功后在此显示输出文件")
        layout.addWidget(self.output_result)

        self.result_summary = QPlainTextEdit()
        self.result_summary.setObjectName("tocRepairResultSummary")
        self.result_summary.setReadOnly(True)
        self.result_summary.setPlaceholderText("识别结果、警告和错误将在这里显示。")
        layout.addWidget(self.result_summary, 1)

    def build_request(self) -> TocRepairRequest:
        """Convert current controls into the service's public request model."""

        source_text = self.source_edit.text().strip()
        if not source_text:
            raise ValueError("请选择 EPUB 文件")
        output_text = self.output_edit.text().strip()
        output = None
        if output_text and not self._output_is_suggestion:
            output = Path(output_text)
        return TocRepairRequest(
            source=Path(source_text),
            output=output,
            replace_existing=self.replace_existing_check.isChecked(),
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

        self._set_running(True)
        self.output_result.clear()
        self.result_summary.clear()
        worker = _TocRepairWorker(request)
        worker.signals.finished.connect(self._handle_result)
        worker.signals.failed.connect(self._handle_worker_failure)
        self._worker = worker
        self._thread_pool.start(worker)

    @Slot(str)
    def _suggest_output_path(self, source_text: str) -> None:
        current = self.output_edit.text().strip() if hasattr(self, "output_edit") else ""
        if current and not self._output_is_suggestion:
            return
        source_text = source_text.strip()
        suggestion = ""
        if source_text:
            source = Path(source_text)
            suggestion = str(source.with_name(f"{source.stem}_toc_fixed.epub"))
        self._setting_output_suggestion = True
        self.output_edit.setText(suggestion)
        self._setting_output_suggestion = False
        self._output_is_suggestion = bool(suggestion)

    @Slot(str)
    def _track_output_change(self, _text: str) -> None:
        if not self._setting_output_suggestion:
            self._output_is_suggestion = False

    @Slot()
    def _choose_source(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "选择 EPUB 文件", self.source_edit.text(), "EPUB 文件 (*.epub)"
        )
        if path:
            self.source_edit.setText(path)

    @Slot()
    def _choose_output(self) -> None:
        initial = self.output_edit.text() or self.source_edit.text()
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "选择输出文件", initial, "EPUB 文件 (*.epub)"
        )
        if path:
            self.output_edit.setText(path)

    def _set_running(self, running: bool) -> None:
        for control in (
            self.source_edit,
            self.source_button,
            self.output_edit,
            self.output_button,
            self.replace_existing_check,
            self.start_button,
        ):
            control.setEnabled(not running)
        if running:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("正在修复目录，请稍候…")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)

    def _show_input_error(self, message: str) -> None:
        self.status_label.setText("无法开始修复。")
        self.result_summary.setPlainText(f"[input_error] {message}")

    def _release_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        try:
            worker.signals.finished.disconnect(self._handle_result)
            worker.signals.failed.disconnect(self._handle_worker_failure)
        except (RuntimeError, TypeError):
            pass
        self._worker = None

    @Slot(object)
    def _handle_result(self, result: TocRepairResult) -> None:
        self._set_running(False)
        self._release_worker()
        lines: list[str] = []
        if result.success:
            self.status_label.setText("目录修复完成。")
            self.output_result.setText(str(result.output))
            volumes, chapters = _entry_counts(result.entries)
            modified_suffixes = {path.suffix.casefold() for path in result.modified_files}
            ncx_updated = ".ncx" in modified_suffixes
            nav_updated = bool(modified_suffixes & {".xhtml", ".html", ".htm"})
            lines.extend(
                (
                    f"识别卷数：{volumes}",
                    f"识别章节数：{chapters}",
                    f"NCX：{'已更新' if ncx_updated else '未更新'}",
                    f"EPUB3 nav：{'已更新' if nav_updated else '未更新'}",
                )
            )
        else:
            self.status_label.setText("目录修复失败。")
            self.output_result.clear()

        if result.warnings:
            lines.append("\n警告：")
            lines.extend(_format_issue(warning) for warning in result.warnings)
        if result.errors:
            lines.append("\n错误：")
            lines.extend(_format_issue(error) for error in result.errors)
        self.result_summary.setPlainText("\n".join(lines))
        self.repair_finished.emit(result)

    @Slot(str)
    def _handle_worker_failure(self, message: str) -> None:
        self._set_running(False)
        self._release_worker()
        self.status_label.setText("目录修复失败。")
        self.output_result.clear()
        self.result_summary.setPlainText(f"[unexpected_error] {message}")

