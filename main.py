"""
JobTracker — entry point.
Initialises the QApplication, applies the dynamic stylesheet, and shows
the main window.
"""

import logging
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from jobtracker.ui.app import MainWindow
from jobtracker.core.config import ICON_PATH, APP_NAME
from jobtracker.core.logging_config import setup_logging


def main() -> None:
    setup_logging()
    logger = logging.getLogger("jobtracker")
    logger.info("Starting %s", APP_NAME)

    # Log otherwise-uncaught exceptions instead of letting them vanish, then fall
    # back to the default handler so behaviour is unchanged.
    def _excepthook(exc_type, exc_value, exc_tb):
        logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # Stylesheet is applied dynamically by MainWindow.__init__

    # Set app icon if one exists
    if sys.platform != "darwin":
        icon_candidates = [
            ICON_PATH,
            ICON_PATH.with_name("JobTracker.png"),
            ICON_PATH.with_suffix(".png"),
            ICON_PATH.with_suffix(".ico"),
        ]
        for candidate in icon_candidates:
            if candidate.exists() and candidate.stat().st_size > 0:
                app.setWindowIcon(QIcon(str(candidate)))
                break

    window = MainWindow(app)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
