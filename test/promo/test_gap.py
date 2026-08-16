"""키워드 갭 스코어링(app.promo.research.gap) 테스트.

yt-dlp subprocess 는 전부 mock 처리한다 (실 네트워크 0).
"""

import json

import pytest

from app.promo.research import gap
from app.promo.research.ingest import ResearchToolMissingError


class _Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _flat_payload(views_list):
    return {
        "entries": [
            {"ie_key": "Youtube", "view_count": views, "id": f"v{idx}"}
            for idx, views in enumerate(views_list)
        ]
    }


@pytest.fixture()
def mock_ytdlp(monkeypatch):
    """yt-dlp 존재를 가정하고, 준비된 payload 를 -J 출력으로 돌려준다."""
    monkeypatch.setattr(gap.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")

    def install(payload):
        def fake_run(cmd, **kwargs):
            assert cmd[-1].startswith("ytsearch")
            return _Completed(stdout=json.dumps(payload))

        monkeypatch.setattr(gap.subprocess, "run", fake_run)

    return install


# ---------------------------------------------------------------------------
# gap_score
# ---------------------------------------------------------------------------


def test_gap_score_zero_demand():
    assert gap.gap_score(0, 100) == 0.0
    assert gap.gap_score(-5, 100) == 0.0


def test_gap_score_formula():
    assert gap.gap_score(100, 0) == 100.0  # 100 / (1 + 0)
    assert gap.gap_score(11, 100) == 1.0  # 11 / (1 + 10)
    assert gap.gap_score(22, 100) == 2.0  # 수요 2배 -> 점수 2배


def test_gap_score_negative_views_clamped():
    assert gap.gap_score(100, -50) == 100.0


# ---------------------------------------------------------------------------
# youtube_supply (yt-dlp mock)
# ---------------------------------------------------------------------------


def test_youtube_supply_parses_flat_json(mock_ytdlp):
    mock_ytdlp(_flat_payload([1000, 500, 0]))

    count, total, top = gap.youtube_supply("무선 선풍기")

    assert (count, total, top) == (3, 1500, 1000)


def test_youtube_supply_filters_non_video_entries(mock_ytdlp):
    payload = _flat_payload([1000])
    payload["entries"].append({"ie_key": "YoutubeTab", "view_count": 999999})
    mock_ytdlp(payload)

    count, total, top = gap.youtube_supply("무선 선풍기")

    assert (count, total, top) == (1, 1000, 1000)


def test_youtube_supply_missing_views_counts_zero(mock_ytdlp):
    mock_ytdlp({"entries": [{"ie_key": "Youtube", "id": "v1"}]})

    count, total, top = gap.youtube_supply("무선 선풍기")

    assert (count, total, top) == (1, 0, 0)


def test_youtube_supply_missing_tool(monkeypatch):
    monkeypatch.setattr(gap.shutil, "which", lambda name: None)
    with pytest.raises(ResearchToolMissingError):
        gap.youtube_supply("무선 선풍기")


def test_youtube_supply_failure_raises(monkeypatch):
    monkeypatch.setattr(gap.shutil, "which", lambda name: "/bin/yt-dlp")
    monkeypatch.setattr(
        gap.subprocess,
        "run",
        lambda cmd, **kwargs: _Completed(stderr="boom", returncode=1),
    )
    with pytest.raises(gap.GapFetchError):
        gap.youtube_supply("무선 선풍기")


def test_youtube_supply_bad_json_raises(monkeypatch):
    monkeypatch.setattr(gap.shutil, "which", lambda name: "/bin/yt-dlp")
    monkeypatch.setattr(
        gap.subprocess,
        "run",
        lambda cmd, **kwargs: _Completed(stdout="not json"),
    )
    with pytest.raises(gap.GapFetchError):
        gap.youtube_supply("무선 선풍기")


# ---------------------------------------------------------------------------
# rank_keywords
# ---------------------------------------------------------------------------


def test_rank_keywords_orders_by_score(mock_ytdlp):
    mock_ytdlp(_flat_payload([100]))

    results = gap.rank_keywords(["a", "b"], demand_map={"a": 11, "b": 110})

    assert [r.keyword for r in results] == ["b", "a"]
    assert results[0].score == 10.0  # 110 / (1 + 10)
    assert results[0].demand == 110.0


def test_rank_keywords_demand_defaults_to_one(mock_ytdlp):
    mock_ytdlp(_flat_payload([0]))

    results = gap.rank_keywords(["a"])

    assert results[0].demand == 1.0
    assert results[0].score == 1.0


def test_rank_keywords_skips_failed_keyword(monkeypatch):
    monkeypatch.setattr(gap.shutil, "which", lambda name: "/bin/yt-dlp")

    def fake_run(cmd, **kwargs):
        if "bad" in cmd[-1]:
            return _Completed(stderr="boom", returncode=1)
        return _Completed(stdout=json.dumps(_flat_payload([10])))

    monkeypatch.setattr(gap.subprocess, "run", fake_run)

    results = gap.rank_keywords(["good", "bad"])

    assert [r.keyword for r in results] == ["good"]


def test_rank_keywords_result_to_dict(mock_ytdlp):
    mock_ytdlp(_flat_payload([81]))

    result = gap.rank_keywords(["a"], demand_map={"a": 10})[0].to_dict()

    assert result == {
        "keyword": "a",
        "demand": 10.0,
        "result_count": 1,
        "top_view_sum": 81,
        "top_view_max": 81,
        "score": 1.0,  # 10 / (1 + 9)
    }
