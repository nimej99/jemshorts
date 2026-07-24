"""레퍼런스 리서치(parse_vtt/analyze_reference) 테스트 — 실 네트워크 없음.

VTT 파싱(타임스탬프, 태그 제거, 롤링 중복 제거), 훅 구간 추출,
페이싱 통계, yt-dlp 부재 시 명시적 실패를 검증한다.
"""

import pytest

from app.promo.research import (
    ResearchToolMissingError,
    analyze_reference,
    fetch_subtitles,
    parse_vtt,
)

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: ko

00:00:00.320 --> 00:00:02.100 align:start position:0%
드디어 <c>나왔다</c> 신메뉴

00:00:02.100 --> 00:00:04.500
드디어 나왔다 신메뉴

00:00:04.500 --> 00:00:08.900
매콤하고 담백한 맛이
일품입니다

00:00:08.900 --> 00:00:12.000
이번 주말까지 이벤트
"""


def test_parse_vtt_extracts_cues_and_strips_tags():
    cues = parse_vtt(SAMPLE_VTT)

    assert [c.text for c in cues] == [
        "드디어 나왔다 신메뉴",
        "매콤하고 담백한 맛이 일품입니다",
        "이번 주말까지 이벤트",
    ]
    assert cues[0].start_s == pytest.approx(0.32)
    assert cues[1].end_s == pytest.approx(8.9)


def test_parse_vtt_dedupes_rolling_duplicates():
    cues = parse_vtt(SAMPLE_VTT)
    # 00:00:02.100 큐는 직전 큐와 동일 텍스트(롤링 자막) — 제거되어야 한다.
    assert len(cues) == 3


def test_parse_vtt_empty_input():
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\n\nNOTE nothing here\n") == []


def test_analyze_reference_hook_and_pacing():
    stats = analyze_reference(parse_vtt(SAMPLE_VTT))

    assert stats.duration_s == pytest.approx(12.0)
    # 훅(첫 3초 시작 큐)만 포함 — 4.5초 시작 큐는 제외.
    assert stats.hook_text == "드디어 나왔다 신메뉴"
    assert stats.cue_count == 3
    assert stats.total_chars == sum(
        len(t.replace(" ", ""))
        for t in ["드디어 나왔다 신메뉴", "매콤하고 담백한 맛이 일품입니다", "이번 주말까지 이벤트"]
    )
    assert stats.chars_per_sec == pytest.approx(stats.total_chars / 12.0, abs=0.01)


def test_analyze_reference_rejects_empty():
    with pytest.raises(ValueError, match="비어"):
        analyze_reference([])


def test_fetch_subtitles_missing_ytdlp(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(ResearchToolMissingError, match="yt-dlp"):
        fetch_subtitles("https://example.com/watch?v=x")
