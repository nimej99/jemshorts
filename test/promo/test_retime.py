"""소재 리타이밍(app.promo.materials.retime) 테스트 — 실제 ffmpeg 실측.

실측 섹션 길이에 맞춘 클립이 정말 그 길이로 나오는지 ffprobe 로 확인한다
(길이 계산만 검증하고 산출물을 안 보면 '조용한 어긋남'을 못 잡는다).
"""

import shutil
import subprocess

import pytest

from app.models.schema import MaterialInfo
from app.promo.materials import (
    RetimeError,
    probe_duration,
    retime_material,
    retime_materials,
)
from app.promo.materials.retime import CORE_TAIL_MARGIN_S, DEFAULT_TAIL_PADDING_S
from app.promo.timing import NarrationTiming, SectionTiming

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 가 없으면 리타이밍 실측을 검증할 수 없습니다",
)

_TOLERANCE_S = 0.15


def _make_video(path, seconds: float, color: str = "red") -> str:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x568:d={seconds}",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return str(path)


def _make_photo(path) -> str:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x568:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return str(path)


def _section(role: str, measured_s: float, start_s: float) -> SectionTiming:
    return SectionTiming(
        role=role,
        text=f"{role} 문장입니다.",
        target_s=measured_s,
        measured_s=measured_s,
        start_s=start_s,
    )


def test_video_longer_than_target_is_trimmed(tmp_path):
    source = _make_video(tmp_path / "src.mp4", 5)
    output = str(tmp_path / "out.mp4")

    retime_material(source, 2.0, output)

    assert abs(probe_duration(output) - 2.0) <= _TOLERANCE_S


def test_video_shorter_than_target_is_looped(tmp_path):
    """짧은 소재는 루프해서 섹션 길이를 채운다 (코어 루프는 섹션 경계를 모른다)."""
    source = _make_video(tmp_path / "src.mp4", 2)
    output = str(tmp_path / "out.mp4")

    retime_material(source, 5.0, output)

    assert abs(probe_duration(output) - 5.0) <= _TOLERANCE_S


def test_photo_becomes_clip_of_exact_length(tmp_path):
    source = _make_photo(tmp_path / "src.jpg")
    output = str(tmp_path / "out.mp4")

    retime_material(source, 3.5, output)

    assert abs(probe_duration(output) - 3.5) <= _TOLERANCE_S


def test_unsupported_material_rejected(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("not a material", encoding="utf-8")

    with pytest.raises(RetimeError, match="지원하지 않는"):
        retime_material(str(source), 2.0, str(tmp_path / "out.mp4"))


def test_zero_target_rejected(tmp_path):
    source = _make_video(tmp_path / "src.mp4", 2)

    with pytest.raises(RetimeError, match="0보다 커야"):
        retime_material(source, 0, str(tmp_path / "out.mp4"))


def test_probe_duration_reports_failure(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-a-video")

    with pytest.raises(RetimeError, match="ffprobe"):
        probe_duration(str(broken))


def test_retime_materials_matches_measured_sections(tmp_path):
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [
        MaterialInfo(provider="local", url=_make_video(tmp_path / "a.mp4", 6)),
        MaterialInfo(provider="local", url=_make_photo(tmp_path / "b.jpg")),
        MaterialInfo(provider="local", url=_make_video(tmp_path / "c.mp4", 1)),
    ]
    narration = NarrationTiming(
        sections=(
            _section("hook", 2.5, 0.0),
            _section("body", 8.0, 2.5),
            _section("cta", 4.0, 10.5),
        )
    )

    retimed = retime_materials(
        materials, narration, local_dir, retime_id="t1", tail_padding_s=1.0
    )

    assert [m.duration for m in retimed] == [2, 8, 5]
    expected = [2.5, 8.0, 5.0]  # 마지막 섹션에만 꼬리 여유 +1.0초
    for material, section, target in zip(retimed, narration.sections, expected):
        # 산출물은 보안 경로(storage/local_videos) 안에 있어야 코어가 받는다.
        assert material.url.startswith(str(local_dir.resolve()))
        assert section.role in material.url
        assert abs(probe_duration(material.url) - target) <= _TOLERANCE_S


def test_retime_materials_rejects_count_mismatch(tmp_path):
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [MaterialInfo(provider="local", url=_make_video(tmp_path / "a.mp4", 2))]
    narration = NarrationTiming(
        sections=(_section("hook", 2.0, 0.0), _section("cta", 3.0, 2.0))
    )

    with pytest.raises(RetimeError, match="일치하지 않습니다"):
        retime_materials(materials, narration, local_dir)


def test_tail_padding_below_core_margin_rejected(tmp_path):
    """코어 안전여유보다 작은 꼬리 여유는 이음매를 못 막는다 — 거부."""
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [MaterialInfo(provider="local", url=_make_video(tmp_path / "a.mp4", 2))]
    narration = NarrationTiming(sections=(_section("hook", 2.0, 0.0),))

    with pytest.raises(RetimeError, match="코어 안전여유"):
        retime_materials(materials, narration, local_dir, tail_padding_s=0.05)


def test_default_tail_padding_covers_core_safety_margin():
    """코어 상수가 바뀌면 이 테스트가 먼저 깨져야 한다 (조용한 어긋남 방지)."""
    from app.services.video import _VIDEO_DURATION_SAFETY_MARGIN

    assert CORE_TAIL_MARGIN_S == _VIDEO_DURATION_SAFETY_MARGIN
    assert DEFAULT_TAIL_PADDING_S >= CORE_TAIL_MARGIN_S
