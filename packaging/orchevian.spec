"""Ollama-first desktop bundle. Build on each target OS in a clean environment."""

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
pdfium_data, pdfium_binaries, pdfium_imports = collect_all("pypdfium2_raw")

a = Analysis(
    [str(root / "packaging" / "desktop.py")],
    pathex=[str(root / "src")],
    binaries=pdfium_binaries,
    datas=[
        (str(root / "src/llm_engine/store/schema.sql"), "llm_engine/store"),
        # The migration loader executes these files by path, outside the module archive.
        (str(root / "src/llm_engine/store/migrations/*.py"), "llm_engine/store/migrations"),
        (str(root / "src/llm_manager_app/assets/*.svg"), "llm_manager_app/assets"),
        # GPL-3.0: distributed copies carry the license text.
        (str(root / "LICENSE"), "."),
    ] + collect_data_files("markdown") + collect_data_files("pptx")
      # python-pptx resolves notes templates through oxml/../templates. The oxml
      # directory must exist on disk even though its modules are in the archive.
      + collect_data_files("pptx", include_py_files=True, includes=["oxml/__init__.py"])
      + collect_data_files("reportlab") + pdfium_data,
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("markdown.extensions")
                  + pdfium_imports,
    # Optional native inference stacks need separate platform-specific packaging work.
    excludes=["mlx", "mlx_lm", "llama_cpp", "torch", "transformers", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Orchevian",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
collection = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, name="Orchevian",
)
if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="Orchevian.app",
        bundle_identifier="com.orchevian.desktop",
        version=version,
        info_plist={
            "CFBundleDisplayName": "Orchevian",
            "CFBundleShortVersionString": version,
            "NSHighResolutionCapable": True,
        },
    )
