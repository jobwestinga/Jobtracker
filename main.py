"""
JobTracker — entry point.
Initialises the QApplication, applies the dynamic stylesheet, and shows
the main window.
"""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from jobtracker.ui.app import MainWindow
from jobtracker.core.config import ICON_PATH, APP_NAME


def main() -> None:
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
