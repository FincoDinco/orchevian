from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from llm_engine import config
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import GenerationParams
from llm_engine.logging import setup_logging
from llm_engine.services.catalog import CatalogService
from llm_engine.services.chat import ChatService
from llm_engine.services.openai_api import ApiServerService, parse_model_id
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore, backup_path_for


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--db", type=Path, default=argparse.SUPPRESS, help="Path to data.db")
    shared.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="Path to config.json"
    )
    shared.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="DEBUG logging"
    )

    parser = argparse.ArgumentParser(
        prog="orchevian-engine",
        description="Orchevian engine CLI",
        parents=[shared],
    )
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", parents=[shared], help="Interactive chat (TTY)")
    chat.add_argument("--model", help="Model ID from the models command (backend/name)")
    chat.add_argument("--conversation", type=int, help="Resume a saved conversation")
    chat.add_argument("--max-tokens", type=int, default=2048, help="Maximum generated tokens")
    sub.add_parser("models", parents=[shared], help="List local models")
    serve = sub.add_parser("serve", parents=[shared], help="OpenAI-compatible HTTP API")
    serve.add_argument("--port", type=int, default=None, help="Bind port (host is 127.0.0.1)")
    sub.add_parser("migrate", parents=[shared], help="Migrate the SQLite database")
    sub.add_parser("health", parents=[shared], help="Print session, db, and API status")
    return parser


def _cmd_health(args: argparse.Namespace) -> int:
    cfg = config.load(args.config, args.db)
    print(f"db: {cfg.db_path}")
    print(f"config: {cfg.config_path}")
    print(f"model_dir: {cfg.model_dir}")
    print(f"api configured: {cfg.api_host}:{cfg.api_port}")
    print("live session: not inspected (use the running app's Settings → API)")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    cfg = config.load(args.config, args.db)
    with SqliteStore(cfg.db_path) as store:
        print(f"db: {store.path}")
        print(f"schema version: {store.schema_version()}")
    backup = backup_path_for(cfg.db_path)
    if backup.exists():
        print(f"pre-engine backup: {backup}")
    return 0


# Long enough that a key someone picks by hand can't be guessed.
MIN_API_KEY_LENGTH = 16


def _cmd_serve(args: argparse.Namespace) -> int:
    # The person running serve chooses the key, so it is never printed or logged.
    supplied = os.environ.get("ORCHEVIAN_API_KEY", "").strip()
    if len(supplied) < MIN_API_KEY_LENGTH:
        raise EngineError(
            "config_invalid",
            f"Set ORCHEVIAN_API_KEY to the key your clients will send, at least "
            f"{MIN_API_KEY_LENGTH} characters. On macOS or Linux, for example: "
            'export ORCHEVIAN_API_KEY="ov-$(openssl rand -hex 24)"',
        )
    cfg = config.load(args.config, args.db)
    registry = BackendRegistry(cfg=cfg)
    session = ModelSession(registry)
    stopped = threading.Event()
    previous_term = signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
    # API inference is stateless; an in-memory library keeps serve off the user's DB.
    with SqliteStore(":memory:") as store:
        chat = ChatService(LibraryService(store), session)
        api = ApiServerService(chat, CatalogService(registry, session), port=cfg.api_port,
                               api_key=supplied)
        try:
            status = api.start(port=args.port)
            print(f"Local API: {status['url']} (Ctrl+C to stop)", flush=True)
            print("Clients send ORCHEVIAN_API_KEY as 'Authorization: Bearer <key>'.", flush=True)
            while api.status()["running"] and not stopped.wait(0.2):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            try:
                api.stop()
            finally:
                registry.close()
                signal.signal(signal.SIGTERM, previous_term)
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    if args.max_tokens < 1:
        raise EngineError("config_invalid", "--max-tokens must be positive")
    cfg = config.load(args.config, args.db)
    registry = BackendRegistry(cfg=cfg)
    session = ModelSession(registry)
    with SqliteStore(cfg.db_path) as store:
        library = LibraryService(store)
        chat = ChatService(library, session)
        try:
            model = parse_model_id(args.model) if args.model else None
            if args.conversation is not None:
                conv = library.get_conversation(args.conversation)
                model = model or conv.summary.model
            else:
                conv = None
            if model is None:
                models, _availability = registry.list_models()
                available = [m for m in models if m.available]
                if len(available) == 1:
                    model = available[0].ref
                else:
                    choices = ", ".join(m.ref.id for m in available) or "none available"
                    raise EngineError("no_model", f"Choose --model backend/name. Models: {choices}")
            # Validate and load before creating an empty saved conversation.
            chat.catalog_load(model)
            if conv is None:
                conv = library.create_conversation(model=model)
            elif model != conv.summary.model:
                chat.set_model(conv.summary.id, model)
            cid = conv.summary.id
            print(f"Conversation {cid} · {model.id}. /exit to quit; Ctrl+C stops a response.")
            for turn in conv.messages:
                print(f"{turn.role}: {turn.content}")
            errors = []
            chat.on_token = lambda _cid, token: print(token, end="", flush=True)
            chat.on_error = lambda _cid, error: errors.append(error)
            while True:
                try:
                    prompt = input("You: ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if prompt.strip() in {"/exit", "/quit"}:
                    break
                if not prompt.strip():
                    continue
                errors.clear()
                print("Assistant: ", end="", flush=True)
                chat.send(cid, prompt, GenerationParams(max_tokens=args.max_tokens))
                try:
                    while chat._worker_thread.is_alive():
                        chat._worker_thread.join(0.1)
                except KeyboardInterrupt:
                    chat.cancel_current()
                    chat._worker_thread.join(5)
                    if chat._worker_thread.is_alive():
                        raise EngineError("cancelled", "Model is still stopping. Exit and retry.")
                print()
                if errors:
                    print(f"error: {errors[-1]}", file=sys.stderr)
        finally:
            chat.cancel_current()
            if chat._worker_thread is not None:
                chat._worker_thread.join(5)
            registry.close()
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    registry: BackendRegistry | None = None
    try:
        cfg = config.load(args.config, args.db)
        registry = BackendRegistry(cfg=cfg)
        models, availability = registry.list_models()
    except EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if registry is not None:
            registry.close()
    for name, (ok, reason) in availability.items():
        if not ok:
            print(f"{name}: unavailable — {reason or 'unavailable'}", file=sys.stderr)
    for model in models:
        print(f"{model.ref.id}\t{model.size_bytes}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command is None:
        parser.print_help()
        return 0

    for key, default in (("config", None), ("db", None), ("verbose", False)):
        vars(args).setdefault(key, default)
    try:
        cfg = config.load(args.config, args.db)
        if cfg.db_path.parent == config.app_data_dir():
            # Chats and notes stay private to this account, including older installs.
            config.secure_app_data()
        setup_logging(verbose=args.verbose, log_path=cfg.db_path.parent / "logs" / "engine.log")
        return {
            "health": _cmd_health,
            "models": _cmd_models,
            "migrate": _cmd_migrate,
            "chat": _cmd_chat,
            "serve": _cmd_serve,
        }[args.command](args)
    except (EngineError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
