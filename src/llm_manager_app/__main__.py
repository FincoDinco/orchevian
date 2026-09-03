from __future__ import annotations

import sys
from collections.abc import Sequence

from llm_engine.logging import setup_logging


def main(argv: Sequence[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is required for the GUI. Install with: uv sync --extra gui",
            file=sys.stderr,
        )
        return 1

    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.tokens import apply_studio
    from llm_manager_app.widgets.settings import (
        APP_NAME,
        ORG_NAME,
        ensure_appearance,
        make_settings,
    )

    setup_logging()
    args = list(argv) if argv is not None else sys.argv
    app = QApplication(args)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    settings = make_settings()
    apply_studio(app, theme=ensure_appearance(settings))
    window = MainWindow(settings=settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
