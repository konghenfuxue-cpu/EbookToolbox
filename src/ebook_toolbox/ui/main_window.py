"""Main application window."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from ebook_toolbox.ui.navigation import NAVIGATION_ITEMS, NavigationList
from ebook_toolbox.ui.pages import (
    DetectionReportPage,
    EpubImageCompressorPage,
    EpubStructuralRepairPage,
    EpubTocRepairPage,
    PlaceholderPage,
    ReportEntry,
    ReportStore,
    WelcomePage,
)


class MainWindow(QMainWindow):
    """Top-level window containing navigation and tool pages."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("电子书工具箱")
        self.resize(1100, 720)
        self.report_store = ReportStore()

        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self.navigation = NavigationList()
        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.pages.addWidget(WelcomePage())
        for title in NAVIGATION_ITEMS[1:]:
            if title == "EPUB 目录修复":
                self.epub_toc_repair_page = EpubTocRepairPage()
                self.epub_toc_repair_page.repair_finished.connect(self._record_toc_repair)
                self.pages.addWidget(self.epub_toc_repair_page)
            elif title == "EPUB 图片压缩":
                self.epub_image_compressor_page = EpubImageCompressorPage()
                self.epub_image_compressor_page.compression_finished.connect(
                    self._record_image_compression
                )
                self.pages.addWidget(self.epub_image_compressor_page)
            elif title == "EPUB 综合修复":
                self.epub_structural_repair_page = EpubStructuralRepairPage()
                self.epub_structural_repair_page.repair_finished.connect(
                    self._record_structural_repair
                )
                self.pages.addWidget(self.epub_structural_repair_page)
            elif title == "检测报告":
                self.detection_report_page = DetectionReportPage(self.report_store)
                self.pages.addWidget(self.detection_report_page)
            else:
                self.pages.addWidget(PlaceholderPage(title))

        self.navigation.page_changed.connect(self.pages.setCurrentIndex)
        central_layout.addWidget(self.navigation)
        central_layout.addWidget(self.pages, 1)
        self.setCentralWidget(central_widget)

        self.statusBar().showMessage("就绪")

    @staticmethod
    def _format_issue(issue: object) -> str:
        code = getattr(issue, "code", "unknown")
        message = getattr(issue, "message", str(issue))
        path = getattr(issue, "path", None)
        location = f" ({path})" if path is not None else ""
        return f"[{code}]{location} {message}"

    def _add_report(
        self,
        *,
        tool: str,
        source: object,
        output: object | None,
        success: bool,
        warnings: Iterable[object],
        errors: Iterable[object],
    ) -> None:
        self.report_store.add(
            ReportEntry(
                created_at=datetime.now(),
                tool=tool,
                source=str(source),
                output=str(output) if output else None,
                success=success,
                warnings=tuple(self._format_issue(issue) for issue in warnings),
                errors=tuple(self._format_issue(issue) for issue in errors),
            )
        )

    def _record_image_compression(self, result: object) -> None:
        self._add_report(
            tool="EPUB 图片压缩",
            source=getattr(result, "source", ""),
            output=getattr(result, "output", None),
            success=bool(getattr(result, "success", False)),
            warnings=getattr(result, "warnings", ()),
            errors=getattr(result, "errors", ()),
        )

    def _record_toc_repair(self, result: object) -> None:
        self._add_report(
            tool="EPUB 目录修复",
            source=getattr(result, "source", ""),
            output=getattr(result, "output", None),
            success=bool(getattr(result, "success", False)),
            warnings=getattr(result, "warnings", ()),
            errors=getattr(result, "errors", ()),
        )

    def _record_structural_repair(self, result: object) -> None:
        for file_result in getattr(result, "files", ()):
            self._add_report(
                tool="EPUB 综合修复",
                source=getattr(file_result, "source", ""),
                output=getattr(file_result, "output", None),
                success=bool(getattr(file_result, "success", False)),
                warnings=getattr(file_result, "warnings", ()),
                errors=getattr(file_result, "errors", ()),
            )
        warnings = getattr(result, "warnings", ())
        errors = getattr(result, "errors", ())
        if warnings or errors:
            self._add_report(
                tool="EPUB 综合修复（批量任务）",
                source="批量任务",
                output=None,
                success=not bool(errors),
                warnings=warnings,
                errors=errors,
            )
