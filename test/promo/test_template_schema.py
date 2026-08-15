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
    DEFAULT_TIMING_TOLERANCE_S,
    MAX_SHOTS_PER_SECTION,
    MAX_TEMPLATE_VERSION,
    Headline,
    Shot,
    TemplateValidationError,
    TimingSpec,
    VoiceSpec,
    load_all,
    load_template,
    validate_template,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = REPO_ROOT / "templates-data"

SEED_IDS = {
    "upbeat-new-menu-v1",
    "calm-space-mood-v2",
    "commerce-pick-review-v2",
    "energetic-event-sale-v2",
    "upbeat-new-menu-v2",
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
    """번들 시드 5개가 전부 load_all 검증을 통과한다."""
    templates = load_all(BUNDLED_DIR)

    assert len(templates) == 5
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


# ---------------------------------------------------------------------------
# (d) v2 선택 필드 (docs/TEMPLATE_V2_DESIGN.md §4) — v1 완전 하위호환
# ---------------------------------------------------------------------------


def _v2_template(**overrides) -> dict:
    """v2 선택 필드를 모두 채운 템플릿 dict."""
    data = _valid_template(version=2)
    data["style_preset"] = "clean-product"
    data["voice"] = {"speed": 1.1}
    data["timing"] = {"owner": "narration", "tolerance_s": 0.2}
    data["structure"][0]["headline"] = {"template": "{menu_name} 출시!", "show": True}
    data["structure"][0]["shots"] = [
        {"kind": "wide", "motion": "push-in"},
        {"kind": "cutin", "motion": "drift", "crop": "center-zoom"},
    ]
    data["structure"][0]["feel"] = "따뜻한, 식욕을 돋우는"
    data.update(overrides)
    return data


def _v2_section(**section_fields) -> dict:
    """hook 섹션에만 v2 필드를 얹은 version 2 템플릿."""
    data = _valid_template(version=2)
    data["structure"][0].update(section_fields)
    return data


def test_v1_template_keeps_v2_fields_empty():
    """version 1 문서는 무수정 통과하고 v2 필드는 전부 기본값(미지정)이다."""
    template = validate_template(_valid_template())

    assert template.version == 1
    assert template.style_preset is None
    assert template.voice is None
    assert template.timing is None
    assert all(
        section.headline is None and section.shots == () and section.feel is None
        for section in template.structure
    )


def test_v2_full_template_parsed():
    """v2 전 필드가 dataclass 로 파싱된다."""
    template = validate_template(_v2_template())

    assert template.version == 2
    assert template.style_preset == "clean-product"
    assert template.voice == VoiceSpec(speed=1.1)
    assert template.timing == TimingSpec(owner="narration", tolerance_s=0.2)

    hook = template.structure[0]
    assert hook.headline == Headline(template="{menu_name} 출시!", show=True)
    assert hook.shots == (
        Shot(kind="wide", motion="push-in"),
        Shot(kind="cutin", motion="drift", crop="center-zoom"),
    )
    assert hook.feel == "따뜻한, 식욕을 돋우는"
    # v2 필드는 v1 계약(총 길이/역할 순서)을 바꾸지 않는다.
    assert template.total_duration_s == 20
    assert [s.role for s in template.structure] == ["hook", "body", "cta"]


def test_v2_defaults_applied_when_subfields_omitted():
    """voice.speed / timing.tolerance_s / headline.show 는 기본값을 갖는다."""
    data = _valid_template(version=2)
    data["voice"] = {}
    data["timing"] = {"owner": "template"}
    data["structure"][0]["headline"] = {"template": "제목"}

    template = validate_template(data)

    assert template.voice.speed == 1.0
    assert template.timing.tolerance_s == DEFAULT_TIMING_TOLERANCE_S
    assert template.structure[0].headline.show is True


def test_v2_template_roundtrips_through_load_template(tmp_path):
    """v2 JSON 파일이 로더를 그대로 통과한다."""
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(_v2_template(), ensure_ascii=False), encoding="utf-8")

    template = load_template(path)

    assert template.timing.owner == "narration"
    assert template.structure[0].shots[1].crop == "center-zoom"


def test_autopilot_flag_defaults_true_and_parses():
    assert validate_template(_valid_template()).autopilot is True
    assert validate_template(_valid_template(autopilot=False)).autopilot is False


def test_autopilot_flag_rejects_non_bool():
    with pytest.raises(TemplateValidationError):
        validate_template(_valid_template(autopilot="no"))


def test_commerce_seed_is_manual_only():
    """커머스 추천 시드는 무인 로테이션 제외 — LLM 지어낸 상품 자동 공개 금지."""
    templates = {t.template_id: t for t in load_all(BUNDLED_DIR)}

    assert templates["commerce-pick-review-v2"].autopilot is False
    assert all(
        template.autopilot
        for template_id, template in templates.items()
        if template_id != "commerce-pick-review-v2"
    )


def test_bundled_seed_versions():
    """upbeat-new-menu-v1 만 하위호환 회귀 기준, 나머지 3종은 v2(실제 운영 템플릿)."""
    versions = {t.template_id: t.version for t in load_all(BUNDLED_DIR)}

    assert versions == {
        "upbeat-new-menu-v1": 1,
        "calm-space-mood-v2": 2,
        "commerce-pick-review-v2": 2,
        "energetic-event-sale-v2": 2,
        "upbeat-new-menu-v2": 2,
    }


def test_v2_seed_exercises_full_feature_set():
    """v2 시드는 라벨만 v2 가 아니라 shots/headline/timing/style_preset 을 실제로 쓴다.

    번들 시드가 기능을 실제로 사용해야 스케줄러 로테이션에서 새 경로가 돈다.
    """
    template = load_template(BUNDLED_DIR / "upbeat-new-menu-v2.json")

    assert template.style_preset == "warm-food"
    assert template.timing is not None and template.timing.owner == "narration"
    assert template.voice is not None

    hook = template.structure[0]
    assert [shot.kind for shot in hook.shots] == ["wide", "cutin"]
    assert hook.headline is not None and hook.headline.show is True
    assert hook.feel


def test_all_v2_seeds_declare_narration_timing_and_shots():
    """v2 시드 3종 전부 실측 타이밍 + 컷인을 실제로 선언한다 (로테이션 품질 균일)."""
    for template in load_all(BUNDLED_DIR):
        if template.version < 2:
            continue
        assert template.timing is not None and template.timing.owner == "narration", (
            f"{template.template_id}: timing.owner=narration 누락"
        )
        assert template.style_preset, f"{template.template_id}: style_preset 누락"
        assert any(section.shots for section in template.structure), (
            f"{template.template_id}: shots 를 쓰는 섹션이 없음"
        )


@pytest.mark.parametrize(
    "filename, expected_vars",
    [
        ("upbeat-new-menu-v2.json", ["menu_name", "highlight"]),
        ("calm-space-mood-v2.json", ["mood_point"]),
        ("energetic-event-sale-v2.json", ["event_name", "benefit", "period"]),
        (
            "commerce-pick-review-v2.json",
            ["product_name", "pain_point", "price_deal"],
        ),
    ],
)
def test_v2_seeds_declare_dynamic_variables(filename, expected_vars):
    """v2 시드의 캡션/헤드라인이 동적 변수를 선언 — LLM 이 채울 값이다."""
    from app.promo.templates.variables import required_variables

    template = load_template(BUNDLED_DIR / filename)
    assert required_variables(template) == expected_vars


@pytest.mark.parametrize(
    "field, value",
    [
        ("style_preset", "clean-product"),
        ("voice", {"speed": 1.0}),
        ("timing", {"owner": "narration"}),
    ],
)
def test_v2_top_level_field_in_v1_document_rejected(field, value):
    """version 1 문서의 v2 필드는 조용히 무시하지 않고 거부한다."""
    with pytest.raises(TemplateValidationError, match="version 2 이상"):
        validate_template(_valid_template(**{field: value}))


@pytest.mark.parametrize(
    "field, value",
    [
        ("headline", {"template": "제목"}),
        ("shots", [{"kind": "wide"}]),
        ("feel", "따뜻한"),
    ],
)
def test_v2_section_field_in_v1_document_rejected(field, value):
    """섹션 단위 v2 필드도 version 1 문서에서는 거부된다 (위치 포함 메시지)."""
    data = _valid_template()
    data["structure"][0][field] = value

    with pytest.raises(TemplateValidationError, match=rf"structure\[0\]\.{field}"):
        validate_template(data)


def test_version_above_max_rejected():
    """미지원 상위 버전은 해석할 수 없으므로 거부한다."""
    with pytest.raises(TemplateValidationError, match="version"):
        validate_template(_valid_template(version=MAX_TEMPLATE_VERSION + 1))


def test_shots_must_start_with_wide():
    """컷인은 와이드샷 파생 — 첫 컷이 cutin 이면 거부."""
    data = _v2_section(shots=[{"kind": "cutin"}, {"kind": "wide"}])

    with pytest.raises(TemplateValidationError, match="'wide' 여야 합니다"):
        validate_template(data)


def test_shots_above_max_rejected():
    """섹션당 컷 수 상한을 넘으면 거부한다."""
    shots = [{"kind": "wide"}] + [{"kind": "cutin"}] * MAX_SHOTS_PER_SECTION
    data = _v2_section(shots=shots)

    with pytest.raises(TemplateValidationError, match="최대 4컷"):
        validate_template(data)


def test_empty_shots_rejected():
    """shots 를 선언했으면 비어있을 수 없다."""
    with pytest.raises(TemplateValidationError, match="비어있지 않은 배열"):
        validate_template(_v2_section(shots=[]))


@pytest.mark.parametrize(
    "shot, expected",
    [
        ({"kind": "closeup"}, "kind"),
        ({"kind": "wide", "motion": "zoom-bounce"}, "motion"),
        ({"kind": "wide", "crop": "diagonal"}, "crop"),
        ({"kind": "wide", "zoom": 2}, "알 수 없는 필드"),
    ],
)
def test_invalid_shot_values_rejected(shot, expected):
    """샷 어휘는 닫힌 집합 — 오타는 렌더 단계에서 조용히 사라지면 안 된다."""
    with pytest.raises(TemplateValidationError, match=expected):
        validate_template(_v2_section(shots=[shot]))


@pytest.mark.parametrize("speed", [0.4, 2.1, "1.0", True])
def test_voice_speed_out_of_range_rejected(speed):
    with pytest.raises(TemplateValidationError, match="voice.speed"):
        validate_template(_valid_template(version=2, voice={"speed": speed}))


def test_voice_unknown_field_rejected():
    with pytest.raises(TemplateValidationError, match="알 수 없는 필드가 있습니다: pitch"):
        validate_template(_valid_template(version=2, voice={"pitch": 3}))


def test_timing_owner_invalid_rejected():
    with pytest.raises(TemplateValidationError, match="timing.owner"):
        validate_template(_valid_template(version=2, timing={"owner": "assembler"}))


@pytest.mark.parametrize("tolerance", [0, -0.1, 1.5])
def test_timing_tolerance_out_of_range_rejected(tolerance):
    data = _valid_template(
        version=2, timing={"owner": "narration", "tolerance_s": tolerance}
    )

    with pytest.raises(TemplateValidationError, match="timing.tolerance_s"):
        validate_template(data)


@pytest.mark.parametrize(
    "headline, expected",
    [
        ({"template": "  "}, "template"),
        ({"template": "제목", "show": "yes"}, "show"),
        ({"show": True}, "template"),
    ],
)
def test_invalid_headline_rejected(headline, expected):
    with pytest.raises(TemplateValidationError, match=expected):
        validate_template(_v2_section(headline=headline))


def test_empty_feel_rejected():
    with pytest.raises(TemplateValidationError, match="feel"):
        validate_template(_v2_section(feel="   "))
