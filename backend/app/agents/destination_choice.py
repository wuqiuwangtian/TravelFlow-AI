"""Resolve an unambiguous short reply against the saved city list, without guessing."""

from __future__ import annotations

import re
import unicodedata

_PREFIX = (
    r"(?:那就选|那就去|那就|就选|就去|就|我想选|我就选|我要去|"
    r"我选|我选择|选择|选|只选|我想去|我去|决定去|去|定)?"
)
_SUFFIX = r"(?:吧|好了|就好|就行)?[。.!！]*"
_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def destination_choice(text: str, cities: list[str]) -> tuple[str | None, bool]:
    """Return (chosen city, invalid ordinal). Questions and compound input go to the model."""
    text = _compact(text)
    ordinal = re.fullmatch(
        _PREFIX + r"第?([0-9]+|[零一二两三四五六七八九十])(?:个城市|座城市|个|项|号|座)?" + _SUFFIX,
        text,
    )
    if ordinal:
        value = ordinal[1]
        index = int(value) if value.isascii() and value.isdecimal() else _NUMBERS[value]
        return (cities[index - 1].strip(), False) if 1 <= index <= len(cities) else (None, True)
    matches = {
        city.strip()
        for city in cities
        if city.strip() and re.fullmatch(_PREFIX + re.escape(_compact(city)) + _SUFFIX, text)
    }
    return (matches.pop(), False) if len(matches) == 1 else (None, False)
