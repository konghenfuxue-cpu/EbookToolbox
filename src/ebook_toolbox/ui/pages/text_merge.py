"""GUI page for safe TXT formatting and author-grouped merging."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal, Slot
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

from ebook_toolbox.services.text_chapter import (
    TextChapterFileResult,
    TextChapterProgress,
    TextChapterRequest,
    normalize_text_file,
)
from ebook_toolbox.services.text_merge import (
    TextMergeProgress,
    TextMergeRequest,
    TextMergeResult,
    merge_author_texts,
)
from ebook_toolbox.ui.background_task import BackgroundTask


def _format_issue(issue: object) -> str:
    code = getattr(issue, "code", "unknown")
    message = getattr(issue, "message", str(issue))
    path = getattr(issue, "path", None)
    return f"[{code}]{f' ({path})' if path else ''} {message}"


class TextMergePage(QWidget):
    """Collect a source folder and publish per-author formatted TXT compilations."""

    merge_finished = Signal(object)
    chapter_finished = Signal(object)

    def __init__(self, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setObjectName("textMergePage")
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: BackgroundTask | None = None
        self._chapter_worker: BackgroundTask | None = None

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

        self.overwrite_check = QCheckBox("允许覆盖已有合集文件（始终保护输入原稿）")
        self.overwrite_check.setObjectName("textMergeOverwrite")
        layout.addWidget(self.overwrite_check)

        merge_actions = QHBoxLayout()
        self.start_button = QPushButton("开始排版并合并")
        self.start_button.setObjectName("startTextMergeButton")
        self.start_button.clicked.connect(self.start_merge)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelTextMergeButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("当前作者合集会安全完成，随后停止处理剩余作者。")
        self.cancel_button.clicked.connect(self.cancel_merge)
        merge_actions.addWidget(self.start_button)
        merge_actions.addWidget(self.cancel_button)
        merge_actions.addStretch(1)
        layout.addLayout(merge_actions)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("textMergeProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)
        self.merge_summary_label = QLabel("总数：0　成功：0　失败：0　跳过：0　取消：0")
        self.merge_summary_label.setObjectName("textMergeSummary")
        layout.addWidget(self.merge_summary_label)
        self.status_label = QLabel("请选择包含 TXT 文件的文件夹。")
        self.status_label.setObjectName("textMergeStatus")
        layout.addWidget(self.status_label)
        self.result_summary = QPlainTextEdit()
        self.result_summary.setObjectName("textMergeResultSummary")
        self.result_summary.setReadOnly(True)
        self.result_summary.setMaximumHeight(140)
        self.result_summary.setPlaceholderText("合并结果、跳过项、警告和错误将在这里显示。")
        layout.addWidget(self.result_summary)

        chapter_title = QLabel("TXT 卷章识别与规范化")
        chapter_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(chapter_title)
        chapter_description = QLabel(
            "识别标准卷、章/回/节及序章等标题，生成独立的“（卷章版）”副本。"
            "无法确定的正文不会被当成标题；原文件不会被修改。"
        )
        chapter_description.setWordWrap(True)
        layout.addWidget(chapter_description)
        chapter_form = QFormLayout()
        self.chapter_source_edit = QLineEdit()
        self.chapter_source_edit.setObjectName("textChapterSourceFile")
        chapter_source_button = QPushButton("浏览…")
        chapter_source_button.clicked.connect(self._choose_chapter_source)
        chapter_source_row = QHBoxLayout()
        chapter_source_row.addWidget(self.chapter_source_edit, 1)
        chapter_source_row.addWidget(chapter_source_button)
        chapter_form.addRow("输入 TXT", chapter_source_row)
        self.chapter_output_edit = QLineEdit()
        self.chapter_output_edit.setObjectName("textChapterOutputFile")
        self.chapter_output_edit.setPlaceholderText("留空时输出为同目录的（卷章版）副本")
        chapter_form.addRow("输出 TXT", self.chapter_output_edit)
        layout.addLayout(chapter_form)
        self.chapter_overwrite_check = QCheckBox("允许覆盖已有卷章版文件（始终保护输入原稿）")
        layout.addWidget(self.chapter_overwrite_check)
        self.chapter_start_button = QPushButton("开始卷章识别")
        self.chapter_start_button.setObjectName("startTextChapterButton")
        self.chapter_start_button.clicked.connect(self.start_chapter_normalization)
        self.chapter_cancel_button = QPushButton("取消")
        self.chapter_cancel_button.setObjectName("cancelTextChapterButton")
        self.chapter_cancel_button.setEnabled(False)
        self.chapter_cancel_button.setToolTip("完成当前安全阶段后停止，不会发布未完成的输出文件。")
        self.chapter_cancel_button.clicked.connect(self.cancel_chapter_normalization)
        chapter_actions = QHBoxLayout()
        chapter_actions.addWidget(self.chapter_start_button)
        chapter_actions.addWidget(self.chapter_cancel_button)
        chapter_actions.addStretch(1)
        layout.addLayout(chapter_actions)
        self.chapter_progress_bar = QProgressBar()
        self.chapter_progress_bar.setObjectName("textChapterProgress")
        self.chapter_progress_bar.setRange(0, 4)
        self.chapter_progress_bar.setValue(0)
        self.chapter_progress_bar.setTextVisible(True)
        layout.addWidget(self.chapter_progress_bar)
        self.chapter_status_label = QLabel("请选择要识别的 TXT 文件。")
        self.chapter_status_label.setObjectName("textChapterStatus")
        layout.addWidget(self.chapter_status_label)
        self.chapter_summary = QPlainTextEdit()
        self.chapter_summary.setObjectName("textChapterResultSummary")
        self.chapter_summary.setReadOnly(True)
        self.chapter_summary.setMaximumHeight(140)
        layout.addWidget(self.chapter_summary)

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
        self._worker = BackgroundTask(
            lambda task: merge_author_texts(
                request,
                progress_callback=task.report_progress,
                should_cancel=task.is_cancel_requested,
            )
        )
        self._worker.signals.finished.connect(self._handle_result)
        self._worker.signals.failed.connect(self._handle_worker_failure)
        self._worker.signals.progress.connect(self._handle_progress)
        self._thread_pool.start(self._worker)

    @Slot()
    def cancel_merge(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消；当前作者合集安全完成后将停止…")

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
        self.cancel_button.setEnabled(running)
        if running:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.status_label.setText("正在排版并合并 TXT，请稍候…")
        else:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 1)

    @Slot(object)
    def _handle_progress(self, progress: TextMergeProgress) -> None:
        self.progress_bar.setRange(0, max(1, progress.total))
        self.progress_bar.setValue(progress.completed)
        self._set_merge_summary(
            progress.total,
            progress.succeeded,
            progress.failed,
            progress.skipped,
            0,
        )
        if progress.current_source is not None and self.cancel_button.isEnabled():
            self.status_label.setText(
                f"正在处理 {progress.completed + 1}/{progress.total}："
                f"{progress.current_source.name}"
            )

    def _set_merge_summary(
        self, total: int, succeeded: int, failed: int, skipped: int, cancelled: int
    ) -> None:
        self.merge_summary_label.setText(
            f"总数：{total}　成功：{succeeded}　失败：{failed}　"
            f"跳过：{skipped}　取消：{cancelled}"
        )

    @Slot()
    def _choose_chapter_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 TXT 文件", "", "TXT 文件 (*.txt)")
        if path:
            self.chapter_source_edit.setText(path)

    @Slot()
    def start_chapter_normalization(self) -> None:
        if self._chapter_worker is not None:
            return
        source = self.chapter_source_edit.text().strip()
        if not source:
            self.chapter_summary.setPlainText("[input_error] 请选择输入 TXT 文件")
            return
        output = self.chapter_output_edit.text().strip()
        self.chapter_start_button.setEnabled(False)
        self.chapter_cancel_button.setEnabled(True)
        self.chapter_progress_bar.setRange(0, 4)
        self.chapter_progress_bar.setValue(0)
        self.chapter_status_label.setText("正在识别卷章：准备开始（0/4）")
        self.chapter_summary.clear()
        request = TextChapterRequest(
            Path(source),
            Path(output) if output else None,
            self.chapter_overwrite_check.isChecked(),
        )
        self._chapter_worker = BackgroundTask(
            lambda task: normalize_text_file(
                request,
                progress_callback=task.report_progress,
                should_cancel=task.is_cancel_requested,
            )
        )
        self._chapter_worker.signals.finished.connect(self._handle_chapter_result)
        self._chapter_worker.signals.failed.connect(self._handle_chapter_failure)
        self._chapter_worker.signals.progress.connect(self._handle_chapter_progress)
        self._thread_pool.start(self._chapter_worker)

    @Slot()
    def cancel_chapter_normalization(self) -> None:
        if self._chapter_worker is None or not self.chapter_cancel_button.isEnabled():
            return
        self._chapter_worker.cancel()
        self.chapter_cancel_button.setEnabled(False)
        self.chapter_status_label.setText(
            "正在取消；完成当前安全阶段后停止且不发布输出…"
        )

    @Slot(object)
    def _handle_chapter_progress(self, progress: TextChapterProgress) -> None:
        self.chapter_progress_bar.setRange(0, progress.total)
        self.chapter_progress_bar.setValue(progress.completed)
        stage_names = {
            "reading": "读取 TXT",
            "normalizing": "识别并规范卷章",
            "writing": "写入临时文件",
            "publishing": "原子发布",
            "completed": "完成",
        }
        if progress.stage == "publishing":
            self.chapter_cancel_button.setEnabled(False)
        if self.chapter_cancel_button.isEnabled() or progress.stage in {"publishing", "completed"}:
            stage = stage_names.get(progress.stage, progress.stage)
            self.chapter_status_label.setText(
                f"正在识别卷章：{stage}（{progress.completed}/{progress.total}）"
            )

    @Slot(object)
    def _handle_chapter_result(self, result: TextChapterFileResult) -> None:
        self._chapter_worker = None
        self.chapter_start_button.setEnabled(True)
        self.chapter_cancel_button.setEnabled(False)
        self.chapter_progress_bar.setRange(0, result.total_stages)
        self.chapter_progress_bar.setValue(result.completed_stages)
        normalized = result.normalized
        lines: list[str] = []
        if result.cancelled:
            self.chapter_status_label.setText(
                "卷章识别已取消；"
                f"已完成 {result.completed_stages}/{result.total_stages} 个阶段，未发布输出。"
            )
            lines.append("操作已安全取消，原文件和已有输出未被修改。")
        elif result.success:
            self.chapter_status_label.setText("卷章识别完成。")
            lines.append(f"输出：{result.output}")
        else:
            self.chapter_status_label.setText("卷章识别失败。")
        if normalized is not None:
            lines.extend(
                (
                    f"识别卷：{normalized.volume_count}",
                    f"识别章节：{normalized.chapter_count}",
                    f"特殊标题：{normalized.special_heading_count}",
                )
            )
        if result.warnings:
            lines.extend(("", "警告：", *(_format_issue(item) for item in result.warnings)))
        if result.errors:
            lines.extend(("", "错误：", *(_format_issue(item) for item in result.errors)))
        self.chapter_summary.setPlainText("\n".join(lines))
        self.chapter_finished.emit(result)

    @Slot(str)
    def _handle_chapter_failure(self, message: str) -> None:
        self._chapter_worker = None
        self.chapter_start_button.setEnabled(True)
        self.chapter_cancel_button.setEnabled(False)
        self.chapter_status_label.setText("卷章识别失败。")
        self.chapter_summary.setPlainText(f"[unexpected_error] {message}")

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
        if result.cancelled:
            lines.append(f"取消未处理：{result.cancelled_count} 个")
        lines.extend(
            f"{item.author}：{item.source_count} 篇 → {item.output.name}" for item in result.files
        )
        if result.warnings:
            lines.extend(("", "警告：", *(_format_issue(item) for item in result.warnings)))
        if result.errors:
            lines.extend(("", "错误：", *(_format_issue(item) for item in result.errors)))
        self.result_summary.setPlainText("\n".join(lines))
        if result.cancelled:
            self.status_label.setText(
                f"TXT 合并已取消；已处理 {result.processed_count} 个，"
                f"未处理 {result.cancelled_count} 个。"
            )
        else:
            self.status_label.setText(
                "TXT 合并完成。" if result.success else "TXT 合并未完成，请查看详情。"
            )
        self.progress_bar.setRange(0, max(1, result.planned_count))
        self.progress_bar.setValue(result.processed_count)
        self._set_merge_summary(
            result.planned_count,
            result.source_count,
            result.failed_source_count,
            result.skipped_source_count,
            result.cancelled_count,
        )
        self.merge_finished.emit(result)

    @Slot(str)
    def _handle_worker_failure(self, message: str) -> None:
        self._worker = None
        self._set_running(False)
        self.status_label.setText("TXT 合并失败。")
        self.result_summary.setPlainText(f"[unexpected_error] {message}")


__all__ = ["TextMergePage"]
