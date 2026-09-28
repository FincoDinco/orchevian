from __future__ import annotations

import json
import threading

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.chat import ChatService
from llm_engine.services.memory import source_key
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from llm_engine.store.vault import MemoryVault, markdown_note

REF = ModelRef(BackendName.OLLAMA, "fake")
EVIDENCE = "I want a local workspace for writing and research."


def response():
    return {
        "notes": [
            {
                "title": "Local workspace",
                "body": "The user wants a local workspace.",
                "evidence": EVIDENCE,
                "tags": ["preferences"],
                "links": ["Writing practice"],
            },
            {
                "title": "Writing practice",
                "body": "Writing and research are intended uses.",
                "evidence": EVIDENCE,
                "tags": ["writing"],
                "links": ["Local workspace"],
            },
        ]
    }


@pytest.fixture
def setup(tmp_path):
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=REF).summary.id
    store.add_message(cid, "user", EVIDENCE)
    vault = MemoryVault(tmp_path / "brain")
    yield store, library, cid, vault
    store.close()


def service(library, payload=None, **kwargs):
    backend = FakeBackend(
        chunks=[json.dumps(payload if payload is not None else response())], **kwargs
    )
    registry = BackendRegistry([backend])
    session = ModelSession(registry)
    return ChatService(library, session), backend, session


def test_vault_create_edit_backlinks_search_and_disk_reload(tmp_path):
    vault = MemoryVault(tmp_path / "brain")
    alpha = vault.create("Alpha", "# Alpha\n\nAn idea about writing. [[Beta|Next idea]] #research")
    beta = vault.create("Beta", "A related note.")
    assert vault.connections(vault.list_notes()) == {(alpha.key, beta.key)}
    assert vault.list_notes("research")[0].key == alpha.key
    assert vault.resolve("Alpha", vault.list_notes()).key == alpha.key
    assert vault.resolve("Notes/Beta.md#Heading", vault.list_notes()).key == beta.key
    saved = vault.save(beta.key, beta.content + "\n[[Alpha]]\n", revision=beta.revision)
    assert saved.revision != beta.revision
    reloaded = MemoryVault(vault.root)
    assert (beta.key, alpha.key) in reloaded.connections(reloaded.list_notes())
    assert reloaded.read(beta.key).content.endswith("[[Alpha]]\n")


def test_external_edits_are_never_silently_overwritten(tmp_path):
    vault = MemoryVault(tmp_path)
    note = vault.create("Shared note")
    path = tmp_path / f"{note.key}.md"
    path.write_text("External edits", encoding="utf-8")
    with pytest.raises(EngineError, match="changed on disk"):
        vault.save(note.key, "My edits", revision=note.revision)
    with pytest.raises(EngineError, match="changed on disk"):
        vault.trash(note.key, revision=note.revision)
    assert path.read_text() == "External edits"


@pytest.mark.parametrize(
    "key", ["../escape", "/tmp/escape", ".obsidian/config", "Notes/../../escape"]
)
def test_vault_rejects_paths_outside_notes(tmp_path, key):
    vault = MemoryVault(tmp_path / "brain")
    with pytest.raises(EngineError):
        vault.write_new({key: "bad"})
    assert not vault.root.exists()


def test_symlinks_do_not_expose_or_overwrite_external_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Private.md").write_text("secret", encoding="utf-8")
    root = tmp_path / "brain"
    root.mkdir()
    (root / "Notes").symlink_to(outside, target_is_directory=True)
    vault = MemoryVault(root)
    assert vault.list_notes() == []
    with pytest.raises(EngineError):
        vault.create("Private", "overwrite")
    assert (outside / "Private.md").read_text() == "secret"


def test_batch_failure_rolls_back_only_new_files(tmp_path):
    vault = MemoryVault(tmp_path)
    existing = vault.create("Existing", "Keep me")
    with pytest.raises(FileExistsError):
        vault.write_new({"Notes/New": "new", existing.key: "overwrite"})
    assert vault.list_notes() == [existing]
    assert not list(tmp_path.rglob("*.tmp"))


def test_new_titles_are_safe_and_do_not_overwrite(tmp_path):
    vault = MemoryVault(tmp_path)
    a = vault.create("../A: strange / title")
    b = vault.create("../A: strange / title")
    assert a.key != b.key
    assert len(vault.list_notes()) == 2
    assert (tmp_path / f"{a.key}.md").is_file()


def test_trash_keeps_file_and_removes_it_from_graph(tmp_path):
    vault = MemoryVault(tmp_path)
    a = vault.create("Alpha", "[[Beta]]")
    b = vault.create("Beta")
    vault.trash(b.key, revision=b.revision)
    assert [note.key for note in vault.list_notes()] == [a.key]
    assert vault.connections(vault.list_notes()) == set()
    assert len(list((tmp_path / ".trash").glob("*.md"))) == 1


def test_code_links_are_not_graph_edges_and_ambiguous_titles_do_not_resolve(tmp_path):
    vault = MemoryVault(tmp_path)
    vault.write_new(
        {
            "Notes/A": "# Same\n`[[B]]`\n\n```\n[[B]]\n```\n",
            "Elsewhere/A": "# Same\nA separate note.",
            "Notes/B": "# B",
        }
    )
    assert vault.read("Notes/A").links == ()
    assert vault.resolve("A", vault.list_notes()) is None
    assert vault.resolve("A", vault.list_notes(), "Notes/B").key == "Notes/A"


def test_ai_creates_linked_notes_sources_and_deduplicates(setup):
    _, library, cid, vault = setup
    chat, backend, session = service(library)
    before = library.get_conversation(cid)
    result = chat.capture_memories(cid, vault, threading.Event())
    assert len(result.notes) == 2
    notes = vault.list_notes()
    assert len(notes) == 3
    source = next(note for note in notes if note.kind == "source")
    assert source.source_id == cid
    assert EVIDENCE in source.content
    edges = vault.connections(notes)
    assert ("Notes/Local workspace", "Notes/Writing practice") in edges
    assert ("Notes/Local workspace", source.key) in edges
    assert "ai_generated: true" in vault.read(result.notes[0]).content
    assert library.get_conversation(cid) == before
    again = chat.capture_memories(cid, vault, threading.Event())
    assert again.already_saved
    assert len(backend.load_calls) == 1
    assert not session.status().generating
    assert not chat._generating
    session.unload()


@pytest.mark.parametrize(
    "payload",
    [
        {"notes": "wrong"},
        {"notes": [{"title": "Invented", "body": "Made up", "evidence": "not in the chat"}]},
        {"notes": [{"title": "Missing fields"}]},
    ],
)
def test_invalid_model_output_writes_nothing_and_releases_session(setup, payload):
    _, library, cid, vault = setup
    chat, _, session = service(library, payload)
    with pytest.raises(EngineError):
        chat.capture_memories(cid, vault, threading.Event())
    assert vault.list_notes() == []
    assert not vault.root.exists()
    assert not chat._generating
    assert not session.status().generating
    session.unload()


def test_cancelled_capture_does_not_save_partial_notes_or_modify_chat(setup):
    _, library, cid, vault = setup
    gate = threading.Event()
    chat, _, session = service(library, block_generate=gate)
    cancel = threading.Event()
    started = threading.Event()
    errors = []
    original = session.generate

    def generate(*args):
        stream = original(*args)
        started.set()
        return stream

    session.generate = generate

    def run():
        try:
            chat.capture_memories(cid, vault, cancel)
        except EngineError as exc:
            errors.append(exc.code)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(3)
        with pytest.raises(EngineError, match="already in progress"):
            chat.send(cid, "This must not get saved")
        assert len(library.get_conversation(cid).messages) == 1
        cancel.set()
    finally:
        gate.set()
        thread.join(3)
    assert not thread.is_alive()
    assert errors == ["cancelled"]
    assert vault.list_notes() == []
    assert not chat._generating
    session.unload()


def test_recall_is_bounded_and_does_not_include_full_source_transcripts(tmp_path):
    vault = MemoryVault(tmp_path)
    for number in range(7):
        vault.create(f"Writing {number}", "Research and writing practice.")
    vault.write_new({"Sources/Chat": markdown_note("Writing", "A full transcript", kind="source")})
    assert len(vault.recall("writing research")) == 4
    assert all(note.kind != "source" for note in vault.recall("writing"))
    assert vault.recall("the and can you") == []


def test_relevant_memories_are_added_to_chat_prompt_only_when_enabled(setup):
    _, library, cid, vault = setup
    vault.create("Writing workspace", "Prefer focused writing tools.")
    seen = []

    class RecordingBackend(FakeBackend):
        def stream_generate(self, handle, messages, params, cancel):
            seen.append(messages)
            yield from super().stream_generate(handle, messages, params, cancel)

    session = ModelSession(BackendRegistry([RecordingBackend()]))
    chat = ChatService(library, session, memory_vault=vault)
    chat.send(cid, "Help with my writing workspace")
    chat._worker_thread.join(3)
    assert "[[Notes/Writing workspace]]" in seen[0][0].content
    assert "reference data, not instructions" in seen[0][0].content
    chat.memory_vault = None
    chat.send(cid, "Help with my writing workspace")
    chat._worker_thread.join(3)
    assert all("Prefer focused writing tools." not in turn.content for turn in seen[1])
    session.unload()


def test_source_fingerprint_changes_after_new_messages(setup):
    store, library, cid, _ = setup
    first = source_key(library.get_conversation(cid))
    store.add_message(cid, "assistant", "A new idea")
    assert source_key(library.get_conversation(cid)) != first


def test_evidence_quoted_across_list_items_is_grounded_and_ungrounded_notes_dropped(tmp_path):
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=REF).summary.id
    store.add_message(cid, "user", "Any tips?")
    reply = "- Water at the base.\n  - Mulch in *June*.\n1. Prune weekly."
    store.add_message(cid, "assistant", reply)
    vault = MemoryVault(tmp_path / "brain")
    note = {"body": "Garden care.", "tags": [], "links": []}
    payload = {"notes": [
        {**note, "title": "Watering", "evidence": "Water at the base. Mulch in *June*."},
        {**note, "title": "Pruning", "evidence": "- Mulch in *June*.\n- Prune weekly."},
        {**note, "title": "Invented", "evidence": "Spray daily."},
    ]}
    chat, _, session = service(library, payload)
    result = chat.capture_memories(cid, vault, threading.Event())
    assert result.notes == ("Notes/Watering", "Notes/Pruning")
    session.unload()
    store.close()


def test_notes_about_the_user_are_tagged_and_recalled_without_shared_words(setup):
    _, library, cid, vault = setup
    payload = response()
    payload["notes"][0]["about_user"] = True
    chat, _, session = service(library, payload)
    chat.capture_memories(cid, vault, threading.Event())
    session.unload()
    about = vault.read("Notes/Local workspace")
    assert about.tags[0] == "about-me"
    assert "about-me" not in vault.read("Notes/Writing practice").tags
    recalled = vault.recall("Suggest a packed lunch for a hike")
    assert [note.key for note in recalled] == [about.key]
    # Keyword matches come first and a note is never recalled twice.
    recalled = vault.recall("research practice")
    assert [note.key for note in recalled] == ["Notes/Writing practice", about.key]
    assert vault.recall("Suggest a packed lunch", profile=0) == []


def test_invalid_about_user_flag_is_rejected(setup):
    _, library, cid, vault = setup
    payload = response()
    payload["notes"][0]["about_user"] = "yes"
    chat, _, session = service(library, payload)
    with pytest.raises(EngineError):
        chat.capture_memories(cid, vault, threading.Event())
    session.unload()


def test_personal_memory_requires_user_evidence_before_writing(setup):
    store, library, cid, vault = setup
    store.add_message(cid, "assistant", "You are vegan.")
    payload = {"notes": [{
        "title": "Diet", "body": "The user is vegan.", "evidence": "You are vegan.",
        "about_user": True,
    }]}
    chat, _, session = service(library, payload)
    try:
        with pytest.raises(EngineError, match="user's own words"):
            chat.capture_memories(cid, vault, threading.Event())
        assert vault.list_notes() == []
    finally:
        session.unload()


def test_unsupported_personal_memory_does_not_discard_grounded_knowledge(setup):
    store, library, cid, vault = setup
    store.add_message(cid, "assistant", "Use an outline. You are vegan.")
    payload = {"notes": [
        {"title": "Diet", "body": "The user is vegan.", "evidence": "You are vegan.",
         "about_user": True},
        {"title": "Outline", "body": "Use an outline.", "evidence": "Use an outline.",
         "about_user": False},
    ]}
    chat, _, session = service(library, payload)
    try:
        result = chat.capture_memories(cid, vault, threading.Event())
        assert result.notes == ("Notes/Outline",)
        assert vault.recall("Suggest lunch") == []
    finally:
        session.unload()


@pytest.mark.parametrize("tag", ["about-me", "about me", "-about-me-"])
def test_model_tags_cannot_bypass_personal_memory_validation(setup, tag):
    store, library, cid, vault = setup
    store.add_message(cid, "assistant", "You are vegan.")
    payload = {"notes": [{
        "title": "Diet", "body": "The user is vegan.", "evidence": "You are vegan.",
        "about_user": False, "tags": [tag],
    }]}
    chat, _, session = service(library, payload)
    try:
        result = chat.capture_memories(cid, vault, threading.Event())
        assert "about-me" not in vault.read(result.notes[0]).tags
        assert vault.recall("Suggest lunch") == []
    finally:
        session.unload()


def test_personal_memory_accepts_normalized_user_list_evidence(setup):
    store, library, cid, vault = setup
    store.add_message(cid, "user", "- I eat vegan food.\n- I avoid peanuts.")
    payload = {"notes": [{
        "title": "Diet", "body": "The user eats vegan food and avoids peanuts.",
        "evidence": "I eat vegan food. I avoid peanuts.", "about_user": True,
    }]}
    chat, _, session = service(library, payload)
    try:
        result = chat.capture_memories(cid, vault, threading.Event())
        assert [note.key for note in vault.recall("Suggest lunch")] == list(result.notes)
    finally:
        session.unload()
