import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from llm_manager.gui import theme
from llm_manager.gui.main_window import MainWindow


def run_app() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("LLM Manager")
    app.setOrganizationName("llm-manager")
    app.setFont(QFont(".AppleSystemUIFont", 13))
    theme.install(app)
    window = MainWindow()
    window.show()
    return app.exec()
