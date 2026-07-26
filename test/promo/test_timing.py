"""내레이션 실측 타이밍(app.promo.timing) + 타임라인 게이트 테스트.

실측 함수는 전부 주입한다 — 실제 TTS/네트워크 호출 없음.
"""

import pytest

from app.promo.quality import (
    CODE_SECTION_DURATION_DRIFT,
    CODE_TIMELINE_COVERAGE_MISMATCH,
    timeline_gate,
)
from app.promo.templates.schema import validate_template
from app.promo.timing import (
    TimingError,
    allocate_sentences,
    measure_narration,
    split_sentences,
)

TEMPLATE_DATA = {
    "template_id": "timing-test-v2",
    "name": "타이밍 테스트",
    "version": 2,
    "mood": "upbeat",
    "timing": {"owner": "narration", "tolerance_s": 0.2},
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "훅",
            "material_slot": "video",
        },
        {
            "role": "body",
            "duration_s": 12,
            "script_guide": "본문",
            "material_slot": "any",
        },
        {
            "role": "cta",
            "duration_s": 5,
            "script_guide": "행동 유도",
            "material_slot": "photo",
        },
    ],
    "total_duration_range": [15, 30],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}

SCRIPT = (
    "드디어 나왔다, 우리 가게 신메뉴! "
    "매콤한 양념에 담백한 육수가 어우러집니다. "
    "직접 우려낸 육수로 매일 아침 준비합니다. "
    "가격은 만이천원, 점심에도 부담 없습니다. "
    "이번 주말까지 시식 이벤트, 지금 방문하세요!"
)


@pytest.fixture()
def template():
    return validate_template(TEMPLATE_DATA)


def _fixed_measure(seconds_by_index):
    """호출 순서대로 정해진 초를 돌려주는 실측 함수."""
    calls = iter(seconds_by_index)

    def _measure(text: str) -> float:
        assert text.strip(), "빈 텍스트를 실측하려 했습니다"
        return next(calls)

    return _measure


# --- 문장 분리 / 배분 ---------------------------------------------------------


def test_split_sentences_handles_terminators_and_newlines():
    assert split_sentences("첫 문장! 둘째 문장?\n셋째 문장.") == [
        "첫 문장!",
        "둘째 문장?",
        "셋째 문장.",
    ]


def test_split_sentences_ignores_blank_input():
    assert split_sentences("   \n  ") == []


def test_allocate_sentences_gives_every_section_at_least_one(template):
    buckets = allocate_sentences(split_sentences(SCRIPT), template)

    assert len(buckets) == 3
    assert all(bucket for bucket in buckets)
    # 원문 순서와 내용이 보존된다 (승인한 스크립트 = 렌더되는 스크립트).
    assert [s for bucket in buckets for s in bucket] == split_sentences(SCRIPT)


def test_allocate_sentences_follows_duration_ratio(template):
    """body(12초)가 hook(3초)보다 많은 문장을 가져간다."""
    buckets = allocate_sentences(split_sentences(SCRIPT), template)

    assert len(buckets[1]) >= len(buckets[0])


def test_allocate_sentences_rejects_fewer_sentences_than_sections(template):
    with pytest.raises(TimingError, match="문장 수"):
        allocate_sentences(["한 문장뿐입니다."], template)


# --- 실측 ---------------------------------------------------------------------


def test_measure_narration_builds_cumulative_boundaries(template):
    narration = measure_narration(SCRIPT, template, _fixed_measure([2.5, 11.0, 4.5]))

    assert narration.total_s == 18.0
    assert narration.boundaries == ((0.0, 2.5), (2.5, 13.5), (13.5, 18.0))
    assert [s.role for s in narration.sections] == ["hook", "body", "cta"]
    assert narration.sections[0].drift_s == -0.5


def test_measure_narration_rejects_empty_script(template):
    with pytest.raises(TimingError, match="비어"):
        measure_narration("   ", template, _fixed_measure([1.0]))


def test_measure_narration_rejects_zero_measurement(template):
    with pytest.raises(TimingError, match="0 이하"):
        measure_narration(SCRIPT, template, _fixed_measure([0.0]))


# --- 타임라인 게이트 -----------------------------------------------------------


def test_timeline_gate_passes_within_range(template):
    narration = measure_narration(SCRIPT, template, _fixed_measure([3.0, 12.0, 5.0]))

    result = timeline_gate(narration, template)

    assert result.passed is True
    assert result.failures == []
    assert result.warnings == []


def test_timeline_gate_fails_when_total_exceeds_range(template):
    """31초 실측은 total_duration_range [15, 30] ±0.2초를 벗어난다."""
    narration = measure_narration(SCRIPT, template, _fixed_measure([4.0, 20.0, 7.0]))

    result = timeline_gate(narration, template)

    assert result.passed is False
    assert any(f.startswith(CODE_TIMELINE_COVERAGE_MISMATCH) for f in result.failures)
    assert any("31초" in f for f in result.failures)


def test_timeline_gate_fails_when_total_below_range(template):
    narration = measure_narration(SCRIPT, template, _fixed_measure([1.0, 5.0, 2.0]))

    result = timeline_gate(narration, template)

    assert result.passed is False
    assert any(f.startswith(CODE_TIMELINE_COVERAGE_MISMATCH) for f in result.failures)


def test_timeline_gate_warns_on_section_drift_without_failing(template):
    """섹션 편차는 경고일 뿐 — 실측이 타임라인의 주인이다."""
    narration = measure_narration(SCRIPT, template, _fixed_measure([8.0, 6.0, 5.0]))

    result = timeline_gate(narration, template)

    assert result.passed is True
    assert any(w.startswith(CODE_SECTION_DURATION_DRIFT) for w in result.warnings)
    assert any("hook" in w and "+5초" in w for w in result.warnings)


def test_timeline_gate_uses_default_tolerance_without_timing_declaration():
    """timing 선언이 없어도 게이트는 기본 허용오차로 동작한다 (직접 호출 시)."""
    data = {key: value for key, value in TEMPLATE_DATA.items() if key != "timing"}
    template = validate_template(data)
    narration = measure_narration(SCRIPT, template, _fixed_measure([3.0, 12.0, 5.0]))

    assert timeline_gate(narration, template).passed is True
