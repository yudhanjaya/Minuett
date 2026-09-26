"""Entry point: ``python -m minuett``."""

import sys


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        from minuett.selftest import run
        return run()

    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import QSettings

    from minuett.ui.main_window import MainWindow
    from minuett.ui.skin.manager import ThemeManager, install

    app = QApplication(sys.argv)
    app.setApplicationName("Minuett")
    app.setDesktopFileName("io.github.yudhanjaya.Minuett")
    themes = ThemeManager()
    install(themes)
    themes.apply(QSettings("minuett", "minuett").value("ui/theme", "realplayer-classic", type=str))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
