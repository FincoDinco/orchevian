from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from llm_engine import config
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--db", type=Path, default=None, help="Path to data.db")
    shared.add_argument("--config", type=Path, default=None, help="Path to config.json")
    shared.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")

    parser = argparse.ArgumentParser(
        prog="orchevian-engine",
        description="Orchevian engine CLI",
        parents=[shared],
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", parents=[shared], help="Interactive chat (TTY)")
    sub.add_parser("models", parents=[shared], help="List local models")
    serve = sub.add_parser("serve", parents=[shared], help="OpenAI-compatible HTTP API")
    serve.add_argument("--port", type=int, default=None, help="Bind port (host is 127.0.0.1)")
    sub.add_parser("migrate", parents=[shared], help="Migrate the SQLite database")
    sub.add_parser("health", parents=[shared], help="Print session, db, and API status")
    return parser


def _unimplemented(command: str) -> int:
    print(f"orchevian-engine {command}: not implemented", file=sys.stderr)
    return 1


def _cmd_health(args: argparse.Namespace) -> int:
    cfg = config.load(args.config, args.db)
    print(f"db: {cfg.db_path}")
    print(f"config: {cfg.config_path}")
    print(f"model_dir: {cfg.model_dir}")
    print(f"api: {cfg.api_host}:{cfg.api_port} (stopped)")
    print("loaded: none")
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

    if args.command == "health":
        setup_logging(verbose=args.verbose)
        return _cmd_health(args)
    if args.command == "models":
        setup_logging(verbose=args.verbose)
        return _cmd_models(args)
    return _unimplemented(args.command)


if __name__ == "__main__":
    sys.exit(main())
