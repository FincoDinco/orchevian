"""Bounded, strict specifications shared by model tools and generators."""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(max_length=8000)]
Short = Annotated[str, Field(max_length=120)]
Scalar = str | int | float | bool | None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Block(Strict):
    type: Literal["paragraph", "heading", "list", "table", "page_break"] = "paragraph"
    text: Text = ""
    level: Annotated[int, Field(ge=1, le=3)] = 1
    items: Annotated[list[Text], Field(max_length=50)] = []
    rows: Annotated[list[list[Scalar]], Field(max_length=100)] = []

    @model_validator(mode="after")
    def table_bounds(self):
        if any(len(row) > 12 for row in self.rows):
            raise ValueError("Document tables allow at most 12 columns")
        return self


class Sheet(Strict):
    name: Annotated[str, Field(min_length=1, max_length=31)] = "Sheet1"
    rows: Annotated[list[list[Scalar]], Field(min_length=1, max_length=1000)]
    number_formats: dict[str, Short] = {}
    chart: Literal["bar", "line", "none"] = "none"

    @model_validator(mode="after")
    def bounds(self):
        if re.search(r"[\\/*?:\[\]]", self.name) or self.name.startswith("'"):
            raise ValueError("Invalid spreadsheet sheet name")
        if any(len(row) > 50 for row in self.rows):
            raise ValueError("Sheets allow at most 50 columns")
        for cell in self.number_formats:
            if not re.fullmatch(r"[A-Z]{1,2}[1-9][0-9]{0,2}|[A-Z]{1,2}1000", cell):
                raise ValueError("Number formats require a cell address within the sheet")
        if self.chart != "none" and (len(self.rows) < 2 or len(self.rows[0]) < 2):
            raise ValueError("Charts require headers and at least one data row")
        return self


class Slide(Strict):
    title: Short
    bullets: Annotated[list[Annotated[str, Field(max_length=220)]], Field(max_length=6)] = []
    notes: Text = ""

    @model_validator(mode="after")
    def text_fits(self):
        if sum(map(len, self.bullets)) > 900:
            raise ValueError("A slide allows at most 900 bullet characters; split it into slides")
        return self


class FormField(Strict):
    name: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")]
    label: Short
    type: Literal["text", "checkbox"] = "text"


class Chart(Strict):
    labels: Annotated[
        list[Annotated[str, Field(max_length=32)]], Field(min_length=1, max_length=20)
    ]
    values: Annotated[list[float | int], Field(min_length=1, max_length=20)]
    type: Literal["bar", "line"] = "bar"

    @model_validator(mode="after")
    def lengths(self):
        if len(self.labels) != len(self.values) or any(not math.isfinite(v) for v in self.values):
            raise ValueError("Chart labels and finite values must have matching lengths")
        return self


class Spec(Strict):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    format: Literal[
        "docx",
        "pdf",
        "rtf",
        "txt",
        "md",
        "xlsx",
        "csv",
        "tsv",
        "pptx",
        "html",
        "json",
        "yaml",
        "xml",
        "svg",
        "png",
    ]
    kind: Literal["document", "spreadsheet", "slides", "form", "data", "chart", "diagram"]
    title: Short = ""
    blocks: Annotated[list[Block], Field(max_length=150)] = []
    sheets: Annotated[list[Sheet], Field(max_length=10)] = []
    slides: Annotated[list[Slide], Field(max_length=30)] = []
    fields: Annotated[list[FormField], Field(max_length=60)] = []
    data: dict | list | None = None
    chart: Chart | None = None
    steps: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=10)] = []

    @model_validator(mode="after")
    def content(self):
        if (
            self.name in {".", ".."}
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', self.name)
            or self.name.endswith((" ", "."))
            or self.name.startswith(".")
            or self.name.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
        ):
            raise ValueError("Use a plain portable filename without a path")
        required = {
            "document": self.blocks,
            "spreadsheet": self.sheets,
            "slides": self.slides,
            "form": self.fields,
            "data": self.data is not None,
            "chart": self.chart,
            "diagram": self.steps,
        }
        if not required[self.kind]:
            raise ValueError(f"Missing content for {self.kind}")
        for kind, value in required.items():
            if kind != self.kind and value:
                raise ValueError(f"{self.kind} specifications cannot contain {kind} content")
        for values in ([s.name.casefold() for s in self.sheets], [f.name for f in self.fields]):
            if len(set(values)) != len(values):
                raise ValueError("Sheet and field names must be unique")
        return self

    @property
    def filename(self):
        suffix = "." + self.format
        return self.name if self.name.lower().endswith(suffix) else self.name + suffix


class ToolCall(Strict):
    tool: Literal["create_documents"]
    files: Annotated[list[Spec], Field(min_length=1, max_length=8)]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_call(text):
    def decode(value):
        return json.loads(
            value, object_pairs_hook=_unique_object,
            parse_constant=lambda v: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON value: {v}"),
            ),
        )

    try:
        return decode(text)
    except json.JSONDecodeError as original:
        # A complete files array followed by a missing outer } is a recurrent
        # local-model error. Complete only that envelope, never file content,
        # strings, commas, nested delimiters, or partially emitted arrays.
        if (original.pos != len(text) or not text.startswith("{")
                or not text.endswith("]") or len(text) >= 120_000):
            raise
        try:
            return decode(text + "}")
        except json.JSONDecodeError:
            raise original from None


def parse_call(text: str) -> ToolCall:
    if len(text) > 120_000:
        raise ValueError("Document specification exceeds 120,000 characters")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # No prose extraction or content guessing: schema validation still requires
    # one complete, unambiguous call after the narrow envelope completion above.
    value = _load_call(text.strip())
    call = ToolCall.model_validate(value)
    names = [spec.filename.casefold() for spec in call.files]
    if len(set(names)) != len(names):
        raise ValueError("Every output in a request needs a unique filename")
    return call
