"""큐레이션 템플릿 원격 fetch + 캐시/번들 폴백.

원격 fetch 는 base_url 을 명시 주입한 경우에만 활성화된다. base_url=None
(기본)이면 원격 시도 없이 캐시 → 번들로 바로 폴백한다 (의도된 로컬 전용
동작이므로 warning 이 아닌 info 로그).

base_url 이 주어지면 (예: 큐레이션 저장소 raw URL) index.json 과 개별
템플릿 JSON 을 가져와 storage 하위 캐시 디렉터리에 저장한다.

폴백 순서:
1. 원격 fetch (base_url 명시 주입 시에만). 스키마 위반 템플릿은 개별 skip + warning.
2. 원격 미사용/실패(네트워크 오류/4xx/index 형식 오류) 또는 유효 템플릿 0개 → 캐시 디렉터리.
3. 캐시도 비어 있으면 → 레포 번들 templates-data/.

네트워크는 stdlib urllib 만 사용하며 timeout 은 10초다.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from app.promo.templates.schema import (
    Template,
    TemplateValidationError,
    load_template,
    validate_template,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10
INDEX_FILENAME = "index.json"
CACHE_SUBDIR = "promo_templates"

# 레포 루트: app/promo/templates/curated_fetch.py -> templates -> promo -> app -> <root>
BUNDLED_DIR = Path(__file__).resolve().parents[3] / "templates-data"

# 원격 fetch 단계에서 폴백을 유발하는 예외들.
# HTTPError(4xx/5xx) 는 URLError 의 하위 클래스, JSONDecodeError 는 ValueError 의
# 하위 클래스이므로 index 형식 오류(ValueError)와 함께 잡힌다.
_FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def _default_cache_dir() -> Path:
    # app.utils 는 로깅 등 부수효과가 있어 필요 시점에만 임포트한다.
    from app.utils import utils

    return Path(utils.storage_dir(CACHE_SUBDIR, create=True))


def _http_get_json(url: str, timeout: float) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _fetch_remote(base_url: str, cache_dir: Path, timeout: float) -> list[Template]:
    base = base_url.rstrip("/")
    index = _http_get_json(f"{base}/{INDEX_FILENAME}", timeout)
    filenames = index.get("templates") if isinstance(index, dict) else index
    if not isinstance(filenames, list) or not all(
        isinstance(name, str) for name in filenames
    ):
        raise ValueError("index.json 형식 오류: 'templates' 문자열 배열이 필요합니다")

    cache_dir.mkdir(parents=True, exist_ok=True)
    templates: list[Template] = []
    for filename in filenames:
        try:
            data = _http_get_json(f"{base}/{filename}", timeout)
            template = validate_template(data, source=filename)
        except TemplateValidationError as exc:
            logger.warning("스키마 위반 템플릿 skip: %s (%s)", filename, exc)
            continue
        except _FETCH_ERRORS as exc:
            logger.warning("템플릿 fetch 실패 skip: %s (%s)", filename, exc)
            continue
        cache_path = cache_dir / Path(filename).name
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        templates.append(template)
    return templates


def _load_dir_lenient(directory: Path) -> list[Template]:
    """디렉터리의 템플릿을 로드하되, 위반 템플릿은 개별 skip + warning."""
    if not directory.is_dir():
        return []
    templates: list[Template] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == INDEX_FILENAME:
            continue
        try:
            templates.append(load_template(path))
        except TemplateValidationError as exc:
            logger.warning("스키마 위반 템플릿 skip: %s (%s)", path.name, exc)
    return templates


def fetch_curated_templates(
    base_url: str | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[Template]:
    """큐레이션 템플릿 목록을 반환한다 (원격 → 캐시 → 번들 폴백).

    base_url 이 None(기본)이면 원격 fetch 를 시도하지 않고 캐시 → 번들로
    바로 폴백한다 (모듈 docstring 참조).
    """
    cache = Path(cache_dir) if cache_dir is not None else _default_cache_dir()

    if base_url is None:
        logger.info("base_url 미지정 - 원격 fetch 없이 캐시/번들 사용")
    else:
        try:
            templates = _fetch_remote(base_url, cache, timeout)
            if templates:
                return templates
            logger.warning("원격 큐레이션 템플릿에 유효 항목이 없습니다 - 폴백 진행")
        except _FETCH_ERRORS as exc:
            logger.warning("큐레이션 템플릿 원격 fetch 실패 (%s) - 캐시 폴백 시도", exc)

    cached = _load_dir_lenient(cache)
    if cached:
        logger.info("캐시 템플릿 %d개 사용: %s", len(cached), cache)
        return cached

    logger.warning("캐시 비어 있음 - 번들(templates-data/) 폴백")
    return _load_dir_lenient(BUNDLED_DIR)
