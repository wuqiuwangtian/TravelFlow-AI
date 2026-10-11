"""详细度档位与篇幅公式测试。"""

from __future__ import annotations

import pytest

from app.agents.detail_levels import (
    detail_name,
    detail_requirements,
    estimate_length,
    target_length_text,
)
from app.domain.enums import DetailLevel


def test_length_grows_clearly_with_duration() -> None:
    """用户明确要求：一日游与一周行程的篇幅应有明显差距。"""
    for level in DetailLevel:
        one_day = estimate_length(level, 1)
        seven_days = estimate_length(level, 7)
        assert seven_days > one_day * 2.5, f"{level} 的篇幅未随天数显著增长"


@pytest.mark.parametrize(
    ("level", "days", "lo", "hi"),
    [
        (DetailLevel.L1_MINIMAL, 1, 300, 400),
        (DetailLevel.L2_STANDARD, 3, 1400, 1500),
        (DetailLevel.L3_DETAILED, 7, 4700, 4900),
        (DetailLevel.L4_BUTLER, 3, 3800, 4000),
    ],
)
def test_expected_lengths(level: DetailLevel, days: int, lo: int, hi: int) -> None:
    assert lo <= estimate_length(level, days) <= hi


def test_higher_level_is_longer() -> None:
    minimal = estimate_length(DetailLevel.L1_MINIMAL, 3)
    butler = estimate_length(DetailLevel.L4_BUTLER, 3)

    assert butler > minimal * 3


def test_none_duration_falls_back_to_one_day() -> None:
    assert estimate_length(DetailLevel.L2_STANDARD, None) == estimate_length(
        DetailLevel.L2_STANDARD, 1
    )


def test_target_length_text_is_a_range() -> None:
    text = target_length_text(DetailLevel.L2_STANDARD, 3)

    low, high = text.split("~")
    assert int(low) < int(high)


def test_every_level_has_name_and_requirements() -> None:
    for level in DetailLevel:
        assert detail_name(level)
        assert len(detail_requirements(level)) > 10
