"""
JobTracker — entry point.
Initialises the QApplication, applies the dynamic stylesheet, and shows
the main window.
"""

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon

from jobtracker.core.config import DB_PATH, ICON_PATH, APP_NAME
from jobtracker.core.logging_config import setup_logging
from jobtracker.desktop.ownership import StoreInUse, StoreLock


def acquire_store_lock() -> StoreLock:
    """Hold this store for the process lifetime; the native engine checks the
    same lock, so two timer owners never open one database."""
    return StoreLock(DB_PATH, role="qt").acquire()


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

    # Before MainWindow constructs services or runs recovery.
    try:
        store_lock = acquire_store_lock()
    except StoreInUse:
        logger.warning("Store already open in another JobTracker process: %s", DB_PATH)
        QMessageBox.information(
            None, APP_NAME,
            "JobTracker is already open with this data.\n\n"
            "Switch to the open JobTracker window to continue.",
        )
        sys.exit(1)

    # Importing MainWindow imports the eager Database singleton and may migrate
    # SQLite. A second owner must be refused before that happens.
    from jobtracker.ui.app import MainWindow

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

    code = app.exec()
    store_lock.release()
    sys.exit(code)


if __name__ == "__main__":
    main()
