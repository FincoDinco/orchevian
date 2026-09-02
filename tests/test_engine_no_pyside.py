from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
import tomllib
from pathlib import Path

import llm_engine

ROOT = Path(__file__).resolve().parents[1]
BANNED_ROOTS = frozenset({"PySide6", "PyQt6", "pyqt_liquidglass"})


def _top_level(name: str) -> str:
    return name.split(".")[0]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(_top_level(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(_top_level(node.module))
    return roots


def test_engine_sources_do_not_import_qt() -> None:
    src = ROOT / "src" / "llm_engine"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        for name in sorted(_imported_roots(path) & BANNED_ROOTS):
            offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert offenders == []


def test_pyproject_core_deps_exclude_pyside() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(data["project"].get("dependencies", []))
    extras = data["project"].get("optional-dependencies", {})
    for name in ("mlx", "gguf"):
        deps.extend(extras.get(name, []))
    joined = "\n".join(deps).lower()
    assert "pyside" not in joined
    assert "pyqt" not in joined
    assert "pyqt-liquidglass" not in joined


def test_importing_llm_engine_does_not_load_pyside6() -> None:
    for module in list(sys.modules):
        if module == "PySide6" or module.startswith("PySide6."):
            del sys.modules[module]

    importlib.reload(llm_engine)
    for info in pkgutil.walk_packages(llm_engine.__path__, llm_engine.__name__ + "."):
        leaf = info.name.rsplit(".", 1)[-1]
        if not leaf.isidentifier():
            continue
        importlib.import_module(info.name)

    loaded = [name for name in sys.modules if name == "PySide6" or name.startswith("PySide6.")]
    assert loaded == []
    assert "pyqt_liquidglass" not in sys.modules
