"""Entry point: ``python -m antiphon``."""

import sys


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        from antiphon.selftest import run
        return run()

    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import QSettings

    from antiphon.ui.main_window import MainWindow
    from antiphon.ui.skin.manager import ThemeManager, install

    app = QApplication(sys.argv)
    app.setApplicationName("Antiphon")
    app.setDesktopFileName("io.github.antiphon.Antiphon")
    themes = ThemeManager()
    install(themes)
    themes.apply(QSettings("antiphon", "antiphon").value("ui/theme", "realplayer-classic", type=str))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
