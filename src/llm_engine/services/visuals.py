"""Bounded image decoding, PDF rendering and offline OCR, only in parser workers."""

from __future__ import annotations

import ctypes
import ctypes.util
import io
import math
import os
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from llm_engine.domain.images import (  # noqa: F401
    MAX_IMAGE_BYTES,
    MAX_TURN_IMAGES,
    validate_image_inputs,
)

IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PIXELS = 25_000_000
MAX_PAGES = 8
MAX_EDGE = 2048
OCR_SETUP = (
    "Local OCR is unavailable. Install Tesseract with English language data "
    "(see README → Pictures and scanned PDFs), then import again. "
    "A compatible Ollama vision model can still read the selected images."
)




@dataclass(frozen=True)
class VisualInfo:
    index: int
    location: str
    width: int
    height: int


@dataclass(frozen=True)
class VisualPage:
    location: str
    data: bytes
    width: int
    height: int


def page_selection(value: str) -> tuple[int, ...] | None:
    """One-based page numbers; None means the first eight available pages."""
    if not value.strip():
        return None
    pages = set()
    try:
        for item in value.split(","):
            bounds = [int(part.strip()) for part in item.split("-")]
            if len(bounds) == 1:
                start = end = bounds[0]
            elif len(bounds) == 2:
                start, end = bounds
            else:
                raise ValueError
            if start < 1 or end < start or end - start >= MAX_PAGES:
                raise ValueError
            pages.update(range(start, end + 1))
            if len(pages) > MAX_PAGES:
                raise ValueError
    except ValueError:
        raise ValueError("Choose up to 8 pages, for example 1-3, 7.") from None
    return tuple(sorted(pages))


def normalize_image(data: bytes):
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as original:
        if original.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("The file is not a PNG, JPEG, or WebP picture.")
        if original.width * original.height > MAX_PIXELS:
            raise ValueError("Pictures are limited to 25 megapixels.")
        if getattr(original, "n_frames", 1) != 1:
            raise ValueError("Animated images are unsupported. Export a single frame first.")
        oriented = ImageOps.exif_transpose(original)
        rgba = oriented.convert("RGBA")
        result = Image.new("RGB", rgba.size, "white")
        result.paste(rgba, mask=rgba.getchannel("A"))
        result.thumbnail((MAX_EDGE, MAX_EDGE))
        return result


def encode_image(image, location):
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88)
    data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("The processed image exceeds the 2 MB limit. Use a smaller image.")
    return VisualPage(location, data, image.width, image.height)


def ocr_text(image):
    """Tesseract C API stays in the killable parser, with no subprocess or temp files.

    https://github.com/tesseract-ocr/tesseract/blob/main/include/tesseract/capi.h
    """
    candidates = [
        os.environ.get("ORCHEVIAN_TESSERACT_LIBRARY"),
        ctypes.util.find_library("tesseract"),
        "/opt/homebrew/lib/libtesseract.dylib",
        "/usr/local/lib/libtesseract.dylib",
    ]
    windows_dir = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR"
    candidates.extend(str(p) for p in windows_dir.glob("*tesseract*.dll"))
    lib = None
    for candidate in filter(None, candidates):
        try:
            lib = ctypes.CDLL(candidate)
            break
        except OSError:
            continue
    if lib is None:
        return "", OCR_SETUP
    pointer = ctypes.c_void_p
    signatures = {
        "TessBaseAPICreate": ([], pointer),
        "TessBaseAPIInit3": ([pointer, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
        "TessBaseAPISetImage": ([pointer, ctypes.c_char_p] + [ctypes.c_int] * 4, None),
        "TessBaseAPIGetUTF8Text": ([pointer], pointer),
        "TessDeleteText": ([pointer], None),
        "TessBaseAPIDelete": ([pointer], None),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    handle = lib.TessBaseAPICreate()
    if not handle:
        return "", OCR_SETUP
    text_pointer = None
    try:
        data_dir = os.environ.get("TESSDATA_PREFIX")
        if not data_dir and (windows_dir / "tessdata").is_dir():
            data_dir = str(windows_dir / "tessdata")
        if lib.TessBaseAPIInit3(handle, os.fsencode(data_dir) if data_dir else None, b"eng"):
            return "", OCR_SETUP
        pixels = image.convert("RGB").tobytes()
        lib.TessBaseAPISetImage(handle, pixels, image.width, image.height, 3, image.width * 3)
        text_pointer = lib.TessBaseAPIGetUTF8Text(handle)
        text = (
            ctypes.string_at(text_pointer).decode("utf-8", errors="replace") if text_pointer else ""
        )
        return text[:200_000], "OCR text may contain mistakes; check the original. English OCR."
    finally:
        if text_pointer:
            lib.TessDeleteText(text_pointer)
        lib.TessBaseAPIDelete(handle)


def read_visuals(data, suffix, pages=None, progress=lambda text: None):
    """Return rendered pages, OCR segments, and visible limitations."""
    if suffix in IMAGE_FORMATS:
        image = normalize_image(data)
        progress("Reading picture with local OCR…")
        text, warning = ocr_text(image)
        return [encode_image(image, "Image 1")], [("Image 1 · OCR", text)], warning
    import pypdfium2 as pdfium

    rendered, segments, warnings = [], [], []
    with closing(pdfium.PdfDocument(data)) as pdf:
        count = len(pdf)
        selected = tuple(range(1, min(count, MAX_PAGES) + 1)) if pages is None else pages
        if not selected or len(selected) > MAX_PAGES or any(p < 1 or p > count for p in selected):
            raise ValueError(f"Choose up to 8 pages between 1 and {count}.")
        if len(selected) < count:
            warnings.append(
                "Visual reading/OCR covers only pages "
                + ", ".join(map(str, selected))
                + f" of {count}; other pages have text extraction only."
            )
        for number in selected:
            progress(f"Reading PDF page {number} of {count}…")
            with closing(pdf[number - 1]) as page:
                width, height = page.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                    raise ValueError("The PDF has invalid page dimensions.")
                scale = min(2, MAX_EDGE / max(width, height))
                with closing(page.render(scale=scale)) as bitmap:
                    image = bitmap.to_pil().convert("RGB")
                rendered.append(encode_image(image, f"Page {number}"))
                with closing(page.get_textpage()) as textpage:
                    has_text = bool(textpage.get_text_range().strip())
                if not has_text:
                    progress(f"Reading page {number} with local OCR…")
                    text, warning = ocr_text(image)
                    segments.append((f"Page {number} · OCR", text))
                    warnings.append(warning)
    return rendered, segments, " ".join(dict.fromkeys(warnings))
