"""품질 게이트 테스트.

실 렌더/실 네트워크 없음. technical_gate 는 ffmpeg lavfi 로 생성한
소형 테스트 파일(2초, 15fps)로 검증한다.
"""

import shutil
import subprocess

import pytest

from app.promo.quality import gates
from app.promo.quality.gates import (
    GateResult,
    TechnicalExpectation,
    structural_gate,
    technical_gate,
)
from app.promo.templates.schema import REQUIRED_ROLES, Section, validate_template

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 미설치",
)


def _make_clip(path, size, with_audio):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:size={size}:duration=2:rate=15"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "2", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("gate-clips")
    return {
        "ok": _make_clip(d / "ok.mp4", "1080x1920", with_audio=True),
        "small": _make_clip(d / "small.mp4", "640x360", with_audio=True),
        "noaudio": _make_clip(d / "noaudio.mp4", "1080x1920", with_audio=False),
    }


# ---------------------------------------------------------------------------
# technical_gate
# ---------------------------------------------------------------------------

def test_technical_gate_pass(clips):
    result = technical_gate(
        clips["ok"],
        TechnicalExpectation(duration_range=(1.5, 2.5), duration_tolerance_s=0.5),
    )
    assert result == GateResult(passed=True, failures=[], warnings=[])


def test_technical_gate_resolution_fail(clips):
    result = technical_gate(
        clips["small"],
        TechnicalExpectation(duration_range=(1.5, 2.5), duration_tolerance_s=0.5),
    )
    assert result.passed is False
    assert any("해상도" in failure and "640x360" in failure for failure in result.failures)


def test_technical_gate_missing_audio_fail(clips):
    result = technical_gate(
        clips["noaudio"],
        TechnicalExpectation(duration_range=(1.5, 2.5), duration_tolerance_s=0.5),
    )
    assert result.passed is False
    assert any("오디오" in failure for failure in result.failures)


def test_technical_gate_duration_out_of_range_fail(clips):
    result = technical_gate(
        clips["ok"],
        TechnicalExpectation(duration_range=(10, 12), duration_tolerance_s=0.5),
    )
    assert result.passed is False
    assert any("duration" in failure for failure in result.failures)


def test_technical_gate_missing_file_fail(tmp_path):
    result = technical_gate(
        str(tmp_path / "nope.mp4"),
        TechnicalExpectation(duration_range=(1, 2)),
    )
    assert result.passed is False
    assert any("파일이 없습니다" in failure for failure in result.failures)


def test_technical_gate_ffprobe_timeout_converges_to_failure(tmp_path, monkeypatch):
    """ffprobe hang(TimeoutExpired)은 예외 전파 없이 failure 로 수렴한다."""
    clip = tmp_path / "hang.mp4"
    clip.write_bytes(b"dummy")
    seen_kwargs = {}

    def fake_run(cmd, **kwargs):
        seen_kwargs.update(kwargs)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(gates.subprocess, "run", fake_run)

    result = technical_gate(str(clip), TechnicalExpectation(duration_range=(1, 2)))

    assert seen_kwargs.get("timeout") == 30
    assert result.passed is False
    assert any("timeout" in failure for failure in result.failures)


# ---------------------------------------------------------------------------
# structural_gate (ffprobe 불필요 — 순수 로직)
# ---------------------------------------------------------------------------

def _sections(*roles):
    return [
        Section(role=role, duration_s=5.0, script_guide="가이드", material_slot="any")
        for role in roles
    ]


def test_structural_gate_pass():
    result = structural_gate(
        _sections("hook", "body", "cta"),
        ["m1.mp4", "m2.mp4"],
        used_brand_count=1,
    )
    assert result == GateResult(passed=True, failures=[], warnings=[])


def test_structural_gate_brand_zero_with_photo_warning_is_downgraded():
    result = structural_gate(
        _sections("hook", "body", "cta"),
        ["stock.mp4"],
        used_brand_count=0,
        photo_warning=True,
    )
    assert result.passed is True
    # 조용한 강등 금지: 강등 사유가 warnings 에 기록되어야 한다.
    assert any("브랜드 소재 0개" in warning for warning in result.warnings)


def test_structural_gate_brand_zero_without_photo_warning_fails():
    result = structural_gate(
        _sections("hook", "body", "cta"),
        ["stock.mp4"],
        used_brand_count=0,
        photo_warning=False,
    )
    assert result.passed is False
    assert any("브랜드 소재" in failure for failure in result.failures)
    assert result.warnings == []


def test_structural_gate_missing_cta_fails():
    result = structural_gate(
        _sections("hook", "body"),
        ["m1.mp4"],
        used_brand_count=1,
    )
    assert result.passed is False
    assert any("cta" in failure for failure in result.failures)


def test_structural_gate_empty_materials_fails():
    result = structural_gate(
        _sections("hook", "body", "cta"),
        [],
        used_brand_count=1,
    )
    assert result.passed is False
    assert any("video_materials" in failure for failure in result.failures)


def test_structural_gate_accepts_dict_sections():
    result = structural_gate(
        [{"role": "hook"}, {"role": "body"}, {"role": "cta"}],
        ["m1.mp4"],
        used_brand_count=1,
    )
    assert result.passed is True


def test_gate_and_schema_share_required_roles_contract():
    """게이트 필수 역할 계약은 schema.REQUIRED_ROLES 단일 상수다 (body 선택)."""
    assert gates.REQUIRED_ROLES is REQUIRED_ROLES
    assert REQUIRED_ROLES == ("hook", "cta")


def test_structural_gate_passes_schema_valid_two_section_template():
    """스키마상 유효한 hook+cta 2섹션 템플릿이 구조 게이트를 통과한다."""
    template = validate_template(
        {
            "template_id": "two-section-v1",
            "name": "2섹션 템플릿",
            "version": 1,
            "mood": "upbeat",
            "structure": [
                {
                    "role": "hook",
                    "duration_s": 5,
                    "script_guide": "훅",
                    "material_slot": "any",
                },
                {
                    "role": "cta",
                    "duration_s": 5,
                    "script_guide": "행동 유도",
                    "material_slot": "any",
                },
            ],
            "total_duration_range": [10, 60],
            "caption_template": "{shop_name} 캡션",
            "hashtags_base": ["테스트"],
        }
    )
    result = structural_gate(
        template.structure,
        ["m1.mp4"],
        used_brand_count=1,
    )
    assert result == GateResult(passed=True, failures=[], warnings=[])
