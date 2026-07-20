"""트렌드 템플릿 스키마/가드레일 + curated_fetch 폴백 테스트.

네트워크 호출은 전부 mock 처리한다 (실제 urlopen 호출 금지).
"""

import io
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.promo.templates import curated_fetch
from app.promo.templates.schema import (
    TemplateValidationError,
    load_all,
    validate_template,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = REPO_ROOT / "templates-data"

SEED_IDS = {
    "upbeat-new-menu-v1",
    "calm-space-mood-v1",
    "energetic-event-sale-v1",
}


def _valid_template(**overrides) -> dict:
    """가드레일을 모두 통과하는 기준 템플릿 dict (총 20초)."""
    data = {
        "template_id": "test-template-v1",
        "name": "테스트 템플릿",
        "version": 1,
        "mood": "upbeat",
        "structure": [
            {
                "role": "hook",
                "duration_s": 3,
                "script_guide": "시선을 붙잡는 첫 컷",
                "material_slot": "video",
            },
            {
                "role": "body",
                "duration_s": 12,
                "script_guide": "핵심 내용 소개",
                "material_slot": "any",
            },
            {
                "role": "cta",
                "duration_s": 5,
                "script_guide": "방문 유도 멘트",
                "material_slot": "photo",
            },
        ],
        "total_duration_range": [10, 60],
        "caption_template": "{shop_name} 소식입니다.",
        "hashtags_base": ["동네가게"],
    }
    data.update(overrides)
    return data


def _with_durations(*durations_and_roles) -> dict:
    """(role, duration_s) 목록으로 structure 를 구성한 템플릿 dict."""
    structure = [
        {
            "role": role,
            "duration_s": duration,
            "script_guide": f"{role} 가이드",
            "material_slot": "any",
        }
        for role, duration in durations_and_roles
    ]
    return _valid_template(structure=structure)


# ---------------------------------------------------------------------------
# (a) 시드 템플릿
# ---------------------------------------------------------------------------


def test_bundled_seed_templates_all_pass_load_all():
    """번들 시드 3개가 전부 load_all 검증을 통과한다."""
    templates = load_all(BUNDLED_DIR)

    assert len(templates) == 3
    assert {t.template_id for t in templates} == SEED_IDS
    assert {t.mood for t in templates} == {"upbeat", "calm", "energetic"}
    for template in templates:
        assert 15 <= template.total_duration_s <= 30
        assert template.structure[0].role == "hook"
        assert any(s.role == "cta" for s in template.structure)


# ---------------------------------------------------------------------------
# (b) 가드레일 위반
# ---------------------------------------------------------------------------


def test_total_duration_below_10s_rejected():
    data = _with_durations(("hook", 2), ("body", 4), ("cta", 3))  # 9초
    with pytest.raises(TemplateValidationError, match="총 길이"):
        validate_template(data)


def test_total_duration_above_60s_rejected():
    data = _with_durations(("hook", 10), ("body", 40), ("cta", 11))  # 61초
    with pytest.raises(TemplateValidationError, match="총 길이"):
        validate_template(data)


def test_missing_hook_rejected():
    data = _with_durations(("body", 12), ("cta", 5))
    with pytest.raises(TemplateValidationError, match="hook 섹션은 필수"):
        validate_template(data)


def test_hook_not_first_rejected():
    data = _with_durations(("body", 12), ("hook", 3), ("cta", 5))
    with pytest.raises(TemplateValidationError, match="첫 번째"):
        validate_template(data)


def test_missing_cta_rejected():
    data = _with_durations(("hook", 3), ("body", 12))
    with pytest.raises(TemplateValidationError, match="cta 섹션은 필수"):
        validate_template(data)


def test_invalid_mood_rejected():
    data = _valid_template(mood="melancholy")
    with pytest.raises(TemplateValidationError, match="mood"):
        validate_template(data)


def test_version_below_1_rejected():
    data = _valid_template(version=0)
    with pytest.raises(TemplateValidationError, match="version"):
        validate_template(data)


def test_fewer_than_two_sections_rejected():
    data = _valid_template(
        structure=[
            {
                "role": "hook",
                "duration_s": 15,
                "script_guide": "단독 섹션",
                "material_slot": "any",
            }
        ]
    )
    with pytest.raises(TemplateValidationError, match="최소 2개"):
        validate_template(data)


def test_total_duration_range_hi_above_60_rejected():
    """total_duration_range 상한은 60초로 클램프된다: [10, 100] 거부."""
    data = _valid_template(total_duration_range=[10, 100])
    with pytest.raises(TemplateValidationError, match="전역 허용 범위"):
        validate_template(data)


def test_total_duration_range_lo_below_10_rejected():
    """total_duration_range 하한은 10초로 클램프된다: [5, 60] 거부."""
    data = _valid_template(total_duration_range=[5, 60])
    with pytest.raises(TemplateValidationError, match="전역 허용 범위"):
        validate_template(data)


def test_total_duration_range_full_bounds_allowed():
    """경계값 [10, 60] 은 허용된다."""
    template = validate_template(_valid_template(total_duration_range=[10, 60]))
    assert template.total_duration_range == (10.0, 60.0)


# ---------------------------------------------------------------------------
# (c) curated_fetch 폴백 (실제 네트워크 호출 없음 - urlopen 전부 mock)
# 원격 fetch 는 base_url 명시 주입 시에만 활성화된다.
# ---------------------------------------------------------------------------

REMOTE_BASE_URL = "https://curated.example/repo"


def _raise_url_error(*args, **kwargs):
    raise urllib.error.URLError("mocked network failure")


def test_default_no_base_url_skips_remote_and_uses_bundle(tmp_path, monkeypatch, caplog):
    """base_url 미주입(기본 None)이면 원격 시도 없이 캐시→번들 폴백 (info 로그)."""

    def _forbid_urlopen(*args, **kwargs):
        raise AssertionError("base_url=None 인데 원격 fetch 가 호출되었습니다")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid_urlopen)
    caplog.set_level(logging.INFO, logger=curated_fetch.logger.name)

    templates = curated_fetch.fetch_curated_templates(cache_dir=tmp_path / "cache")

    assert {t.template_id for t in templates} == SEED_IDS
    assert "원격 fetch 없이 캐시/번들 사용" in caplog.text


def test_network_failure_falls_back_to_bundle(tmp_path, monkeypatch, caplog):
    """네트워크 실패 + 빈 캐시 → 번들(templates-data/) 폴백."""
    monkeypatch.setattr(urllib.request, "urlopen", _raise_url_error)
    caplog.set_level(logging.WARNING, logger=curated_fetch.logger.name)

    templates = curated_fetch.fetch_curated_templates(
        base_url=REMOTE_BASE_URL, cache_dir=tmp_path / "cache"
    )

    assert {t.template_id for t in templates} == SEED_IDS
    assert "원격 fetch 실패" in caplog.text
    assert "번들(templates-data/) 폴백" in caplog.text


def test_network_failure_prefers_cache_and_skips_invalid(tmp_path, monkeypatch, caplog):
    """네트워크 실패 → 캐시 폴백. 캐시 내 스키마 위반 템플릿은 개별 skip."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "good.json").write_text(
        json.dumps(_valid_template(), ensure_ascii=False), encoding="utf-8"
    )
    (cache / "bad.json").write_text(
        json.dumps(_valid_template(mood="invalid-mood"), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(urllib.request, "urlopen", _raise_url_error)
    caplog.set_level(logging.WARNING, logger=curated_fetch.logger.name)

    templates = curated_fetch.fetch_curated_templates(
        base_url=REMOTE_BASE_URL, cache_dir=cache
    )

    assert [t.template_id for t in templates] == ["test-template-v1"]
    assert "스키마 위반 템플릿 skip: bad.json" in caplog.text


def test_remote_fetch_skips_invalid_template(tmp_path, monkeypatch, caplog):
    """원격 fetch 성공 경로에서 스키마 위반 템플릿만 개별 skip + warning."""
    good = _valid_template()
    bad = _valid_template(template_id="bad-template-v1", mood="invalid-mood")
    responses = {
        "index.json": {"templates": ["good.json", "bad.json"]},
        "good.json": good,
        "bad.json": bad,
    }

    def fake_urlopen(url, timeout=None):
        assert timeout == curated_fetch.DEFAULT_TIMEOUT_S
        body = json.dumps(responses[url.rsplit("/", 1)[1]], ensure_ascii=False)
        return io.BytesIO(body.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    caplog.set_level(logging.WARNING, logger=curated_fetch.logger.name)
    cache = tmp_path / "cache"

    templates = curated_fetch.fetch_curated_templates(
        base_url=REMOTE_BASE_URL, cache_dir=cache
    )

    assert [t.template_id for t in templates] == ["test-template-v1"]
    assert "스키마 위반 템플릿 skip: bad.json" in caplog.text
    # 유효 템플릿만 캐시에 저장된다.
    assert (cache / "good.json").exists()
    assert not (cache / "bad.json").exists()
