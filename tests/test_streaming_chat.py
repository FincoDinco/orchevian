from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    Conversation,
    ConversationSummary,
    GenerationParams,
    LocalModel,
    ModelRef,
)
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

REF = ModelRef(BackendName.OLLAMA, "fake")
LOCAL = LocalModel(ref=REF, path=None, size_bytes=0)


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-manager-tests"])
    return app


def _library(tmp_path: Path) -> tuple[SqliteStore, LibraryService]:
    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _wait_until(predicate, timeout: float = 5.0, message: str = "timeout") -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class ProbeFake(FakeBackend):
    def __init__(self, *args: object, gate_after: int | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.params: list[GenerationParams] = []
        self.gen_ident: int | None = None
        self.load_ident: int | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.gate_after = gate_after

    def load(self, model: LocalModel, options=None):  # type: ignore[no-untyped-def]
        self.load_ident = threading.get_ident()
        return super().load(model, options)

    def stream_generate(self, handle, messages, params, cancel):  # type: ignore[no-untyped-def]
        del handle, messages
        self.params.append(params)
        self.gen_ident = threading.get_ident()
        yielded = 0
        for chunk in self.chunks:
            if cancel.is_set():
                return
            yield chunk
            yielded += 1
            if self._fail_after is not None and yielded >= self._fail_after:
                raise self._error
            if self.gate_after is not None and yielded >= self.gate_after:
                self.entered.set()
                while not self.release.is_set():
                    if cancel.is_set():
                        return
                    self.release.wait(timeout=0.05)


def _window(tmp_path: Path, fake: FakeBackend | None = None):
    from PySide6.QtCore import QSettings

    from llm_manager_app.main_window import MainWindow

    store, library = _library(tmp_path)
    probe = fake if fake is not None else ProbeFake(models=[LOCAL], chunks=("Hello", " world"))
    registry = BackendRegistry([probe])
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    window = MainWindow(registry=registry, library=library, settings=settings)
    return window, store, library, probe


def test_composer_return_sends_shift_return_newline() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

    from llm_manager_app.widgets.composer import Composer

    host = QWidget()
    composer = Composer(host)
    layout = QVBoxLayout(host)
    layout.addWidget(composer)
    host.resize(400, 200)
    host.show()
    app.processEvents()

    edit = composer.findChild(QPlainTextEdit, "composerEdit")
    assert edit is not None
    assert edit.height() == 40
    edit.setFocus()
    app.processEvents()

    sent: list[str] = []
    composer.send_requested.connect(sent.append)
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == []

    QTest.keyClick(edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in edit.toPlainText()

    composer.set_text("hello")
    edit.setFocus()
    app.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == ["hello"]

    composer.set_text("\n".join(["line"] * 40))
    app.processEvents()
    assert edit.height() == 140

    composer.set_text("word " * 80)
    app.processEvents()
    assert 40 < edit.height() <= 140
    host.close()


def test_streaming_plain_then_markdown_on_done(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QWidget

    fake = ProbeFake(models=[LOCAL], chunks=("Hello ", "**world**"))
    window, store, library, probe = _window(tmp_path, fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()

        from PySide6.QtCore import QObject

        class Probe(QObject):
            def __init__(self) -> None:
                super().__init__()
                self.states: list[tuple[str, bool, bool, str]] = []

            def on_token(self, *_args: object) -> None:
                transcript = window._chat_view.transcript()
                plain = window.findChild(QPlainTextEdit, "transcriptStream")
                caret = window.findChild(QWidget, "streamCaret")
                self.states.append(
                    (
                        "token",
                        transcript.is_streaming(),
                        transcript.is_plain(),
                        (plain.toPlainText() if plain is not None else ""),
                    )
                )
                assert caret is not None and caret.isVisible()

            def on_done(self, *_args: object) -> None:
                transcript = window._chat_view.transcript()
                browser = window.findChild(QTextBrowser, "transcriptHistory")
                html = browser.toHtml() if browser is not None else ""
                self.states.append(
                    ("done", transcript.is_streaming(), transcript.is_plain(), html)
                )

        probe_ui = Probe()
        assert window._worker is not None
        window._worker.token.connect(probe_ui.on_token)
        window._worker.done.connect(probe_ui.on_done)

        composer = window._chat_view.composer()
        composer.set_text("hi there")
        composer.submit()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="timed out waiting for done",
        )
        states = probe_ui.states

        assert states
        assert states[0][0] == "token"
        assert states[0][1] is True
        assert states[0][2] is True
        assert "Hello" in states[0][3]
        assert states[-1][0] == "done"
        assert states[-1][1] is False
        assert states[-1][2] is False
        html = states[-1][3].lower()
        assert "<table" not in html
        assert "hello" in html
        assert probe.params
        assert probe.params[0] == GenerationParams.preset("balanced")
        loaded = library.get_conversation(cid)
        assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
        assert loaded.messages[0].content == "hi there"
        assert "Hello" in loaded.messages[1].content
        assert window.windowTitle() == "hi there"
    finally:
        window.close()
        store.close()


def test_generate_not_on_gui_thread(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    gui_ident = threading.get_ident()
    window, store, library, probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        assert window._worker is not None
        window._chat_view.composer().set_text("ping")
        window._chat_view.composer().submit()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="timed out waiting for done",
        )
        assert probe.load_ident is not None and probe.load_ident != gui_ident
        assert probe.gen_ident is not None and probe.gen_ident != gui_ident
    finally:
        window.close()
        store.close()


def test_error_keeps_buffer_banner_no_done(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser

    fake = ProbeFake(
        models=[LOCAL],
        chunks=("Hello", " world"),
        fail_after=1,
        error=EngineError("backend_unavailable", "boom"),
    )
    window, store, library, _probe = _window(tmp_path, fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        from PySide6.QtCore import QObject

        class DoneCatch(QObject):
            def __init__(self) -> None:
                super().__init__()
                self.dones: list[object] = []

            def on_done(self, *args: object) -> None:
                self.dones.append(args)

        catch = DoneCatch()
        assert window._worker is not None
        window._worker.done.connect(catch.on_done)
        window._chat_view.composer().set_text("hi")
        window._chat_view.composer().submit()
        _wait_until(
            lambda: bool(window._chat_view.banner_text()),
            message="timed out waiting for error banner",
        )
        app.processEvents()
        assert catch.dones == []
        transcript = window._chat_view.transcript()
        assert transcript.is_plain()
        plain = window.findChild(QPlainTextEdit, "transcriptStream")
        assert plain is not None
        assert "Hello" in plain.toPlainText()
        assert "boom" in window._chat_view.banner_text()
        browser = window.findChild(QTextBrowser, "transcriptHistory")
        assert browser is not None
        assert not browser.isVisible()
    finally:
        window.close()
        store.close()


def test_stop_escape_cancels(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QPlainTextEdit

    fake = ProbeFake(models=[LOCAL], chunks=tuple(["."] * 32), gate_after=32)
    window, store, library, probe = _window(tmp_path, fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        assert window._worker is not None
        window._chat_view.composer().set_text("stop me")
        window._chat_view.composer().submit()
        assert probe.entered.wait(2.0)
        deadline = time.monotonic() + 2.0
        plain = None
        while time.monotonic() < deadline:
            app.processEvents()
            plain = window.findChild(QPlainTextEdit, "transcriptStream")
            if (
                window._chat_view.transcript().is_streaming()
                and plain is not None
                and "." in plain.toPlainText()
            ):
                break
            time.sleep(0.01)
        assert window._chat_view.transcript().is_streaming()
        assert plain is not None and "." in plain.toPlainText()
        shortcuts = window.findChildren(QShortcut)
        assert any(s.key().matches(QKeySequence(Qt.Key.Key_Escape)) for s in shortcuts)
        window._chat_view.stop()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="timed out waiting for stop",
        )
    finally:
        probe.release.set()
        window.close()
        store.close()


def test_regenerate_replaces_assistant(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    window, store, library, _probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        assert window._worker is not None
        window._chat_view.composer().set_text("again")
        window._chat_view.composer().submit()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="timed out waiting for first done",
        )
        first = library.get_conversation(cid)
        assert [turn.role for turn in first.messages] == ["user", "assistant"]
        from PySide6.QtWidgets import QPushButton

        regen = window.findChild(QPushButton, "regenerateButton")
        assert regen is not None and regen.isEnabled()
        regen.click()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="timed out waiting for regenerate",
        )
        loaded = library.get_conversation(cid)
        assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
        assert loaded.messages[0].content == "again"
        assert "Hello" in loaded.messages[1].content
    finally:
        window.close()
        store.close()


def test_send_disabled_without_a_model(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

    window, store, library, _probe = _window(tmp_path)
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        send = window.findChild(QPushButton, "sendButton")
        picker = window.findChild(QToolButton, "modelPicker")
        assert send is not None and not send.isEnabled()
        assert picker is not None and picker.text() == "Select a model"
        composer = window._chat_view.composer()
        composer.set_text("hello")
        composer.submit()
        assert not window._chat_view.is_streaming()
        assert composer.text() == "hello"
        _wait_until(
            lambda: "Select a model" in (window.findChild(QLabel, "modelEmpty").text() or ""),
            message="timed out waiting for no-model empty state",
        )
    finally:
        window.close()
        store.close()


def _conv(cid: int, title: str = "New Chat", model: ModelRef | None = None) -> Conversation:
    now = datetime.now()
    return Conversation(
        summary=ConversationSummary(
            id=cid,
            title=title,
            model=model,
            project_id=None,
            message_count=0,
            updated_at=now,
            created_at=now,
        ),
        system_prompt="",
        messages=(),
    )


def test_rejected_does_not_restore_composer_on_other_conversation() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.widgets.chat_view import ChatView

    view = ChatView()
    view.show()
    app.processEvents()
    view.set_conversation(_conv(1, "Alpha", REF))
    view.composer().set_text("secret-draft")
    view.composer().submit()
    view.set_conversation(_conv(2, "Beta", REF))
    view.on_rejected(1, "no_model", "conversation has no model")
    assert "secret-draft" not in view.composer().text()
    assert view.banner_text() == ""
    view.set_conversation(_conv(1, "Alpha", REF))
    app.processEvents()
    assert view.composer().text() == "secret-draft"
    assert "model" in view.banner_text()
    view.close()


def test_transcript_scrolls_to_end_after_html() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

    from llm_manager_app.widgets.transcript import Transcript

    host = QWidget()
    transcript = Transcript(host)
    layout = QVBoxLayout(host)
    layout.addWidget(transcript)
    host.resize(400, 220)
    host.show()
    app.processEvents()
    turns = [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn {i} " + "para " * 12)
        for i in range(20)
    ]
    transcript.set_turns(turns)
    app.processEvents()
    browser = transcript.findChild(QTextBrowser, "transcriptHistory")
    assert browser is not None
    bar = browser.verticalScrollBar()
    assert bar.maximum() == 0 or bar.value() == bar.maximum()
    host.close()


def test_caret_hides_when_scrolled_out_of_view() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from llm_manager_app.widgets.transcript import Transcript

    host = QWidget()
    transcript = Transcript(host)
    layout = QVBoxLayout(host)
    layout.addWidget(transcript)
    host.resize(400, 180)
    host.show()
    app.processEvents()
    transcript.set_turns(
        [ChatTurn(role="user", content="block\n" * 40) for _ in range(2)]
    )
    transcript.begin_stream()
    transcript.append_stream("tail")
    app.processEvents()
    caret = transcript.findChild(QWidget, "streamCaret")
    assert caret is not None
    transcript._plain.verticalScrollBar().setValue(0)
    app.processEvents()
    assert not caret.isVisible()
    host.close()
