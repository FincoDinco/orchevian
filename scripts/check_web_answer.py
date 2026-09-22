"""Check live search and a local model together, using a temporary chat library.

Run: .venv/bin/python scripts/check_web_answer.py --model mlx/ORG/MODEL "question"
The report includes evidence and the answer for human review. Passing verifies
completion, a citation to retrieved evidence, and no retrieval on the off turn;
--expect-date also checks the visible answer for an independently verified date.
These checks do not establish factual accuracy or freshness.
"""

import argparse
import json
import multiprocessing
import re
import tempfile
from datetime import date
from pathlib import Path

from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import GenerationParams
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore


def mentions_date(answer, expected):
    """Check the visible answer, excluding dates embedded only in link targets."""
    visible = re.sub(r"https?://\S+", "", answer)
    month = rf"(?:{expected.strftime('%B')}|{expected.strftime('%b')}\.?)"
    day = rf"0?{expected.day}(?:st|nd|rd|th)?"
    return bool(re.search(
        rf"\b(?:{expected.isoformat()}|{month}\s+{day},?\s+{expected.year}|"
        rf"{day}\s+{month},?\s+{expected.year})\b", visible, re.I,
    ))


def check(model_id, question, *, timeout=240, max_tokens=1024, expected_date=None):
    registry = BackendRegistry()
    session = ModelSession(registry)
    report = {"ok": False, "model": model_id, "question": question}
    try:
        models, availability = registry.list_models()
        model = next((item for item in models if item.ref.id == model_id), None)
        if model is None:
            raise ValueError(f"Model not found locally: {model_id}")
        available, reason = availability[str(model.ref.backend)]
        if not available:
            raise ValueError(reason or "Model runtime is unavailable")
        with tempfile.TemporaryDirectory(prefix="orchevian-web-answer-") as directory:
            with SqliteStore(Path(directory) / "chat.db") as store:
                library = LibraryService(store)
                errors, completions, calls = [], [], []
                chat = ChatService(
                    library, session,
                    on_error=lambda cid, error: errors.append(str(error)),
                    on_done=lambda cid, **result: completions.append(result),
                )
                retriever = chat.web.retriever

                def tracked_retriever(query, cancel, progress):
                    calls.append(query)
                    print(f"Search query: {query}", flush=True)
                    return retriever(query, cancel, progress)

                chat.web.retriever = tracked_retriever
                cid = library.create_conversation(model=model.ref).summary.id

                def turn(prompt, enabled):
                    errors.clear()
                    completions.clear()
                    chat.send(
                        cid, prompt, GenerationParams(temperature=0, max_tokens=max_tokens),
                        web_search=enabled,
                    )
                    worker = chat._worker_thread
                    worker.join(timeout)
                    if worker.is_alive():
                        chat.cancel_current()
                        session.force_unload()
                        worker.join(10)
                        raise TimeoutError(f"Model turn exceeded {timeout} seconds")
                    if errors:
                        raise RuntimeError(errors[0])
                    if not completions or completions[-1]["cancelled"]:
                        raise RuntimeError("Model turn did not complete")
                    messages = library.get_conversation(cid).messages
                    if not messages or messages[-1].role != "assistant":
                        raise RuntimeError("No assistant answer was saved")
                    return messages[-1].content

                try:
                    report["answer"] = turn(question, True)
                    report["evidence"] = chat.web.history(cid)[0]
                    report["cited_retrieved_url"] = any(
                        f"]({source['url']})" in report["answer"]
                        for source in report["evidence"]["sources"]
                    )
                    count = len(calls)
                    report["search_off_answer"] = turn(
                        "Reply with the single word Ready.", False,
                    )
                    report["search_off_no_retrieval"] = (
                        len(calls) == count and chat.web.history(cid)[0]["status"] == "off"
                    )
                    report["ok"] = (
                        report["evidence"]["status"] == "ready"
                        and report["cited_retrieved_url"]
                        and report["search_off_no_retrieval"]
                    )
                    if expected_date is not None:
                        report["expected_date"] = expected_date.isoformat()
                        report["event_date_in_answer"] = mentions_date(
                            report["answer"], expected_date,
                        )
                        report["ok"] = report["ok"] and report["event_date_in_answer"]
                finally:
                    report["search_calls"] = calls
                    if "evidence" not in report:
                        report["evidence"] = chat.web.history(cid)
                    chat.cancel_current()
                    session.force_unload()
                    if chat._worker_thread is not None:
                        chat._worker_thread.join(10)
    except (EngineError, OSError, ValueError, RuntimeError, TimeoutError) as exc:
        report["error"] = str(exc)
    finally:
        session.force_unload()
        registry.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--model", required=True, help="Installed backend/name model ID")
    parser.add_argument("--timeout", type=float, default=240, help="Seconds per chat turn")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    parser.add_argument("--expect-date", type=date.fromisoformat,
                        help="Require this independently checked event date in prose (YYYY-MM-DD)")
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_tokens <= 0:
        parser.error("timeout and max-tokens must be positive")
    report = check(args.model, args.question, timeout=args.timeout, max_tokens=args.max_tokens,
                   expected_date=args.expect_date)
    output = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
