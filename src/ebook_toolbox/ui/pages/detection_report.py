"""Session-level report center for completed ebook tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ebook_toolbox.services.report_export import export_session_report

_CHECK_NAMES = {
    "internal_structure": "内部结构检查",
    "calibre_metadata": "Calibre 元数据读取",
    "epubcheck": "EPUBCheck 标准校验",
}
_CHECK_STATUSES = {
    "passed": "通过",
    "failed": "失败",
    "not_run": "未运行",
    "not_installed": "未安装",
}


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """One completed task rendered by the report center."""

    created_at: datetime
    tool: str
    source: str
    output: str | None
    success: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    checks: tuple[tuple[str, str], ...] = ()


class ReportStore(QObject):
    """In-memory report entries shared by pages in one application session."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[ReportEntry] = []

    @property
    def entries(self) -> tuple[ReportEntry, ...]:
        return tuple(self._entries)

    def add(self, entry: ReportEntry) -> None:
        self._entries.append(entry)
        self.changed.emit()

    def clear(self) -> None:
        if not self._entries:
            return
        self._entries.clear()
        self.changed.emit()


class DetectionReportPage(QWidget):
    """Display structured results produced by the current application session."""

    def __init__(self, report_store: ReportStore) -> None:
        super().__init__()
        self.setObjectName("detectionReportPage")
        self._report_store = report_store
        self._visible_entries: tuple[ReportEntry, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        title = QLabel("检测报告")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel(
            "汇总本次启动以来已完成的任务。报告可导出为 JSON；"
            "未导出的报告仅保存在本次会话中。导出不会读取或修改原文件。"
        )
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        actions = QHBoxLayout()
        self.summary_label = QLabel("本次会话暂无报告。")
        self.summary_label.setObjectName("detectionReportSummary")
        self.clear_button = QPushButton("清空本次报告")
        self.clear_button.setObjectName("clearDetectionReportsButton")
        self.clear_button.clicked.connect(self._report_store.clear)
        self.export_button = QPushButton("导出本次报告…")
        self.export_button.setObjectName("exportDetectionReportsButton")
        self.export_button.clicked.connect(self._export_report)
        actions.addWidget(self.summary_label, 1)
        actions.addWidget(self.export_button)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)
        self.export_status_label = QLabel()
        self.export_status_label.setObjectName("detectionReportExportStatus")
        self.export_status_label.setWordWrap(True)
        self.export_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.export_status_label)

        self.report_table = QTableWidget(0, 6)
        self.report_table.setObjectName("detectionReportTable")
        self.report_table.setHorizontalHeaderLabels(
            ("时间", "工具", "输入文件", "状态", "警告", "错误")
        )
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.report_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.report_table.setAlternatingRowColors(True)
        header = self.report_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.report_table.itemSelectionChanged.connect(self._show_selected_details)
        layout.addWidget(self.report_table, 1)

        self.details = QPlainTextEdit()
        self.details.setObjectName("detectionReportDetails")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(128)
        self.details.setPlaceholderText("选择一条报告以查看检查范围、警告和错误详情。")
        layout.addWidget(self.details)

        self._report_store.changed.connect(self._render_entries)
        self._render_entries()

    @Slot()
    def _render_entries(self) -> None:
        self._visible_entries = self._report_store.entries
        self.report_table.setRowCount(0)
        self.details.clear()
        self.export_status_label.clear()

        successful = sum(entry.success for entry in self._visible_entries)
        failed = len(self._visible_entries) - successful
        warnings = sum(len(entry.warnings) for entry in self._visible_entries)
        errors = sum(len(entry.errors) for entry in self._visible_entries)
        if not self._visible_entries:
            self.summary_label.setText("本次会话暂无报告。")
        else:
            self.summary_label.setText(
                f"任务：{len(self._visible_entries)}　成功：{successful}　失败：{failed}　"
                f"警告：{warnings}　错误：{errors}"
            )

        for entry in self._visible_entries:
            row = self.report_table.rowCount()
            self.report_table.insertRow(row)
            values = (
                entry.created_at.strftime("%H:%M:%S"),
                entry.tool,
                entry.source,
                "成功" if entry.success else "失败",
                str(len(entry.warnings)),
                str(len(entry.errors)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 3, 4, 5}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.report_table.setItem(row, column, item)
        self.clear_button.setEnabled(bool(self._visible_entries))
        self.export_button.setEnabled(bool(self._visible_entries))

    @Slot()
    def _export_report(self) -> None:
        entries = self._report_store.entries
        if not entries:
            return
        self.export_status_label.clear()
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出本次会话报告（请选择新文件名）",
            f"EbookToolbox-检测报告-{datetime.now():%Y%m%d-%H%M%S}.json",
            "JSON 报告 (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not destination:
            return
        try:
            exported = export_session_report(entries, destination)
        except FileExistsError:
            self.export_status_label.setText("导出失败：目标路径已存在，请另选新文件名。")
        except (OSError, TypeError, ValueError) as exc:
            self.export_status_label.setText(
                f"导出失败：{exc}。若已创建目标文件，可能是不完整报告；请另选新文件名重试。"
            )
        else:
            self.export_status_label.setText(f"已导出本次会话的 {len(entries)} 条报告：{exported}")

    @Slot()
    def _show_selected_details(self) -> None:
        selected_rows = self.report_table.selectionModel().selectedRows()
        if not selected_rows:
            self.details.clear()
            return
        row = selected_rows[0].row()
        if row >= len(self._visible_entries):
            self.details.clear()
            return
        entry = self._visible_entries[row]
        lines = [
            f"工具：{entry.tool}",
            f"状态：{'成功' if entry.success else '失败'}",
            f"输入：{entry.source}",
        ]
        if entry.output:
            lines.append(f"输出：{entry.output}")
        if entry.checks:
            lines.extend(("", "检查范围（内部检查不等同 EPUB 标准校验）："))
            lines.extend(
                f"{_CHECK_NAMES.get(name, name)}：{_CHECK_STATUSES.get(status, status)}"
                for name, status in entry.checks
            )
        else:
            lines.extend(("", "检查范围：本任务未记录检查项。"))
        if entry.warnings:
            lines.extend(("", "警告：", *entry.warnings))
        if entry.errors:
            lines.extend(("", "错误：", *entry.errors))
        self.details.setPlainText("\n".join(lines))


__all__ = ["DetectionReportPage", "ReportEntry", "ReportStore"]
