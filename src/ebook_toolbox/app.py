"""Application bootstrap code."""

import sys
from collections.abc import Sequence

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qt_material import apply_stylesheet

from ebook_toolbox import __version__
from ebook_toolbox.resources import resource_path
from ebook_toolbox.ui.main_window import MainWindow


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or return the process-wide Qt application instance."""
    existing_application = QApplication.instance()
    if existing_application is not None:
        return existing_application

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("EbookToolbox")
    application.setApplicationDisplayName("EbookToolbox")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("EbookToolbox")
    icon_path = resource_path("app_icon.ico")
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    apply_stylesheet(application, theme="light_blue.xml")
    return application


def main() -> int:
    """Start the desktop application and return its exit code."""
    application = create_application()
    window = MainWindow()
    window.show()
    return application.exec()


def packaged_self_test() -> int:
    """Initialize the packaged Qt application and main window, then exit cleanly."""

    application = create_application(["EbookToolbox", "--self-test"])
    window = MainWindow()
    window.show()
    application.processEvents()
    window.close()
    application.processEvents()
    return 0
