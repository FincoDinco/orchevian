from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from llm_engine import cli
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore, backup_path_for
from test_openai_api import BODY, free_port, wait_for


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_ENGINE_FAKE_BACKEND", "1")
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)
    return ["--config", str(tmp_path / "config.json"), "--db", str(tmp_path / "data.db")]


def test_migrate_legacy_idempotent_and_options_before_subcommand(paths, tmp_path, capsys):
    db = tmp_path / "data.db"
    shutil.copy2(Path(__file__).parent / "fixtures/legacy_data.db", db)
    original = db.read_bytes()
    assert cli.main(paths + ["migrate"]) == 0
    assert backup_path_for(db).read_bytes() == original
    with SqliteStore(db) as store:
        conversations = LibraryService(store).list_conversations()
        assert conversations
    assert cli.main(["migrate"] + paths) == 0
    assert backup_path_for(db).read_bytes() == original
    with SqliteStore(db) as store:
        assert LibraryService(store).list_conversations() == conversations
    assert "schema version: 7" in capsys.readouterr().out


def test_chat_streams_persists_and_resumes(paths, tmp_path, monkeypatch, capsys):
    prompts = iter(["Hello from terminal", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(prompts))
    assert cli.main(paths + ["chat", "--model", "ollama/fake"]) == 0
    assert "Hello world" in capsys.readouterr().out
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        cid = library.list_conversations()[0].id
        assert [t.content for t in library.get_conversation(cid).messages] == [
            "Hello from terminal", "Hello world",
        ]
    prompts = iter(["Continue", "/quit"])
    assert cli.main(["chat", "--conversation", str(cid)] + paths) == 0
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        assert len(library.list_conversations()) == 1
        assert len(library.get_conversation(cid).messages) == 4


def test_missing_model_does_not_create_conversation(paths, tmp_path, capsys):
    assert cli.main(["chat", "--model", "ollama/missing"] + paths) == 1
    with SqliteStore(tmp_path / "data.db") as store:
        assert LibraryService(store).list_conversations() == []
    assert "not found" in capsys.readouterr().err


def test_health_does_not_claim_to_inspect_gui(paths, capsys):
    assert cli.main(["health"] + paths) == 0
    output = capsys.readouterr().out
    assert "live session: not inspected" in output
    assert "loaded: none" not in output


def test_serve_real_process_stops_on_termination_without_opening_db(paths, tmp_path):
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "llm_engine", "serve", "--port", str(port), *paths],
        env=os.environ | {"ORCHEVIAN_API_KEY": "ov-test-key"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    key = {"Authorization": "Bearer ov-test-key"}
    try:
        def ready():
            if process.poll() is not None:
                pytest.fail(process.communicate()[1])
            try:
                return httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=0.3,
                                 headers=key).is_success
            except httpx.TransportError:
                return False
        wait_for(ready)
        result = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=BODY,
                            headers=key)
        assert result.json()["choices"][0]["message"]["content"] == "Hello world"
        assert not (tmp_path / "data.db").exists()
        process.terminate()
        process.communicate(timeout=8)
        if os.name != "nt":
            assert process.returncode == 0
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
