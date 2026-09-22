"""GUI page for safe TXT formatting and author-grouped merging."""

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

from ebook_toolbox.services.text_merge import TextMergeRequest, TextMergeResult, merge_author_texts


class _WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _TextMergeWorker(QRunnable):
    def __init__(self, request: TextMergeRequest) -> None:
        super().__init__()
        self._request = request
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(merge_author_texts(self._request))
        except Exception as exc:  # pragma: no cover - retained as GUI safety net
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def _format_issue(issue: object) -> str:
    code = getattr(issue, "code", "unknown")
    message = getattr(issue, "message", str(issue))
    path = getattr(issue, "path", None)
    return f"[{code}]{f' ({path})' if path else ''} {message}"


class TextMergePage(QWidget):
    """Collect a source folder and publish per-author formatted TXT compilations."""

    merge_finished = Signal(object)

    def __init__(self, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setObjectName("textMergePage")
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: _TextMergeWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)
        title = QLabel("TXT 排版并按作者合并")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel(
            "读取文件名以 [作者] 开头的 TXT，自动识别 UTF-8、UTF-16 和 GB18030，"
            "清理不可见字符并生成独立合集。原文件不会被修改。"
        )
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("textMergeSourceDirectory")
        self.source_edit.setPlaceholderText("选择包含 [作者] 文件名 TXT 的文件夹")
        source_button = QPushButton("浏览…")
        source_button.setObjectName("browseTextMergeSourceButton")
        source_button.clicked.connect(self._choose_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(source_button)
        form.addRow("输入文件夹", source_row)

        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("textMergeOutputDirectory")
        self.output_edit.setPlaceholderText("留空时写入输入目录下 EbookToolbox-合集")
        output_button = QPushButton("浏览…")
        output_button.setObjectName("browseTextMergeOutputButton")
        output_button.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_button)
        form.addRow("输出文件夹", output_row)
        layout.addLayout(form)

        self.overwrite_check = QCheckBox("允许覆盖已有合集文件")
        self.overwrite_check.setObjectName("textMergeOverwrite")
        layout.addWidget(self.overwrite_check)

        self.start_button = QPushButton("开始排版并合并")
        self.start_button.setObjectName("startTextMergeButton")
        self.start_button.clicked.connect(self.start_merge)
        layout.addWidget(self.start_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("textMergeProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("请选择包含 TXT 文件的文件夹。")
        self.status_label.setObjectName("textMergeStatus")
        layout.addWidget(self.status_label)
        self.result_summary = QPlainTextEdit()
        self.result_summary.setObjectName("textMergeResultSummary")
        self.result_summary.setReadOnly(True)
        self.result_summary.setPlaceholderText("合并结果、跳过项、警告和错误将在这里显示。")
        layout.addWidget(self.result_summary, 1)

    def build_request(self) -> TextMergeRequest:
        source = self.source_edit.text().strip()
        if not source:
            raise ValueError("请选择输入文件夹")
        output = self.output_edit.text().strip()
        return TextMergeRequest(
            source_directory=Path(source),
            output_directory=Path(output) if output else None,
            overwrite=self.overwrite_check.isChecked(),
        )

    @Slot()
    def start_merge(self) -> None:
        if self._worker is not None:
            return
        try:
            request = self.build_request()
        except ValueError as exc:
            self.status_label.setText("无法开始合并。")
            self.result_summary.setPlainText(f"[input_error] {exc}")
            return
        self._set_running(True)
        self.result_summary.clear()
        self._worker = _TextMergeWorker(request)
        self._worker.signals.finished.connect(self._handle_result)
        self._worker.signals.failed.connect(self._handle_worker_failure)
        self._thread_pool.start(self._worker)

    @Slot()
    def _choose_source(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 TXT 输入文件夹", self.source_edit.text()
        )
        if path:
            self.source_edit.setText(path)

    @Slot()
    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 TXT 输出文件夹", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        if running:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("正在排版并合并 TXT，请稍候…")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)

    @Slot(object)
    def _handle_result(self, result: TextMergeResult) -> None:
        self._worker = None
        self._set_running(False)
        lines = [
            f"输出目录：{result.output_directory}",
            f"生成合集：{len(result.files)} 个",
            f"合并 TXT：{result.source_count} 个",
            f"跳过文件：{result.skipped_count} 个",
        ]
        lines.extend(
            f"{item.author}：{item.source_count} 篇 → {item.output.name}" for item in result.files
        )
        if result.warnings:
            lines.extend(("", "警告：", *(_format_issue(item) for item in result.warnings)))
        if result.errors:
            lines.extend(("", "错误：", *(_format_issue(item) for item in result.errors)))
        self.result_summary.setPlainText("\n".join(lines))
        self.status_label.setText(
            "TXT 合并完成。" if result.success else "TXT 合并未完成，请查看详情。"
        )
        self.merge_finished.emit(result)

    @Slot(str)
    def _handle_worker_failure(self, message: str) -> None:
        self._worker = None
        self._set_running(False)
        self.status_label.setText("TXT 合并失败。")
        self.result_summary.setPlainText(f"[unexpected_error] {message}")


__all__ = ["TextMergePage"]
