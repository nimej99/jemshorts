"""네이버 데이터랩 검색 추이 수요 신호 — 커머스 키워드 선별용.

Google Trends 급상승(뉴스 주도)은 쇼핑 키워드를 거의 커버하지 못하므로,
국내 커머스 갭 선별에는 네이버 검색 추이 비율이 더 적합한 수요 신호다.
네이버 지역검색과 같은 인증을 재사용한다: config `naver_client_id` /
`naver_client_secret` (developers.naver.com 무료 발급, 데이터랩 API 권한 필요).

- 호출당 최대 5개 그룹 — 초과 시 자동 분할 호출.
- ratio 는 그룹별 정규화된 상대값(0~100)이라 그룹 간 절대량 비교는
  불가능하지만, 같은 배치의 랭킹 선별에는 충분하다.
- 기간(기본 28일) 평균 ratio 를 키워드별 수요 값으로 집계한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

from loguru import logger

from app.config import config

DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"
MAX_GROUPS_PER_CALL = 5
DEFAULT_DAYS = 28
_TIMEOUT_S = 15


class DataLabNotConfiguredError(RuntimeError):
    """네이버 오픈API 키 미설정."""


class DataLabFetchError(RuntimeError):
    """데이터랩 호출/파싱 실패."""


def configured() -> bool:
    client_id = str(config.app.get("naver_client_id", "")).strip()
    client_secret = str(config.app.get("naver_client_secret", "")).strip()
    return bool(client_id and client_secret)


def _credentials() -> tuple[str, str]:
    client_id = str(config.app.get("naver_client_id", "")).strip()
    client_secret = str(config.app.get("naver_client_secret", "")).strip()
    if not client_id or not client_secret:
        raise DataLabNotConfiguredError(
            "네이버 오픈API 키가 없습니다: config 에 naver_client_id / "
            "naver_client_secret 을 설정하세요 (데이터랩 검색추이 API 권한 필요)"
        )
    return client_id, client_secret


def _average_ratio(result: dict) -> float:
    data = result.get("data") or []
    ratios = [float(item.get("ratio") or 0) for item in data]
    if not ratios:
        return 0.0
    return round(sum(ratios) / len(ratios), 2)


def _request_group_demand(
    keywords: list[str], start: date, end: date, client_id: str, client_secret: str
) -> dict[str, float]:
    payload = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "group": [
            {"groupName": keyword, "keywords": [keyword]} for keyword in keywords
        ],
    }
    request = urllib.request.Request(
        DATALAB_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataLabFetchError(f"데이터랩 호출 실패: {exc}") from exc
    return {
        result.get("title", ""): _average_ratio(result)
        for result in body.get("results") or []
    }


def fetch_demand(
    keywords: list[str], *, days: int = DEFAULT_DAYS, today: date | None = None
) -> dict[str, float]:
    """키워드별 기간 평균 검색 추이 ratio 를 반환한다.

    반환 dict 에 없는 키워드는 데이터랩이 결과를 주지 않은 것 —
    호출자(갭 랭킹)는 다음 수요 신호로 폴백한다.
    """
    clean = [keyword.strip() for keyword in keywords if keyword.strip()]
    if not clean:
        raise ValueError("키워드가 없습니다")
    client_id, client_secret = _credentials()
    end = today or date.today()
    start = end - timedelta(days=max(1, days))

    demand: dict[str, float] = {}
    for index in range(0, len(clean), MAX_GROUPS_PER_CALL):
        chunk = clean[index : index + MAX_GROUPS_PER_CALL]
        chunk_demand = _request_group_demand(
            chunk, start, end, client_id, client_secret
        )
        for keyword in chunk:
            if keyword in chunk_demand:
                demand[keyword] = chunk_demand[keyword]
    logger.debug(f"데이터랩 수요 실측: {len(demand)}/{len(clean)}개 키워드")
    return demand
