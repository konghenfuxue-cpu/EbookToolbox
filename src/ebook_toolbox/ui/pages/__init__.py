"""Pages displayed by the main window."""

from ebook_toolbox.ui.pages.detection_report import DetectionReportPage, ReportEntry, ReportStore
from ebook_toolbox.ui.pages.epub_image_compressor import EpubImageCompressorPage
from ebook_toolbox.ui.pages.epub_structural_repair import EpubStructuralRepairPage
from ebook_toolbox.ui.pages.epub_toc_repair import EpubTocRepairPage
from ebook_toolbox.ui.pages.placeholder import PlaceholderPage
from ebook_toolbox.ui.pages.welcome import WelcomePage

__all__ = [
    "EpubImageCompressorPage",
    "EpubStructuralRepairPage",
    "EpubTocRepairPage",
    "DetectionReportPage",
    "PlaceholderPage",
    "ReportEntry",
    "ReportStore",
    "WelcomePage",
]
