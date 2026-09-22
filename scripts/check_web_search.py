"""Live provider/page-reading check; no model or saved library is needed.

Run: .venv/bin/python scripts/check_web_search.py "Python official documentation"
No account, API key or hosted application backend is required.
"""

import argparse
import json
import multiprocessing
import threading

from llm_engine.domain.errors import EngineError
from llm_engine.services.web_retrieval import retrieve_isolated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="Python official documentation")
    args = parser.parse_args()
    try:
        result = retrieve_isolated(args.query, threading.Event(), print)
    except EngineError as exc:
        print(json.dumps({"ok": False, "provider": "Bing public web search", "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "provider": "Bing public web search", **result}, indent=2))
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
