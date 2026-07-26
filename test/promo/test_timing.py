"""내레이션 실측 타이밍(app.promo.timing) + 타임라인 게이트 테스트.

실측 함수는 전부 주입한다 — 실제 TTS/네트워크 호출 없음.
"""

from datetime import timedelta
from pathlib import Path

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
    cues_from_sub_maker,
    measure_narration,
    narrate_full_script,
    sections_from_cues,
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


# --- 전체 스크립트 1회 TTS 경로 (b-3) ---------------------------------------


class _FakeCue:
    def __init__(self, start: float, end: float, text: str):
        self.start = timedelta(seconds=start)
        self.end = timedelta(seconds=end)
        self.content = text


class _FakeSubMaker:
    """edge-tts 7.x 스타일 cues 를 갖는 가짜 sub_maker."""

    def __init__(self, cues):
        self.cues = cues


class _LegacySubMaker:
    """구형 offset/subs 구조 (코어의 다른 TTS 경로)."""

    def __init__(self, pairs):
        self.offset = [(int(s * 1e7), int(e * 1e7)) for s, e, _ in pairs]
        self.subs = [text for _, _, text in pairs]


def _cues_for_script():
    """SCRIPT 의 5문장에 대응하는 큐 (총 18초)."""
    sentences = split_sentences(SCRIPT)
    spans = [(0.0, 2.6), (2.6, 7.0), (7.0, 11.2), (11.2, 14.4), (14.4, 18.0)]
    return [_FakeCue(start, end, text) for (start, end), text in zip(spans, sentences)]


def test_cues_from_sub_maker_reads_modern_and_legacy(template):
    modern = _FakeSubMaker(_cues_for_script())
    legacy = _LegacySubMaker([(0.0, 1.0, "가"), (1.0, 2.5, "나")])

    assert cues_from_sub_maker(modern)[0][:2] == (0.0, 2.6)
    assert cues_from_sub_maker(legacy) == [(0.0, 1.0, "가"), (1.0, 2.5, "나")]


def test_cues_from_sub_maker_reads_content_and_unescapes():
    """edge-tts 7.x cue 는 `.content`(XML 이스케이프) — `.text` 가 아니다.

    실측 경계는 글자수 비율로 끊으므로 이스케이프를 풀어 실제 길이를 쓴다.
    (e2e 가 잡아낸 회귀: cue.text 를 읽어 AttributeError)
    """
    cues = [_FakeCue(0.0, 1.0, "매운&amp;떡볶이"), _FakeCue(1.0, 2.0, "출시!")]

    result = cues_from_sub_maker(_FakeSubMaker(cues))

    assert result[0][2] == "매운&떡볶이"
    assert result[1][2] == "출시!"


def test_sections_from_cues_ends_exactly_at_audio_end(template):
    """실측 합계가 실제 오디오 길이와 정확히 같아야 렌더 오디오와 어긋나지 않는다."""
    cues = cues_from_sub_maker(_FakeSubMaker(_cues_for_script()))

    narration = sections_from_cues(SCRIPT, template, cues)

    assert narration.total_s == 18.0
    assert narration.sections[0].start_s == 0.0
    assert narration.sections[-1].end_s == 18.0
    # 경계는 항상 큐 끝에 맞는다 (문장 중간에서 화면이 끊기지 않는다).
    cue_ends = {round(end, 3) for _, end, _ in cues}
    assert all(section.end_s in cue_ends for section in narration.sections)


def test_sections_from_cues_rejects_empty_cues(template):
    with pytest.raises(TimingError, match="자막 큐"):
        sections_from_cues(SCRIPT, template, [])


def test_narrate_full_script_synthesizes_once_and_returns_audio(tmp_path, template):
    """TTS 는 한 번만 호출되고, 그 오디오 핸들이 렌더 재사용용으로 돌아온다."""
    calls = []

    def fake_synthesize(*, text, voice_name, voice_rate, voice_file):
        calls.append({"text": text, "voice": voice_name, "rate": voice_rate})
        Path(voice_file).write_bytes(b"fake-audio")
        return _FakeSubMaker(_cues_for_script())

    narration, audio = narrate_full_script(
        SCRIPT,
        template,
        voice_name="ko-KR-SunHiNeural-Female",
        voice_rate=1.1,
        audio_dir=tmp_path / "narration",
        synthesize=fake_synthesize,
    )

    assert len(calls) == 1  # 섹션 수만큼 부르지 않는다
    assert calls[0]["rate"] == 1.1
    assert narration.total_s == 18.0
    assert audio.duration_s == narration.total_s
    assert Path(audio.audio_file).is_file()
    assert audio.sub_maker is not None


def test_narrate_full_script_fails_loudly_when_tts_returns_none(tmp_path, template):
    with pytest.raises(TimingError, match="TTS 합성"):
        narrate_full_script(
            SCRIPT,
            template,
            voice_name="ko-KR-SunHiNeural-Female",
            audio_dir=tmp_path / "narration",
            synthesize=lambda **kwargs: None,
        )


def test_sections_from_cues_rejects_fewer_cues_than_sections(template):
    """큐가 섹션보다 적으면 경계가 겹친다 — 조용히 0초 섹션을 만들지 않는다."""
    cues = [(0.0, 5.0, "한 덩어리로 합성된 문장"), (5.0, 12.0, "두 번째 덩어리")]

    with pytest.raises(TimingError, match="자막 큐 2개"):
        sections_from_cues(SCRIPT, template, cues)
