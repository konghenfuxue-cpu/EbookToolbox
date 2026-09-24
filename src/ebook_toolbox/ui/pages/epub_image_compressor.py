"""PySide6 page for the EPUB image compressor service."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from ebook_toolbox.services.epub_image_compressor import (
    BALANCED,
    HIGH_QUALITY,
    PROFILES,
    SPACE_SAVER,
    CompressionProgress,
    CompressionRequest,
    CompressionResult,
    compress_epub,
)
from ebook_toolbox.ui.background_task import BackgroundTask

PROFILE_LABELS = {
    BALANCED.name: "balanced（均衡）",
    HIGH_QUALITY.name: "high_quality（高质量）",
    SPACE_SAVER.name: "space_saver（节省空间）",
}


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def _format_issue(issue: object) -> str:
    code = getattr(issue, "code", "unknown")
    message = getattr(issue, "message", str(issue))
    path = getattr(issue, "path", None)
    location = f" ({path})" if path is not None else ""
    return f"[{code}]{location} {message}"


class EpubImageCompressorPage(QWidget):
    """Collect one compression request and render its structured result."""

    compression_finished = Signal(object)

    def __init__(self, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setObjectName("epubImageCompressorPage")
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: BackgroundTask | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("EPUB 图片压缩")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel("压缩单个 EPUB 中的 JPEG、PNG 和 WebP 图片，原始文件不会被修改。")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(12)

        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("epubSourcePath")
        self.source_edit.setPlaceholderText("选择要处理的 .epub 文件")
        source_button = QPushButton("浏览…")
        source_button.setObjectName("browseEpubButton")
        source_button.clicked.connect(self._choose_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(source_button)
        form.addRow("EPUB 文件", source_row)

        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("epubOutputPath")
        self.output_edit.setPlaceholderText("留空时在原文件旁生成 *_optimized.epub")
        output_button = QPushButton("浏览…")
        output_button.setObjectName("browseOutputButton")
        output_button.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_button)
        form.addRow("输出文件", output_row)

        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("compressionProfile")
        for profile in (BALANCED, HIGH_QUALITY, SPACE_SAVER):
            self.profile_combo.addItem(PROFILE_LABELS[profile.name], profile.name)
        form.addRow("压缩模式", self.profile_combo)

        self.include_cover_check = QCheckBox("同时处理封面图片")
        self.include_cover_check.setObjectName("includeCover")
        self.include_cover_check.setChecked(False)
        form.addRow("封面", self.include_cover_check)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("开始压缩")
        self.start_button.setObjectName("startCompressionButton")
        self.start_button.clicked.connect(self.start_compression)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelCompressionButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("当前图片或安全阶段完成后取消，不会发布半成品 EPUB。")
        self.cancel_button.clicked.connect(self.cancel_compression)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("compressionProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        self.summary_label = QLabel("总数：0　替换：0　跳过：0　失败：0　取消：0")
        self.summary_label.setObjectName("compressionSummary")
        layout.addWidget(self.summary_label)

        self.status_label = QLabel("请选择一个 EPUB 文件。")
        self.status_label.setObjectName("compressionStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.output_result = QLineEdit()
        self.output_result.setObjectName("compressionOutputResult")
        self.output_result.setReadOnly(True)
        self.output_result.setPlaceholderText("成功后在此显示输出文件")
        layout.addWidget(self.output_result)

        self.result_summary = QPlainTextEdit()
        self.result_summary.setObjectName("compressionResultSummary")
        self.result_summary.setReadOnly(True)
        self.result_summary.setPlaceholderText("压缩结果、警告和错误将在这里显示。")
        layout.addWidget(self.result_summary, 1)

    def build_request(self) -> CompressionRequest:
        """Convert current controls into the service's public request model."""

        source_text = self.source_edit.text().strip()
        if not source_text:
            raise ValueError("请选择 EPUB 文件")
        output_text = self.output_edit.text().strip()
        profile_name = self.profile_combo.currentData()
        return CompressionRequest(
            source=Path(source_text),
            output=Path(output_text) if output_text else None,
            profile=PROFILES[str(profile_name)],
            include_cover=self.include_cover_check.isChecked(),
        )

    @Slot()
    def start_compression(self) -> None:
        try:
            request = self.build_request()
        except (KeyError, ValueError) as exc:
            self._show_input_error(str(exc))
            return

        self._set_running(True)
        self.output_result.clear()
        self.result_summary.clear()
        worker = BackgroundTask(
            lambda task: compress_epub(
                request,
                progress_callback=task.report_progress,
                should_cancel=task.is_cancel_requested,
            )
        )
        worker.signals.finished.connect(self._handle_result)
        worker.signals.failed.connect(self._handle_worker_failure)
        worker.signals.progress.connect(self._handle_progress)
        self._worker = worker
        self._thread_pool.start(worker)

    @Slot()
    def cancel_compression(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消；完成当前安全阶段后停止且不发布输出…")

    @Slot()
    def _choose_source(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "选择 EPUB 文件", self.source_edit.text(), "EPUB 文件 (*.epub)"
        )
        if not path:
            return
        self.source_edit.setText(path)
        if not self.output_edit.text().strip():
            source = Path(path)
            self.output_edit.setText(str(source.with_name(f"{source.stem}_optimized.epub")))

    @Slot()
    def _choose_output(self) -> None:
        initial = self.output_edit.text() or self.source_edit.text()
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "选择输出文件", initial, "EPUB 文件 (*.epub)"
        )
        if path:
            self.output_edit.setText(path)

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        if running:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.status_label.setText("正在压缩，请稍候…")
        else:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 1)

    def _set_summary(
        self, total: int, replaced: int, skipped: int, failed: int, cancelled: int
    ) -> None:
        self.summary_label.setText(
            f"总数：{total}　替换：{replaced}　跳过：{skipped}　"
            f"失败：{failed}　取消：{cancelled}"
        )

    @Slot(object)
    def _handle_progress(self, progress: CompressionProgress) -> None:
        self.progress_bar.setRange(0, max(1, progress.total))
        self.progress_bar.setValue(progress.completed)
        self._set_summary(
            progress.total,
            progress.replaced,
            progress.skipped,
            progress.failed,
            0,
        )
        if not self.cancel_button.isEnabled():
            return
        if progress.current_image is not None:
            self.status_label.setText(
                f"正在处理 {progress.completed + 1}/{progress.total}："
                f"{progress.current_image.name}"
            )
            return
        stage_messages = {
            "building": "正在重建 EPUB…",
            "validating": "正在验证输出 EPUB…",
            "publishing": "正在原子发布输出 EPUB，此阶段无法取消…",
        }
        if progress.stage in stage_messages:
            if progress.stage == "publishing":
                self.cancel_button.setEnabled(False)
            self.status_label.setText(stage_messages[progress.stage])

    def _show_input_error(self, message: str) -> None:
        self.status_label.setText("无法开始压缩。")
        self.result_summary.setPlainText(f"[input_error] {message}")

    @Slot(object)
    def _handle_result(self, result: CompressionResult) -> None:
        self._set_running(False)
        self._worker = None
        lines: list[str] = []
        if result.cancelled:
            self.status_label.setText(
                f"图片压缩已取消；已处理 {result.processed_count} 张，"
                f"未处理 {result.cancelled_count} 张，未发布输出。"
            )
            self.output_result.clear()
            lines.append(f"取消未处理：{result.cancelled_count} 张")
        elif result.success:
            self.status_label.setText("压缩完成。")
            self.output_result.setText(str(result.output))
            lines.extend(
                (
                    f"原始大小：{_format_bytes(result.original_bytes)}",
                    f"输出大小：{_format_bytes(result.output_bytes)}",
                    f"节省空间：{_format_bytes(result.saved_bytes)}",
                    f"替换图片：{result.replaced_count}",
                    f"跳过图片：{result.skipped_count}",
                    f"失败图片：{result.failed_count}",
                )
            )
        else:
            self.status_label.setText("压缩失败。")
            self.output_result.clear()

        if result.warnings:
            lines.append("\n警告：")
            lines.extend(_format_issue(warning) for warning in result.warnings)
        if result.errors:
            lines.append("\n错误：")
            lines.extend(_format_issue(error) for error in result.errors)
        self.result_summary.setPlainText("\n".join(lines))
        self.progress_bar.setRange(0, max(1, result.planned_count))
        self.progress_bar.setValue(result.processed_count)
        self._set_summary(
            result.planned_count,
            result.replaced_count,
            result.skipped_count,
            result.failed_count,
            result.cancelled_count,
        )
        self.compression_finished.emit(result)

    @Slot(str)
    def _handle_worker_failure(self, message: str) -> None:
        self._set_running(False)
        self._worker = None
        self.status_label.setText("压缩失败。")
        self.output_result.clear()
        self.result_summary.setPlainText(f"[unexpected_error] {message}")

