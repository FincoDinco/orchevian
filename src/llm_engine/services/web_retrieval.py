"""Bounded public-web retrieval. Production requests run in a disposable process."""

from __future__ import annotations

import base64
import http.client
import ipaddress
import multiprocessing
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from llm_engine.domain.errors import EngineError

MAX_BYTES = 1_000_000
MAX_QUERY = 500
MAX_SOURCES = 3
MAX_CONTEXT = 8_000
NETWORK_TIMEOUT = 8
MAX_CANDIDATES = 8

_FILLER = frozenset("""
a an the and or but of to for in on at by with from into about as is are was were
be been being do does did has have had can could would should will may might must
i me my we our you your it its they their them this that these those there here
what which who whom whose when where why how many much please tell give show find
search web internet look up explain help want need know information using use
""".split())
_TIME_WORDS = frozenset("""
recent recently latest current currently today now new news update updates development
developments january february march april june july august september october november december
""".split())
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Sept|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_PAGE_DATE = re.compile(
    rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|"
    rf"\d{{1,2}}\s+{_MONTH}\s+\d{{4}})\b", re.I,
)
_DATE_LABEL = re.compile(r"\b(?:published|updated|modified|posted|released|dated)\b", re.I)


def _subject_phrase(query):
    matches = re.findall(r"\b[\w-]+\s+(?:\d+(?:\.\d+)+|[IVX]{1,5})\b", query)
    return matches[0] if len(matches) == 1 else None


def search_query(question: str, *, today: date | None = None) -> str:
    """Turn the current question into a bounded topic query without adding chat history."""
    today = today or date.today()
    question = question[:MAX_QUERY]
    asks_date = bool(re.search(r"\bwhen\b|\b(?:what|which)\s+(?:exact\s+)?date\b",
                               question, re.I))
    owners = set(re.findall(r"\b([A-Z][\w-]+)['’]s\b", question))
    recent = bool(set(re.findall(r"\w+", question.lower())) & _TIME_WORDS)
    # Resolve the ambiguous acronym only when the question names its monetary context.
    monetary = bool(re.search(r"\b(?:interest|funds?)\s+rates?\b", question, re.I))
    fed = bool(re.search(r"\b(?:fed|federal reserve|fomc)\b", question, re.I))
    words = set(re.findall(r"\w+", question.casefold()))
    decision_words = set("""
        fed federal reserve fomc interest fund funds rate rates raise raised hike hiked
        increase increased cut cuts decrease decreased lower lowered hold held change changed
        point points basis percentage percent amount decision meeting last most
    """.split())
    simple_decision = words <= _FILLER | _TIME_WORDS | decision_words
    if monetary and fed and recent and simple_decision:
        # A rate question can contain a false premise (raise/cut/hold). Search for
        # the decision itself, then let the evidence establish its direction/size.
        return f"Federal Reserve interest rate decision {today.strftime('%B %Y')}"
    if monetary and fed:
        question = re.sub(r"\b(?:fed|fomc)\b", "Federal Reserve", question, flags=re.I)
    # Keep the subject first: leading 'date' made ordinary launch/release
    # questions return calendar sites. Preserve version numbers and named
    # Roman-numeral suffixes instead of turning 'Python 3.13.0' into '3 13 0'
    # or deleting the I in 'Artemis I' as if it were the pronoun.
    question = re.sub(
        r"^\s*(?:on\s+)?(?:what|which)\s+(?:exact\s+)?date\s+",
        "", question, flags=re.I,
    )
    question = re.sub(r"(?<=\w)['’]s\b", "", question)
    tokens = re.findall(r"\d+(?:\.\d+)+|[\w]+(?:[-'][\w]+)*", question, re.UNICODE)
    words = [word for index, word in enumerate(tokens)
             if word.lower() not in _FILLER or (
                 word == "I" and index and tokens[index - 1][0].isupper()
                 and tokens[index - 1].lower() not in _FILLER
             )]
    focused = " ".join(words)
    phrase = _subject_phrase(focused)
    if asks_date and phrase:
        remaining = set(focused.replace(phrase, "", 1).split()) - owners
        events = {"launch": "launch", "launched": "launch", "release": "release",
                  "released": "release"}
        matched = remaining & events.keys()
        # Only simplify a single launch/release-date question. Preserve extra
        # qualifiers (platform, location, comparisons, etc.) in all other cases.
        if len(matched) == 1 and remaining <= events.keys() | {"mission", "version", "date"}:
            focused = f"{phrase} {events[next(iter(matched))]} date"
    return focused.strip()[:MAX_QUERY]


def topic_terms(text: str) -> set[str]:
    text = re.sub(r"\bfederal\s+reserve\b|\bfed\b|\bfomc\b", "federalreserve", text,
                  flags=re.I)
    terms = set()
    for word in re.findall(r"[^\W_]+", unquote(text).casefold()):
        if (len(word) < 3 or word.isdecimal() or word in _FILLER
                or word in _TIME_WORDS or word in {"decision", "official"}):
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        terms.add(word)
    return terms


def relevance(terms: set[str], text: str) -> float:
    return len(terms & topic_terms(text)) / len(terms) if terms else 0.0


def public_url(url: str) -> str:
    """Validate syntax before DNS. Every resolved address is checked separately."""
    try:
        if len(url) > 1000 or re.search(r"[\s\x00-\x1f\x7f\\]", url):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443}):
            raise ValueError()
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or "%" in host:
                raise ValueError() from None
        else:
            if not _public_address(address):
                raise ValueError()
        return urlunsplit(parsed._replace(fragment=""))
    except ValueError as exc:
        raise EngineError("web_failed", "Only public HTTP(S) pages may be read.") from exc


def _public_address(address):
    # Reject transition addresses as well as loopback, private and special-use ranges.
    return address.is_global and not address.is_multicast and not (
        isinstance(address, ipaddress.IPv6Address)
        and (address.ipv4_mapped or address.sixtofour or address.teredo
             or address in ipaddress.ip_network("64:ff9b::/96"))
    )


class _PublicConnection(http.client.HTTPConnection):
    """Connect directly to a validated address, preventing a second DNS lookup."""

    def __init__(self, host, port, *, tls):
        super().__init__(host, port, timeout=NETWORK_TIMEOUT)
        self.tls = tls

    def connect(self):
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(
            not _public_address(ipaddress.ip_address(item[4][0])) for item in addresses
        ):
            raise EngineError("web_failed", "The page resolved to a non-public address.")
        last_error = None
        # One budget across all addresses: an unreachable dual-stack host would
        # otherwise spend the timeout once per address and exhaust the search deadline.
        deadline = time.monotonic() + self.timeout
        for family, kind, protocol, _, sockaddr in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock = socket.socket(family, kind, protocol)
            sock.settimeout(remaining)
            try:
                sock.connect(sockaddr)
                sock.settimeout(self.timeout)
                self.sock = (
                    ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                    if self.tls else sock
                )
                return
            except OSError as exc:
                sock.close()
                last_error = exc
        raise last_error or TimeoutError("Could not connect")


def fetch_public(url: str) -> tuple[str, str, str]:
    """No cookies, credentials, proxies, scripts, subresources or automatic redirects."""
    for _ in range(5):
        url = public_url(url)
        parsed = urlsplit(url)
        tls = parsed.scheme == "https"
        conn = _PublicConnection(parsed.hostname, parsed.port or (443 if tls else 80), tls=tls)
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            conn.request("GET", path, headers={
                "User-Agent": "Orchevian/0.1 (public-page reader)",
                "Accept": "text/html, text/plain;q=0.9",
                "Accept-Encoding": "identity",
            })
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise EngineError("web_failed", "The page returned an invalid redirect.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                if response.status in {202, 403, 429}:
                    raise EngineError(
                        "web_failed", "The site blocked or rate-limited this request "
                        f"(HTTP {response.status}). Try later or turn Web search off."
                    )
                raise EngineError("web_failed", f"The site returned HTTP {response.status}.")
            mime = response.headers.get_content_type()
            if mime not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise EngineError("web_failed", "This page is not readable HTML or text.")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise EngineError("web_failed", "The site returned an unsupported encoding.")
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise EngineError("web_failed", "The page exceeds the 1 MB reading limit.")
            encoding = response.headers.get_content_charset() or "utf-8"
            try:
                content = data.decode(encoding, errors="replace")
            except LookupError:
                content = data.decode("utf-8", errors="replace")
            return url, content, mime
        finally:
            conn.close()
    raise EngineError("web_failed", "The page redirected too many times.")


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str


class SearchProvider(Protocol):
    name: str

    def search(self, query: str) -> list[SearchHit]: ...


class _SearchHTML(HTMLParser):
    """Read only organic result headings from Bing's public HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits = []
        self._url = None
        self._title = []
        self._li_depth = 0
        self._result_depth = None
        self._heading = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self._li_depth += 1
            if "b_algo" in attrs.get("class", "").split():
                self._result_depth = self._li_depth
        if tag == "h2" and self._result_depth is not None:
            self._heading = True
        if tag == "a" and self._heading:
            url = urljoin("https://www.bing.com", attrs.get("href", ""))
            try:
                parsed = urlsplit(url)
                if parsed.hostname in {"bing.com", "www.bing.com"}:
                    encoded = parse_qs(parsed.query).get("u", [""])[0]
                    if not encoded.startswith("a1"):
                        return
                    encoded = encoded[2:]
                    url = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
                self._url = public_url(url)
                self._title = []
            except (ValueError, UnicodeError, EngineError):
                self._url = None

    def handle_data(self, data):
        if self._url:
            self._title.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._url:
            title = " ".join(" ".join(self._title).split())[:200]
            if self._url not in {hit.url for hit in self.hits}:
                self.hits.append(SearchHit(title or self._url, self._url))
            self._url = None
        if tag == "h2":
            self._heading = False
        if tag == "li":
            if self._li_depth == self._result_depth:
                self._result_depth = None
                self._heading = False
            self._li_depth = max(0, self._li_depth - 1)


class BingProvider:
    """Public HTML search: no API, account, key, login or challenge bypass."""

    name = "Bing public web search"

    def search(self, query):
        _, content, _ = fetch_public(
            "https://www.bing.com/search?" + urlencode({"q": query[:MAX_QUERY]}, quote_via=quote)
        )
        parser = _SearchHTML()
        parser.feed(content)
        if not parser.hits:
            if "b_no" in content and "no results" in content.lower():
                return []
            raise EngineError(
                "web_failed", "Bing returned a blocked or unrecognized search page. "
                "Try later or turn Web search off."
            )
        return parser.hits[:MAX_CANDIDATES]


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "template", "nav", "footer"}:
            self.hidden.append(tag)
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(content, mime, query):
    if mime != "text/plain":
        parser = _PageText()
        parser.feed(content)
        content = " ".join(parser.parts)
    paragraphs = list(dict.fromkeys(
        " ".join(p.split()) for p in content.splitlines() if p.strip()
    ))
    # Keep useful question-matching passages when navigation precedes article text.
    chunks = [p[i:i + 800] for p in paragraphs for i in range(0, len(p), 800)]
    terms = topic_terms(query)
    ranked = sorted(enumerate(chunks), key=lambda item: (
        -len(terms & topic_terms(item[1])), -len(item[1]), item[0]
    ))
    # Short datelines otherwise lose to long topic-heavy paragraphs. Preserve
    # up to two with their preceding labels, keeping publication/update dates
    # as source text rather than claiming they are the event date.
    date_indices = set()
    dates = 0
    for index, text in enumerate(chunks):
        if len(text) <= 160 and _PAGE_DATE.search(text):
            date_indices.add(index)
            if index and len(chunks[index - 1]) <= 80 and _DATE_LABEL.search(chunks[index - 1]):
                date_indices.add(index - 1)
            dates += 1
            if dates == 2:
                break
    selected = [(index, chunks[index]) for index in sorted(date_indices)]
    remaining = 2400
    remaining -= sum(len(text) + 1 for _, text in selected)
    for index, text in ranked:
        if index in date_indices:
            continue
        if remaining <= 1:
            break
        text = text[:remaining - 1]
        selected.append((index, text))
        remaining -= len(text) + 1
    return "\n".join(text for _, text in sorted(selected))


def retrieve(query, provider, fetch, cancel, progress):
    if cancel.is_set():
        raise EngineError("cancelled", "Web search stopped.")
    focused = search_query(query)
    terms = topic_terms(focused)
    phrase = _subject_phrase(focused)
    if not terms:
        raise EngineError(
            "web_failed", "Include the subject you want to search for in this message."
        )
    progress(f"Searching: {focused}")
    hits = provider.search(focused)
    if not hits:
        raise EngineError("web_failed", "Web search found no results. Try a different question.")
    sources = []
    failures = 0
    unrelated = 0
    seen = set()
    timed_out_hosts = set()
    # Shared units alone (heart rate vs interest rate, pressure points vs basis
    # points) do not identify a subject. Require a topical word in result metadata.
    subject_terms = terms - {"rate", "point", "cost", "price", "value", "amount",
                             "number", "level", "change", "percent", "time", "date"}
    subject_terms = subject_terms or terms
    candidates = hits[:MAX_CANDIDATES]
    candidates = sorted(candidates, key=lambda hit: -relevance(terms, hit.title + " " + hit.url))
    for hit in candidates:
        if cancel.is_set():
            raise EngineError("cancelled", "Web search stopped.")
        if not relevance(subject_terms, hit.title + " " + hit.url):
            unrelated += 1
            continue
        # A host that already timed out would likely consume the shared deadline again.
        host = urlsplit(hit.url).hostname
        if host in timed_out_hosts:
            failures += 1
            continue
        progress(f"Checking relevant pages ({len(sources)}/{MAX_SOURCES} selected)…")
        try:
            url, content, mime = fetch(public_url(hit.url))
            url = public_url(url)
            if url in seen:
                continue
            seen.add(url)
            excerpt = extract_text(content, mime, focused)
            if not excerpt:
                raise EngineError("web_failed", "No readable text.")
            if phrase and not re.search(r"\b" + re.escape(phrase) + r"\b", excerpt, re.I):
                unrelated += 1
                continue
            # Metadata alone is not evidence. Check the actual passages too, so
            # redirects, generic landing pages and off-topic results cannot fill the quota.
            if relevance(terms, excerpt) < 0.5:
                unrelated += 1
                continue
            sources.append({"title": hit.title[:200], "url": url, "excerpt": excerpt})
        except TimeoutError:
            timed_out_hosts.add(host)
            failures += 1
        except (EngineError, OSError, ValueError, http.client.HTTPException):
            failures += 1
        if len(sources) == MAX_SOURCES:
            break
    if cancel.is_set():
        raise EngineError("cancelled", "Web search stopped.")
    if not sources:
        if unrelated:
            raise EngineError(
                "web_failed", "No sufficiently relevant web pages were found for this question. "
                "Unrelated results were discarded. Try naming the subject and date explicitly."
            )
        raise EngineError(
            "web_failed", "Search returned results, but no public pages could be read."
        )
    warnings = []
    if failures:
        warnings.append(f"{failures} result page(s) could not be read.")
    if unrelated:
        warnings.append(f"{unrelated} unrelated result(s) were discarded.")
    return {"sources": sources, "search_query": focused, "warning": " ".join(warnings)}


def _retrieval_worker(connection, query):
    try:
        provider = BingProvider()
        result = retrieve(query, provider, fetch_public, threading.Event(),
                          lambda message: connection.send(("progress", message)))
        connection.send(("done", result))
    except Exception as exc:
        # Network exception strings may contain the query; do not return or log them.
        message = str(exc) if isinstance(exc, EngineError) else "Web search could not connect."
        connection.send(("error", message))
    finally:
        connection.close()


def retrieve_isolated(query, cancel, progress, *, timeout=45, worker=_retrieval_worker):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, query[:MAX_QUERY]), daemon=True)
    try:
        if cancel.is_set():
            raise EngineError("cancelled", "Web search stopped.")
        process.start()
        child.close()
        deadline = time.monotonic() + timeout
        while True:
            if cancel.is_set():
                raise EngineError("cancelled", "Web search stopped.")
            if time.monotonic() >= deadline:
                raise EngineError("web_failed", "Web search timed out. Try again or turn it off.")
            if parent.poll(0.05):
                kind, result = parent.recv()
                if kind == "progress":
                    progress(result)
                elif kind == "done":
                    if cancel.is_set():
                        raise EngineError("cancelled", "Web search stopped.")
                    return result
                else:
                    raise EngineError("web_failed", result)
            elif not process.is_alive():
                raise EngineError("web_failed", "The web reader stopped unexpectedly.")
    except (EOFError, OSError) as exc:
        raise EngineError("web_failed", "The web reader could not finish.") from exc
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(0.5)
            if process.is_alive():
                process.kill()
                process.join(0.5)
            process.close()
