from datetime import date
from pathlib import Path
from runpy import run_path

import pytest

mentions_date = run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "check_web_answer.py")
)["mentions_date"]


@pytest.mark.parametrize("answer", [
    "On September 16, 2026, it changed.",
    "On Sep. 16, 2026, it changed.",
    "On 16 September 2026, it changed.",
    "On 2026-09-16 it changed.",
])
def test_live_check_accepts_event_dates_in_the_visible_answer(answer):
    assert mentions_date(answer, date(2026, 9, 16))


@pytest.mark.parametrize("answer", [
    "It changed. [Source](https://example.com/2026-09-16)",
    "It changed. https://example.com/2026-09-16",
    "On September 17, 2026, it changed.",
    "On September 16, 2025, it changed.",
    "The event date is unclear from these sources.",
])
def test_live_check_does_not_pass_missing_or_wrong_dates(answer):
    assert not mentions_date(answer, date(2026, 9, 16))
