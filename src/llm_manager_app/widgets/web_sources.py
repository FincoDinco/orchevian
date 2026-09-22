"""Reviewable retrieval evidence, escaped HTML and explicitly opened public links."""

from html import escape

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QPushButton, QTextBrowser, QVBoxLayout, QWidget

from llm_engine.domain.errors import EngineError
from llm_engine.services.web_retrieval import public_url


class WebSources(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = None
        self.cid = None
        self.disclosure = QPushButton("Web sources", self)
        self.disclosure.setCheckable(True)
        self.content = QTextBrowser(self)
        self.content.setOpenLinks(False)
        self.content.setOpenExternalLinks(False)
        self.content.setMaximumHeight(200)
        self.content.anchorClicked.connect(self._open)
        self.disclosure.toggled.connect(self.content.setVisible)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addWidget(self.disclosure)
        layout.addWidget(self.content)
        self.content.hide()
        self.hide()

    def set_conversation(self, cid):
        if self.cid != cid:
            self.disclosure.setChecked(False)
            self.content.clear()
        self.cid = cid
        self.refresh()

    def refresh(self):
        reports = self.service.history(self.cid) if self.service else []
        reports = [report for report in reports if report["enabled"]]
        self.setVisible(bool(reports))
        self.disclosure.setText(f"Web sources ({len(reports)} searches)")
        sections = []
        for report in reports:
            sections.append(f"<p><b>{escape(report['query'])}</b><br>"
                            f"{escape(report['provider'])} · {escape(report['retrieved_at'])} · "
                            f"{escape(report['status'])}</p>")
            if report.get("search_query"):
                sections.append(f"<p>Searched for: {escape(report['search_query'])}</p>")
            if report["warning"]:
                sections.append(f"<p>{escape(report['warning'])}</p>")
            for source in report["sources"]:
                sections.append(
                    f'<p><a href="{escape(source["url"], quote=True)}">'
                    f'{escape(source["title"])}</a><br>{escape(source["url"])}<br>'
                    f'{escape(source["excerpt"])}</p>'
                )
        self.content.setHtml("".join(sections))

    def _open(self, url):
        try:
            safe = public_url(url.toString())
        except EngineError:
            return
        QDesktopServices.openUrl(QUrl(safe))
