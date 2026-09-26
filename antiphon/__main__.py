"""Entry point: ``python -m antiphon``."""

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from antiphon.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Antiphon")
    app.setDesktopFileName("io.github.antiphon.Antiphon")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
