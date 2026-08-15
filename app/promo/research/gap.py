"""키워드 갭 스코어링 — 수요 대비 영상 공급이 부족한 키워드 선별.

커머스 갭 추천 쇼츠(검색량/판매량 많고 영상 적은 상품) 선정 계층.
공급 측정은 yt-dlp 플랫 검색(ytsearchN)으로 실측하고, 수요 신호는
호출자가 제공한다 (Google Trends 트래픽, 네이버 검색량 등).

점수는 절대값이 아니라 랭킹용 휴리스틱이다:

    score = demand / (1 + sqrt(상위 영상 조회수 합))

수요가 2배면 점수 2배, 상위 조회수가 4배면 점수 절반.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass

from loguru import logger

from app.promo.research.ingest import ResearchToolMissingError

FETCH_TIMEOUT_S = 60


class GapFetchError(RuntimeError):
    """유튜브 공급 실측 실패 (도구 실행/파싱)."""


@dataclass(frozen=True)
class GapResult:
    """키워드별 공급 실측 + 갭 점수."""

    keyword: str
    demand: float
    result_count: int
    top_view_sum: int
    top_view_max: int
    score: float

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "demand": self.demand,
            "result_count": self.result_count,
            "top_view_sum": self.top_view_sum,
            "top_view_max": self.top_view_max,
            "score": self.score,
        }


def gap_score(demand: float, top_view_sum: int) -> float:
    """갭 점수 휴리스틱: demand / (1 + sqrt(상위 영상 조회수 합))."""
    if demand <= 0:
        return 0.0
    return round(demand / (1 + math.sqrt(max(0, top_view_sum))), 2)


def _parse_flat_search(payload: dict) -> tuple[int, int, int]:
    """-J 플랫 검색 출력에서 (영상 수, 상위 조회수 합, 최고 조회수) 추출."""
    count = 0
    total = 0
    top = 0
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        # 검색 결과에 채널/플레이리스트가 섞인다 — 영상만 센다
        if entry.get("ie_key") not in (None, "Youtube"):
            continue
        count += 1
        views = entry.get("view_count") or 0
        total += views
        top = max(top, views)
    return count, total, top


def youtube_supply(
    keyword: str, *, limit: int = 10, timeout_s: float = FETCH_TIMEOUT_S
) -> tuple[int, int, int]:
    """키워드의 유튜브 상위 N개 영상 공급을 플랫 검색으로 실측한다.

    반환: (result_count, top_view_sum, top_view_max).
    도구 없음/실행 실패/파싱 실패 시 각각 ResearchToolMissingError /
    GapFetchError 를 던진다.
    """
    ytdlp = shutil.which("yt-dlp")
    if ytdlp is None:
        raise ResearchToolMissingError(
            "yt-dlp 를 찾을 수 없습니다 (설치: uv tool install yt-dlp)"
        )
    cmd = [ytdlp, "--flat-playlist", "-J", f"ytsearch{limit}:{keyword}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        raise GapFetchError(f"yt-dlp 검색 타임아웃 ({keyword})") from exc
    if result.returncode != 0:
        raise GapFetchError(
            f"yt-dlp 검색 실패 ({keyword}): {result.stderr.strip()[:300]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GapFetchError(f"yt-dlp 출력이 JSON 이 아닙니다 ({keyword})") from exc
    return _parse_flat_search(payload)


def rank_keywords(
    keywords: list[str],
    demand_map: dict[str, float] | None = None,
    *,
    limit: int = 10,
) -> list[GapResult]:
    """키워드별 공급 실측 + 수요 결합 후 score 내림차순 정렬.

    - demand_map 에 없는 키워드의 수요는 1 로 폴백 (공급 단독 랭킹).
    - 실측 실패(GapFetchError) 키워드는 결과에서 제외한다 (경고 로그).
      도구 미설치(ResearchToolMissingError)는 그대로 전파한다.
    """
    demand_map = demand_map or {}
    results: list[GapResult] = []
    for keyword in keywords:
        try:
            count, total, top = youtube_supply(keyword, limit=limit)
        except GapFetchError as exc:
            logger.warning(f"갭 실측 건너뜀: {exc}")
            continue
        demand = float(demand_map.get(keyword, 1))
        results.append(
            GapResult(
                keyword=keyword,
                demand=demand,
                result_count=count,
                top_view_sum=total,
                top_view_max=top,
                score=gap_score(demand, total),
            )
        )
    return sorted(results, key=lambda r: r.score, reverse=True)
