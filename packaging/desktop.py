"""Frozen entry point: divert spawned model workers before importing Qt."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()

    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        from llm_manager_app.smoke_test import main
    else:
        from llm_manager_app.__main__ import main

    raise SystemExit(main())
