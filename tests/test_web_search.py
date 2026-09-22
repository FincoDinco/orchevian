from __future__ import annotations

import socket
import threading
import time
from email.message import Message

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ChatTurn, GenerationParams
from llm_engine.services import web_retrieval as web
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession
from llm_engine.services.web_search import WebSearchService
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore


def result():
    return {"sources": [{"title": "Public reference", "url": "https://example.com/reference",
                         "excerpt": "The verified launch date is Friday."}], "warning": ""}


class RecordingBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def stream_generate(self, handle, messages, params, cancel):
        self.prompts.append(messages)
        yield "Friday ([source](https://example.com/reference))."


@pytest.fixture
def stack(tmp_path):
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        backend = RecordingBackend()
        session = ModelSession(BackendRegistry([backend]))
        chat = ChatService(library, session)
        calls = []

        def retrieve(query, cancel, progress):
            calls.append(query)
            progress("Searching the web…")
            return result()

        chat.web.retriever = retrieve
        cid = library.create_conversation(model=backend.list_models()[0].ref).summary.id
        yield store, library, chat, backend, cid, calls
        if chat._worker_thread:
            chat.cancel_current()
            chat._worker_thread.join(5)
        session.force_unload()


def join(chat):
    chat._worker_thread.join(5)
    assert not chat._worker_thread.is_alive()


def test_off_on_off_regenerate_and_api_are_isolated(stack):
    store, library, chat, backend, cid, calls = stack
    chat.send(cid, "First question")
    join(chat)
    assert calls == []
    chat.send(cid, "When is launch?", web_search=True)
    join(chat)
    assert calls == ["When is launch?"]
    assert "verified launch date" in backend.prompts[-1][0].content
    assert "untrusted reference data" in backend.prompts[-1][0].content
    assert chat.web.history(cid)[0]["sources"] == result()["sources"]
    chat.send(cid, "Next question", web_search=False)
    join(chat)
    assert calls == ["When is launch?"]
    assert "verified launch date" not in " ".join(t.content for t in backend.prompts[-1])
    chat.regenerate(cid, web_search=True)
    join(chat)
    assert calls == ["When is launch?", "Next question"]
    chat.regenerate(cid, web_search=False)
    join(chat)
    assert chat.web.history(cid)[0]["status"] == "off"
    assert len(chat.web.history(cid)) == 3
    list(chat.stream_external(backend.list_models()[0].ref, [ChatTurn("user", "API question")],
                              GenerationParams(), threading.Event()))
    assert backend.prompts[-1] == [ChatTurn("user", "API question")]
    assert len(calls) == 2


def test_retained_evidence_survives_reopen_and_cascades_on_delete(stack):
    store, library, chat, backend, cid, calls = stack
    chat.send(cid, "When?", web_search=True)
    join(chat)
    with SqliteStore(store.path) as reopened:
        assert WebSearchService(reopened).history(cid) == chat.web.history(cid)
    library.delete_conversation(cid)
    assert chat.web.history(cid) == []


def test_retrieval_timestamp_stays_in_evidence_not_model_event_context(stack):
    store, library, chat, backend, cid, calls = stack
    chat.send(cid, "How many stops are there?", web_search=True)
    join(chat)
    evidence = chat.web.history(cid)[0]
    assert evidence["retrieved_at"]
    prompt = backend.prompts[-1][0].content
    assert evidence["retrieved_at"] not in prompt
    assert "answer directly without adding an unsupported date" in prompt


@pytest.mark.parametrize("question, expected", [
    ("On what date did NASA's Artemis I mission launch?", "Artemis I launch date"),
    ("On what date was Python 3.13.0 released?", "Python 3.13.0 release date"),
    ("Which exact date was Python 3.13.0 released?", "Python 3.13.0 release date"),
    ("When was Python 3.13.0 released for Windows?", "Python 3.13.0 released Windows"),
    ("Can I use Python 3.13.0?", "Python 3.13.0"),
    ("Find a date calculator", "date calculator"),
])
def test_search_keeps_named_subjects_versions_and_roman_numerals(question, expected):
    assert web.search_query(question) == expected


@pytest.mark.parametrize("question, generic, specific", [
    ("When was Python 3.13.0 released?", "Python tutorials and Python 3.14 releases.",
     "Python 3.13.0 release date: October 7, 2024."),
    ("On what date did NASA's Artemis I mission launch?", "NASA Artemis II launch mission.",
     "NASA Artemis I launch date: November 16, 2022."),
])
def test_search_requires_the_requested_version_or_named_mission_in_excerpts(
    question, generic, specific,
):
    class Provider:
        def search(self, query):
            return [web.SearchHit(query, "https://example.com/generic"),
                    web.SearchHit(query, "https://example.com/specific")]

    result = web.retrieve(question, Provider(), lambda url: (
        url, specific if url.endswith("/specific") else generic, "text/plain"
    ), threading.Event(), lambda _: None)
    assert [source["url"] for source in result["sources"]] == ["https://example.com/specific"]


def test_private_search_is_memory_only_and_cannot_reappear_after_clear(stack):
    store, library, chat, backend, cid, calls = stack
    private = chat.create_private(backend.list_models()[0].ref).summary.id
    chat.send(private, "Private query", web_search=True)
    join(chat)
    assert chat.web.history(private)[0]["sources"]
    with store.locked() as conn:
        assert conn.execute("SELECT count(*) FROM web_searches").fetchone()[0] == 0
    chat.discard_private(private)
    chat.web._save(private, None, {"query": "late result"})
    assert chat.web.history(private) == []
    fresh = chat.create_private(backend.list_models()[0].ref).summary.id
    chat.send(fresh, "No search")
    join(chat)
    assert calls == ["Private query"]


def test_search_sends_only_current_question_and_clips_context(stack, tmp_path):
    store, library, chat, backend, cid, calls = stack
    chat.set_system_prompt(cid, "Secret guidance")
    store.add_message(cid, "user", "Secret earlier message")
    path = tmp_path / "secret.txt"
    path.write_text("Secret document content")
    chat.documents.import_file(cid, path, threading.Event())
    chat.send(cid, "Public question " * 100, web_search=True)
    join(chat)
    assert calls == [("Public question " * 100)[:web.MAX_QUERY]]
    assert "Secret" not in calls[0]
    chat.web.retriever = lambda *args: {"sources": [
        {"title": "a" * 200, "url": "https://example.com/" + "x" * 900, "excerpt": "b" * 2400}
        for _ in range(3)
    ], "warning": ""}
    prompt = chat.web.context(cid, "query", True, threading.Event(), lambda _: None)
    assert len(prompt.split("\n\n", 1)[1]) <= web.MAX_CONTEXT


def test_provider_failure_is_visible_and_does_not_generate(stack):
    store, library, chat, backend, cid, calls = stack
    errors = []
    chat.on_error = lambda cid, error: errors.append(error)

    def fail(*args):
        raise EngineError("web_failed", "Provider unavailable")

    chat.web.retriever = fail
    chat.send(cid, "Question", web_search=True)
    join(chat)
    assert errors[0].code == "web_failed"
    assert not backend.prompts
    assert chat.web.history(cid)[0]["status"] == "failed"
    assert chat.web.history(cid)[0]["warning"] == "Provider unavailable"


def test_stop_during_retrieval_never_generates(stack):
    store, library, chat, backend, cid, calls = stack
    entered = threading.Event()
    done = []

    def pending(query, cancel, progress):
        entered.set()
        assert cancel.wait(3)
        return result()

    chat.web.retriever = pending
    chat.on_done = lambda cid, **kwargs: done.append(kwargs)
    chat.send(cid, "Question", web_search=True)
    assert entered.wait(3)
    chat.stop(cid)
    join(chat)
    assert not backend.prompts
    assert done[0]["cancelled"]
    assert chat.web.history(cid)[0]["status"] == "cancelled"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/a", "http://localhost/a", "http://x.local/a",
    "http://127.0.0.1/", "http://169.254.169.254/latest", "http://10.0.0.1/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[64:ff9b::7f00:1]/",
    "http://[2002:7f00:1::]/", "http://224.0.0.1/", "https://user:pass@example.com/",
    "http://example.com:11434/", "http://example.com/\r\nHost: localhost", "http://example.com\\x",
])
def test_unsafe_urls_are_rejected_before_connecting(url):
    with pytest.raises(EngineError, match="public"):
        web.public_url(url)


def test_mixed_public_and_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr(socket, "socket", lambda *a: pytest.fail("Connected to mixed DNS"))
    with pytest.raises(EngineError, match="non-public"):
        web._PublicConnection("example.com", 443, tls=True).connect()


def test_connection_pins_validated_ip_without_second_dns_lookup(monkeypatch):
    resolved, connected = [], []

    def resolve(*args, **kwargs):
        resolved.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    class Sock:
        def settimeout(self, value): pass
        def connect(self, value): connected.append(value)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *args: Sock())
    web._PublicConnection("example.com", 80, tls=False).connect()
    assert len(resolved) == 1
    assert connected == [("93.184.216.34", 80)]


def test_connection_shares_one_timeout_across_addresses(monkeypatch):
    clock = [0.0]
    attempts = []
    monkeypatch.setattr(web.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"93.184.216.{i}", 443)) for i in range(4)
    ])

    class Sock:
        def settimeout(self, value): self.timeout = value
        def close(self): pass

        def connect(self, address):
            attempts.append(self.timeout)
            clock[0] += self.timeout
            raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "socket", lambda *args: Sock())
    with pytest.raises(TimeoutError):
        web._PublicConnection("example.com", 443, tls=True).connect()
    assert attempts == [web.NETWORK_TIMEOUT]


def response(monkeypatch, *, status=200, headers=None, data=b"hello"):
    requested = []

    class Response:
        def __init__(self):
            self.status = status
            self.headers = Message()
            for key, value in (headers or {"Content-Type": "text/html"}).items():
                self.headers[key] = value

        def getheader(self, key, default=None): return self.headers.get(key, default)
        def read(self, limit): return data[:limit]

    class Connection:
        def __init__(self, host, port, **kwargs): requested.append(host)
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass

    monkeypatch.setattr(web, "_PublicConnection", Connection)
    return requested


def test_redirect_cannot_fetch_local_service(monkeypatch):
    requested = response(monkeypatch, status=302, headers={"Location": "http://127.0.0.1/"})
    with pytest.raises(EngineError, match="public"):
        web.fetch_public("https://example.com")
    assert requested == ["example.com"]


@pytest.mark.parametrize("kwargs,match", [
    ({"data": b"x" * (web.MAX_BYTES + 1)}, "1 MB"),
    ({"headers": {"Content-Type": "application/pdf"}}, "HTML or text"),
    ({"status": 429}, "rate-limited"),
    ({"status": 202}, "blocked"),
    ({"headers": {"Content-Encoding": "gzip"}}, "encoding"),
    ({"status": 301, "headers": {"Location": "https://example.com/loop"}}, "too many"),
])
def test_reader_limits_and_errors(monkeypatch, kwargs, match):
    response(monkeypatch, **kwargs)
    with pytest.raises(EngineError, match=match):
        web.fetch_public("https://example.com")


def test_search_parses_realistic_links_and_rejects_challenges(monkeypatch):
    markup = '''<a href="https://ad.example">Advertisement</a><li class="b_algo"><h2>
    <a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbQ">
    A <b>reference</b></a></h2></li><li class="b_algo"><h2>
    <a href="http://localhost">bad</a></h2></li>'''
    monkeypatch.setattr(web, "fetch_public", lambda url: (url, markup, "text/html"))
    assert web.BingProvider().search("query") == [web.SearchHit("A reference", "https://example.com")]
    markup = '<form id="b_captcha">Please solve the challenge</form>'
    with pytest.raises(EngineError, match="blocked"):
        web.BingProvider().search("query")
    markup = '<li class="b_no">There are no results</li>'
    assert web.BingProvider().search("query") == []


def test_retrieval_has_bounded_pages_and_visible_partial_failures():
    class Provider:
        def search(self, query):
            return [web.SearchHit(f"Launch update {i}", f"https://example.com/{i}")
                    for i in range(10)]

    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/0"):
            raise OSError("offline")
        return url, "<script>hidden script</script><p>Launch is Friday.</p>", "text/html"

    result = web.retrieve("launch", Provider(), fetch, threading.Event(), lambda _: None)
    assert len(fetched) == 4
    assert len(result["sources"]) == 3
    assert "1 result page" in result["warning"]
    assert "hidden" not in str(result["sources"])


def test_retrieval_skips_hosts_that_timed_out():
    class Provider:
        def search(self, query):
            return [web.SearchHit(f"Launch update {i}", f"https://{host}.example/{i}")
                    for i, host in enumerate(["slow", "slow", "fast", "slow", "fast"])]

    fetched = []

    def fetch(url):
        fetched.append(url)
        if "slow" in url:
            raise TimeoutError("timed out")
        return url, "<p>Launch is Friday.</p>", "text/html"

    result = web.retrieve("launch", Provider(), fetch, threading.Event(), lambda _: None)
    assert [url for url in fetched if "slow" in url] == ["https://slow.example/0"]
    assert len(result["sources"]) == 2
    assert "3 result page" in result["warning"]


@pytest.mark.parametrize("hits,match", [([], "no results"),
                                       ([web.SearchHit("query", "http://localhost")], "no public")])
def test_empty_or_unreadable_results_fail(hits, match):
    class Provider:
        def search(self, query): return hits

    with pytest.raises(EngineError, match=match):
        web.retrieve("query", Provider(), lambda _: pytest.fail("unsafe fetch"),
                     threading.Event(), lambda _: None)


def stalled_worker(connection, query):
    connection.send(("progress", "Reading web pages…"))
    threading.Event().wait(60)


def test_isolated_reader_can_stop_a_blocked_network_call():
    cancel = threading.Event()
    started = time.monotonic()
    with pytest.raises(EngineError, match="stopped"):
        web.retrieve_isolated("question", cancel, lambda _: cancel.set(), worker=stalled_worker)
    assert time.monotonic() - started < 5


def test_isolated_reader_deadline():
    with pytest.raises(EngineError, match="timed out"):
        web.retrieve_isolated("question", threading.Event(), lambda _: None,
                              timeout=0.1, worker=stalled_worker)


FED_QUESTION = "How many points did the FED raise interest rates by recently"


def test_fed_question_becomes_a_neutral_dated_decision_search():
    from datetime import date

    assert web.search_query(FED_QUESTION, today=date(2026, 9, 17)) == (
        "Federal Reserve interest rate decision September 2026"
    )
    # Explicit dates and additional subjects must not be overwritten by 'recently'.
    historical = web.search_query("How much did the Fed raise interest rates in July 2023?")
    assert "July 2023" in historical
    comparison = web.search_query(
        "Compare recent Fed interest rates with European Central Bank rates"
    )
    assert "European Central Bank" in comparison


def test_provider_encodes_the_complete_focused_query(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    requested = []

    def fetch(url):
        requested.append(url)
        return url, '<li class="b_no">There are no results</li>', "text/html"

    monkeypatch.setattr(web, "fetch_public", fetch)
    focused = "Federal Reserve interest rate decision September 2026"
    web.BingProvider().search(focused)
    assert "Federal%20Reserve%20interest%20rate" in requested[0]
    assert parse_qs(urlsplit(requested[0]).query)["q"] == [focused]


def test_fed_search_rejects_clinic_dictionary_and_misleading_page_content():
    queries, fetched = [], []

    class Provider:
        def search(self, query):
            queries.append(query)
            return [
                web.SearchHit("Heart Rate: What's Normal? Cleveland Clinic",
                              "https://my.clevelandclinic.org/heart-rate"),
                web.SearchHit("MANY | English meaning - Cambridge Dictionary",
                              "https://dictionary.cambridge.org/dictionary/english/many"),
                web.SearchHit("Federal Reserve interest rate decision",
                              "https://example.com/misleading"),
                web.SearchHit("Federal Reserve Board - Implementation Note",
                              "https://www.federalreserve.gov/decision"),
            ]

    def fetch(url):
        fetched.append(url)
        if url.endswith("/misleading"):
            return url, "<p>Welcome to the clinic. Heart rate and pressure points.</p>", "text/html"
        assert "federalreserve.gov" in url
        return url, (
            "<p>The Federal Reserve announced its interest rate decision. "
            "The target range for the federal funds rate increased by 25 basis points.</p>"
        ), "text/html"

    result = web.retrieve(FED_QUESTION, Provider(), fetch, threading.Event(), lambda _: None)
    assert len(queries) == 1 and "Federal Reserve interest rate decision" in queries[0]
    assert len(result["sources"]) == 1  # Never pad to three with unrelated results.
    assert "federalreserve.gov" in result["sources"][0]["url"]
    assert all("clevelandclinic" not in url and "cambridge" not in url for url in fetched)
    assert "3 unrelated" in result["warning"]
    assert result["search_query"] == queries[0]


def test_unrelated_results_stop_instead_of_becoming_model_evidence():
    class Provider:
        def search(self, query):
            return [web.SearchHit("Many - Wikipedia", "https://en.wikipedia.org/wiki/Many")]

    with pytest.raises(EngineError, match="No sufficiently relevant"):
        web.retrieve(FED_QUESTION, Provider(), lambda _: pytest.fail("unrelated fetch"),
                     threading.Event(), lambda _: None)


def test_second_topic_rejects_results_matching_only_one_word():
    class Provider:
        def search(self, query):
            return [web.SearchHit("SOLID software principles", "https://example.com/solid"),
                    web.SearchHit("Solid-state batteries for electric vehicles",
                                  "https://example.com/batteries")]

    def fetch(url):
        if url.endswith("/solid"):
            return url, "SOLID is a set of principles in software design.", "text/plain"
        return url, "Solid state batteries are being developed for electric vehicles.", "text/plain"

    result = web.retrieve("What are the latest developments in solid state batteries "
                          "for electric vehicles?", Provider(), fetch, threading.Event(),
                          lambda _: None)
    assert [source["url"] for source in result["sources"]] == ["https://example.com/batteries"]


def test_page_excerpts_rank_subject_terms_above_question_filler():
    text = ("How many people know what is the latest news? " * 60
            + "\nThe Federal Reserve interest rate decision raised the target by 25 basis points.")
    assert "25 basis points" in web.extract_text(text, "text/plain", FED_QUESTION)


def test_excerpts_keep_short_publication_and_update_datelines_with_labels():
    article = (
        "<nav>September 1, 2020</nav>"
        "<div>Published <time>September 16, 2026 2:00pm EDT</time></div>"
        "<div>Updated</div><div>September 17, 2026 4:00pm EDT</div>"
        + "".join(f"<p>Federal Reserve interest rate decision: item {i}. "
                  + "Interest rates increased by 25 basis points. " * 12 + "</p>"
                  for i in range(10))
    )
    excerpt = web.extract_text(article, "text/html", FED_QUESTION)
    assert "Published September 16, 2026 2:00pm EDT" in excerpt
    assert "Updated\nSeptember 17, 2026 4:00pm EDT" in excerpt
    assert "25 basis points" in excerpt
    assert "2020" not in excerpt
    assert len(excerpt) <= 2400


@pytest.mark.parametrize("dateline", ["2026-09-16", "16 September 2026", "Sep. 16, 2026"])
def test_plain_text_datelines_survive_the_excerpt_budget(dateline):
    text = "Published\n" + dateline + "\n" + "\n".join(
        f"Federal Reserve interest rate decision {i}. " * 30 for i in range(8)
    )
    excerpt = web.extract_text(text, "text/plain", FED_QUESTION)
    assert "Published\n" + dateline in excerpt
    assert len(excerpt) <= 2400


def test_date_heavy_pages_still_leave_room_for_the_answer():
    text = "\n".join(f"September {day}, 2026" for day in range(1, 30))
    text += "\nThe Federal Reserve interest rate decision raised the target by 25 basis points."
    excerpt = web.extract_text(text, "text/plain", FED_QUESTION)
    assert "25 basis points" in excerpt
    assert len(excerpt) <= 2400
