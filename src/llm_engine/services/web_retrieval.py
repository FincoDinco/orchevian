"""Bounded public-web retrieval. Production requests run in a disposable process."""

from __future__ import annotations

import http.client
import ipaddress
import json
import multiprocessing
import re
import socket
import ssl
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from llm_engine.domain.errors import EngineError

# News article HTML is often 1-3 MB with inline scripts; larger pages are refused.
MAX_BYTES = 3_000_000
MAX_QUERY = 500
MAX_SOURCES = 3
MAX_CONTEXT = 8_000
NETWORK_TIMEOUT = 8
MAX_CANDIDATES = 10

_FILLER = frozenset("""
a an the and or but of to for in on at by with from into about as is are was were
be been being do does did has have had can could would should will may might must
i me my we our you your it its they their them this that these those there here
what which who whom whose when where why how many much please tell give show find
search web internet look up explain help want need know information using use
searching searched searches specifically exactly again else also just actually regarding
he she him her his hers say says said saying
curious wondering wonder wondered interested asking asked
""".split())
_TIME_WORDS = frozenset("""
recent recently latest current currently today yesterday tonight now new news update updates
development week weeks month
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


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b")


def _spell_dates(question: str) -> str:
    """'9/22' becomes 'September 22' so search does not see two bare numbers."""
    def spell(match):
        month, day, year = int(match[1]), int(match[2]), match[3]
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return match[0]
        text = f"{_MONTHS[month - 1]} {day}"
        if year:
            text += f" {year if len(year) == 4 else '20' + year}"
        return text
    return _NUMERIC_DATE.sub(spell, question)


def resolve_follow_up(question: str, earlier: Sequence[str] = ()) -> str:
    """Borrow the subject of a recent earlier question when this one has none.

    'Specifically today 9/22' or 'Where are you searching?' alone would search
    for dictionary or film pages; only the current message is used otherwise.
    """
    recent = list(earlier)[-3:]
    if not topic_terms(search_query(question)):
        for previous in reversed(recent):
            if topic_terms(search_query(previous)):
                return f"{previous[:MAX_QUERY // 2]} {question}"
        return question
    # 'What did he say about the Fed?' has a topic but no person: carry the
    # first name from the latest earlier question that has one.
    if _PRONOUN.search(question) and not _names(question):
        for previous in reversed(recent):
            names = _names(previous)
            if names:
                # Topic first: search engines weigh leading words most, and a leading
                # famous name returns biography pages instead of the topic.
                return _join(question, names[0])
        # No capitalized name to point at ('donald trump'): borrow the question.
        for previous in reversed(recent):
            if topic_terms(search_query(previous)):
                return _join(question, previous[:MAX_QUERY // 2])
    return question


def _join(question: str, borrowed: str) -> str:
    # A comma, not '? ': after sentence punctuation a name reads as a sentence
    # start and would not be recognized as a name.
    return f"{question.rstrip(' ?.!')}, {borrowed}"


_PRONOUN = re.compile(r"\b(?:he|she|him|her|his|hers|they|them|their)\b", re.I)


def _entities(text: str) -> list[set[str]]:
    """Topic terms of each named subject: capitalized runs and acronyms (FED, NASA).

    A page must mention every one to count. A multi-word name is identified by
    its last word ('Trump', 'Assembly', 'Bank'): 'Donald' alone matched a
    Wikipedia page about the given name.
    """
    runs = [run.split()[-1] for run in _names(text)] + re.findall(r"\b[A-Z]{3,}\b", text)
    return [terms for terms in map(topic_terms, runs) if terms]


def _names(text: str) -> list[str]:
    """Runs of capitalized words after the first word, e.g. 'Donald Trump'."""
    runs = []
    for run in re.findall(r"(?<!^)(?<![.?!]\s)\b(?:[A-Z][a-z][\w-]*\s?)+", text.strip()):
        words = [word for word in run.split()
                 if word.lower() not in _FILLER and word not in _MONTHS]
        if words:
            runs.append(" ".join(words))
    return runs


def search_query(question: str, *, today: date | None = None) -> str:
    """Turn a question into a bounded topic query; follow-ups are resolved beforehand."""
    today = today or date.today()
    question = _spell_dates(question[:MAX_QUERY])
    name_words = {word for run in _names(question) for word in run.split()}
    period = re.search(r"\b(last|this|past)\s+(week|month)\b", question, re.I)
    if period:
        back = 7 if period[2].lower() == "week" else 30
        day = today - timedelta(days=back if period[1].lower() != "this" else 0)
        question = (question[:period.start()] + f" {_MONTHS[day.month - 1]} {day.year} "
                    + question[period.end():])
    explicit_date = bool(re.search(rf"\b{_MONTH}\s+\d{{1,2}}\b", question, re.I))
    relative = re.findall(r"\b(today|yesterday|tonight)\b", question, re.I)
    if relative:
        # News search needs the calendar date (the model never sees this query).
        # The latest relative word wins: a follow-up can say 'what about yesterday?'.
        question = re.sub(r"\b(?:today|yesterday|tonight)\b", " ", question, flags=re.I)
        if not explicit_date:
            day = today - timedelta(days=1) if relative[-1].lower() == "yesterday" else today
            question += f" {_MONTHS[day.month - 1]} {day.day} {day.year}"
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
    lead_fed = False
    if not (monetary and fed) and re.search(r"\b(?:Fed|FED|FOMC)\b", question):
        # Capitalized 'Fed'/'FED' names the central bank; lowercase 'fed' may not.
        question = re.sub(r"\b(?:Fed|FED|FOMC)(?:['’]s)?\b", "Federal Reserve", question)
        lead_fed = True
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
    # A borrowed subject can repeat words; search each once, in order.
    seen = set()
    words = [word for word in words
             if not (word.casefold() in seen or seen.add(word.casefold()))]
    # Named subjects lead: search engines weigh leading words most. 'weather Paris'
    # returned the searcher's local forecast, 'Paris weather' returned Paris;
    # 'donald trump Federal Reserve rates' returned biographies, while
    # 'Federal Reserve rates donald trump' returned Fed news. Reordered after
    # filtering so a version or numeral stays with its name ('Artemis I').
    leaders, rest, index = [], [], 0
    while index < len(words):
        group = [words[index]]
        while index + 1 < len(words) and re.fullmatch(r"[IVX]{1,5}|\d+(?:\.\d+)+",
                                                      words[index + 1]):
            index += 1
            group.append(words[index])
        (leaders if group[0] in name_words else rest).extend(group)
        index += 1
    words = leaders + rest
    if lead_fed:
        words = ["Federal", "Reserve"] + [w for w in words if w not in {"Federal", "Reserve"}]
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
                raise EngineError("web_failed", "The page exceeds the 3 MB reading limit.")
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
    # Page text supplied by the search provider (Tavily); empty means read the page.
    content: str = ""


class SearchProvider(Protocol):
    name: str

    def search(self, query: str) -> list[SearchHit]: ...


def _request_json(method: str, url: str, headers: dict,
                  payload: dict | None = None) -> tuple[int, dict]:
    """JSON request to a validated public HTTPS endpoint; returns (status, parsed body)."""
    parsed = urlsplit(public_url(url))
    conn = _PublicConnection(parsed.hostname, parsed.port or 443, tls=True)
    try:
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, path, body=body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Accept-Encoding": "identity", "User-Agent": "Orchevian/0.1", **headers,
        })
        response = conn.getresponse()
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise EngineError("web_failed", "The search response was too large.")
        text = data.decode("utf-8", errors="replace")
        if "event-stream" in (response.getheader("Content-Type") or ""):
            # Server-sent events: the reply is the last `data:` line.
            events = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = events[-1] if events else ""
        try:
            parsed_body = json.loads(text or "{}")
        except ValueError:
            parsed_body = {}
        return response.status, parsed_body if isinstance(parsed_body, dict) else {}
    finally:
        conn.close()


def _hit(title, url, text="") -> SearchHit | None:
    try:
        url = public_url(str(url or ""))
    except (ValueError, UnicodeError, EngineError):
        return None
    return SearchHit(" ".join(str(title or url).split())[:200], url, str(text or "")[:MAX_BYTES])


class _KeyedProvider:
    """A search service used with the person's own API key."""

    name = ""
    invalid_status = frozenset({401, 403})
    quota_status = frozenset({402, 432, 433})

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _check(self, status: int, body: dict) -> None:
        message = str(body.get("message") or body.get("error") or "").lower()
        if status in self.invalid_status:
            raise EngineError("web_key_invalid", f"{self.name} did not accept your API key. "
                              "Check it in Settings → Web Search.")
        if status in self.quota_status or "credit" in message:
            raise EngineError("web_quota", f"Your free {self.name} searches are used up.")
        if status == 429:
            raise EngineError("web_quota", f"{self.name} is limiting searches right now.")
        if status != 200:
            raise EngineError("web_failed", f"{self.name} returned an error (HTTP {status}).")

    def search(self, query):
        status, body = self._request(query[:MAX_QUERY])
        self._check(status, body)
        return [hit for hit in self._hits(body) if hit is not None][:MAX_CANDIDATES]


class TavilyProvider(_KeyedProvider):
    """Returns each page's text, so pages are not fetched separately."""

    name = "Tavily"

    def _request(self, query):
        return _request_json("POST", "https://api.tavily.com/search",
                             {"Authorization": f"Bearer {self.api_key}"},
                             {"query": query, "search_depth": "basic",
                              "max_results": MAX_CANDIDATES, "include_raw_content": "text"})

    def _hits(self, body):
        return [_hit(r.get("title"), r.get("url"), r.get("raw_content") or r.get("content"))
                for r in body.get("results") or [] if isinstance(r, dict)]


class ExaProvider(_KeyedProvider):
    """Returns each page's text, so pages are not fetched separately."""

    name = "Exa"

    def _request(self, query):
        return _request_json("POST", "https://api.exa.ai/search", {"x-api-key": self.api_key},
                             {"query": query, "numResults": MAX_CANDIDATES, "type": "auto",
                              "contents": {"text": {"maxCharacters": 20_000}}})

    def _hits(self, body):
        return [_hit(r.get("title"), r.get("url"), r.get("text"))
                for r in body.get("results") or [] if isinstance(r, dict)]


class SerperProvider(_KeyedProvider):
    """Google results with short snippets; result pages are then read directly."""

    name = "Serper"

    def _request(self, query):
        return _request_json("POST", "https://google.serper.dev/search",
                             {"X-API-KEY": self.api_key}, {"q": query, "num": MAX_CANDIDATES})

    def _hits(self, body):
        return [_hit(r.get("title"), r.get("link"))
                for r in body.get("organic") or [] if isinstance(r, dict)]


class BraveProvider(_KeyedProvider):
    """Brave's own index; result pages are then read directly."""

    name = "Brave Search"

    def _request(self, query):
        params = urlencode({"q": query, "count": MAX_CANDIDATES}, quote_via=quote)
        return _request_json("GET", "https://api.search.brave.com/res/v1/web/search?" + params,
                             {"X-Subscription-Token": self.api_key})

    def _hits(self, body):
        results = (body.get("web") or {}).get("results") or []
        return [_hit(r.get("title"), r.get("url")) for r in results if isinstance(r, dict)]


@dataclass(frozen=True)
class SearchService:
    """What Settings shows a newcomer about each supported service."""

    id: str
    name: str
    provider: type
    signup_url: str
    free_plan: str
    needs_card: bool = False


# Recommended order, and the order keys are tried in.
SEARCH_SERVICES = (
    SearchService("exa", "Exa", ExaProvider, "https://dashboard.exa.ai",
                  "Free monthly credits"),
    SearchService("serper", "Serper", SerperProvider, "https://serper.dev",
                  "2,500 free searches to start (one time)"),
    SearchService("tavily", "Tavily", TavilyProvider, "https://app.tavily.com",
                  "1,000 free searches every month", needs_card=True),
    SearchService("brave", "Brave Search", BraveProvider, "https://api-dashboard.search.brave.com",
                  "About 1,000 free searches every month", needs_card=True),
)
_SERVICES = {service.id: service for service in SEARCH_SERVICES}


class ExaFreeProvider:
    """Exa's free keyless search (its hosted MCP endpoint): the default, no setup.

    Exa documents "free rate-limited usage without sign-in or API key". One
    stateless JSON-RPC call; results arrive as text blocks with page highlights.
    """

    name = "Exa (free)"
    natural_language = True
    URL = "https://mcp.exa.ai/mcp"

    def search(self, query):
        status, body = _request_json("POST", self.URL, {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "web_search_exa",
            "arguments": {"query": query[:MAX_QUERY], "numResults": MAX_CANDIDATES,
                          "objective": ("Pages that directly answer this question, with dates "
                                        f"and exact quotes where relevant: {query[:MAX_QUERY]}")},
        }})
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        text = "\n".join(str(item.get("text") or "") for item in result.get("content") or []
                         if isinstance(item, dict))
        if status == 429 or "rate limit" in text.lower()[:300]:
            raise EngineError("web_quota", "Exa's free search is busy or at its limit right now.")
        if status != 200 or body.get("error") or result.get("isError"):
            raise EngineError("web_failed", "Exa's free search did not answer.")
        hits = []
        for block in text.split("\n---\n"):
            fields = dict(re.findall(r"^(Title|URL|Published): *(.*)$", block, re.M))
            highlights = block.split("Highlights:", 1)[-1].strip() if "Highlights:" in block else ""
            dated = f"Published {fields['Published'][:10]}\n" if fields.get("Published") else ""
            hit = _hit(fields.get("Title"), fields.get("URL"), (dated + highlights).strip())
            if hit is not None:
                hits.append(hit)
        return hits[:MAX_CANDIDATES]


class SearchChain:
    """Services with the person's keys (in order), then Exa's free search.

    `notice` explains any service that was skipped, for the Web sources panel.
    """

    takes_question = True

    def __init__(self, keys=()):
        self.providers = [_SERVICES[service].provider(key) for service, key in keys
                          if service in _SERVICES and key.strip()]
        self.providers.append(ExaFreeProvider())
        self.name = self.providers[0].name
        self.notice = ""

    def search(self, query, question=None):
        problems = []
        for provider in self.providers:
            text = question if question and getattr(provider, "natural_language", False) \
                else query
            try:
                hits = provider.search(text)
            except EngineError as exc:
                problems.append(str(exc))
                continue
            except (OSError, ValueError, http.client.HTTPException):
                problems.append(f"{provider.name} could not be reached.")
                continue
            self.name = provider.name
            self.notice = " ".join(problems)
            return hits
        # No scraping fallback: search engines' terms and robots.txt forbid
        # automated use of their results pages.
        raise EngineError(
            "web_failed", " ".join(problems) + " Try again in a moment, or add a free search "
            "key in Settings → Web Search.",
        )


KeyedSearch = SearchChain  # Earlier name.


def check_search_key(service: str, api_key: str) -> str:
    """Run one search (one free credit) to confirm a key; '' when it works, else why not."""
    if service not in _SERVICES:
        return "Unknown search service."
    if not api_key.strip():
        return "Paste a key first."
    try:
        _SERVICES[service].provider(api_key).search("Orchevian key check")
    except EngineError as exc:
        return str(exc)
    except (OSError, ValueError, http.client.HTTPException):
        return f"{_SERVICES[service].name} could not be reached. Check your internet connection."
    return ""


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


def retrieve(query, provider, fetch, cancel, progress, focus=None):
    """`focus` is the current question when `query` borrowed an earlier subject:
    pages must still cover its own topic, not only the borrowed name."""
    if cancel.is_set():
        raise EngineError("cancelled", "Web search stopped.")
    focused = search_query(query)
    terms = topic_terms(focused)
    focus_terms = topic_terms(search_query(focus)) if focus else set()
    # Named subjects come from the user's own words; a borrowed name only steers,
    # unless the follow-up names nothing itself ('Specifically today 9/22').
    entities = (_entities(focus) if focus else []) or _entities(query)
    phrase = _subject_phrase(focused)
    if not terms:
        raise EngineError(
            "web_failed", "Include the subject you want to search for in this message."
        )
    progress(f"Searching: {focused}")
    # Services built for plain-language questions get the question itself.
    hits = (provider.search(focused, question=query)
            if getattr(provider, "takes_question", False) else provider.search(focused))
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
            if hit.content:
                url, content, mime = public_url(hit.url), hit.content, "text/plain"
            else:
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
            if relevance(terms, excerpt) < 0.5 or (
                focus_terms and not relevance(focus_terms, excerpt)
            ) or not all(relevance(entity, excerpt) for entity in entities):
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
    notice = getattr(provider, "notice", "")
    if notice:
        warnings.insert(0, notice)
    return {"sources": sources, "search_query": focused, "warning": " ".join(warnings),
            "provider": getattr(provider, "name", "Web search")}


def _retrieval_worker(connection, query, focus=None, keys=None):
    try:
        provider = SearchChain(keys or ())
        result = retrieve(query, provider, fetch_public, threading.Event(),
                          lambda message: connection.send(("progress", message)), focus)
        connection.send(("done", result))
    except Exception as exc:
        # Network exception strings may contain the query; do not return or log them.
        message = str(exc) if isinstance(exc, EngineError) else "Web search could not connect."
        connection.send(("error", message))
    finally:
        connection.close()


def retrieve_isolated(query, cancel, progress, *, focus=None, keys=None, timeout=45,
                      worker=_retrieval_worker):
    """`keys` is [(service id, API key), ...] in the order to try them."""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    args = (child, query[:MAX_QUERY])
    if focus or keys:
        args += (focus[:MAX_QUERY] if focus else None,)
    if keys:
        # Passed to the reader process directly: never logged or put in a URL.
        args += (list(keys),)
    process = context.Process(target=worker, args=args, daemon=True)
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
