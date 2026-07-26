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
from app.promo.materials import build_shot_filter, probe_dimensions
from app.promo.materials.retime import CORE_TAIL_MARGIN_S, DEFAULT_TAIL_PADDING_S
from app.promo.templates.schema import Shot
from app.promo.timing import NarrationTiming, SectionTiming

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 가 없으면 리타이밍 실측을 검증할 수 없습니다",
)

_TOLERANCE_S = 0.15
_FONT_PATH = str(
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "resource"
    / "fonts"
    / "NotoSansKR-Bold.otf"
)


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

    assert [clip.material.duration for clip in retimed] == [2, 8, 5]
    assert [clip.seconds for clip in retimed] == [2.5, 8.0, 5.0]  # 마지막에만 +1.0초
    assert [(c.section_index, c.shot_index) for c in retimed] == [(0, 0), (1, 0), (2, 0)]
    for clip, section in zip(retimed, narration.sections):
        # 산출물은 보안 경로(storage/local_videos) 안에 있어야 코어가 받는다.
        assert clip.material.url.startswith(str(local_dir.resolve()))
        assert section.role in clip.material.url
        assert abs(probe_duration(clip.material.url) - clip.seconds) <= _TOLERANCE_S


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


# --- 템플릿 v2 shots: 와이드 + 컷인 파생 클립 -------------------------------


def _frame_signature(path: str, at_s: float = 0.2) -> str:
    """클립의 한 프레임을 PNG 로 뽑아 내용 해시를 만든다 (화면이 다른지 판별)."""
    import hashlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        png = f"{tmp}/frame.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(at_s), "-i", path, "-frames:v", "1", png,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return hashlib.sha256(open(png, "rb").read()).hexdigest()


def _pattern_video(path, seconds: float) -> str:
    """중앙과 주변이 다른 패턴 영상 (컷인 크롭이 실제로 다른 화면인지 보려고)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=640x360:duration={seconds}:rate=30",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return str(path)


def test_shots_split_section_and_derive_distinct_frames(tmp_path):
    """와이드+컷인 2컷: 섹션 길이를 균등 분배하고 화면이 실제로 달라진다."""
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [
        MaterialInfo(provider="local", url=_pattern_video(tmp_path / "a.mp4", 6))
    ]
    narration = NarrationTiming(sections=(_section("hook", 6.0, 0.0),))
    shots = [
        (
            Shot(kind="wide"),
            Shot(kind="cutin", crop="center-zoom"),
        )
    ]

    clips = retime_materials(
        materials, narration, local_dir, shots=shots, retime_id="s1", tail_padding_s=0.5
    )

    assert [(c.section_index, c.shot_index) for c in clips] == [(0, 0), (0, 1)]
    # 6초를 2컷으로 균등 분배, 마지막 컷에만 꼬리 여유 +0.5초
    assert [c.seconds for c in clips] == [3.0, 3.5]
    for clip in clips:
        assert abs(probe_duration(clip.material.url) - clip.seconds) <= _TOLERANCE_S
    # 컷인은 같은 소재에서 나왔지만 화면(프레이밍)이 달라야 의미가 있다.
    assert _frame_signature(clips[0].material.url) != _frame_signature(
        clips[1].material.url
    )
    # 해상도는 유지된다 (코어가 받는 소재 규격 불변).
    assert probe_dimensions(clips[0].material.url) == probe_dimensions(
        clips[1].material.url
    )


@pytest.mark.parametrize(
    "motion", ["static", "push-in", "pull-out", "drift", "pan-left", "pan-right"]
)
def test_every_motion_renders_valid_clip(tmp_path, motion):
    """스키마가 허용하는 모션은 전부 실제로 렌더돼야 한다 (선언만 되고 죽은 값 금지)."""
    source = _pattern_video(tmp_path / "src.mp4", 3)
    output = str(tmp_path / f"out-{motion}.mp4")

    retime_material(source, 2.0, output, shot=Shot(kind="wide", motion=motion))

    assert abs(probe_duration(output) - 2.0) <= _TOLERANCE_S
    assert probe_dimensions(output) == (640, 360)


@pytest.mark.parametrize("crop", ["center-zoom", "top", "bottom", "left", "right"])
def test_every_crop_renders_distinct_framing(tmp_path, crop):
    """스키마가 허용하는 크롭은 전부 렌더되고 전체 화면과 달라야 한다."""
    source = _pattern_video(tmp_path / "src.mp4", 3)
    wide = str(tmp_path / "wide.mp4")
    cut = str(tmp_path / f"cut-{crop}.mp4")

    retime_material(source, 1.5, wide, shot=Shot(kind="wide"))
    retime_material(source, 1.5, cut, shot=Shot(kind="cutin", crop=crop))

    assert abs(probe_duration(cut) - 1.5) <= _TOLERANCE_S
    assert _frame_signature(wide) != _frame_signature(cut)


def test_wide_without_motion_needs_no_filter(tmp_path):
    """와이드 정지컷은 필터 없이 그대로 간다 (불필요한 재프레이밍 금지)."""
    source = _pattern_video(tmp_path / "src.mp4", 2)

    assert build_shot_filter(Shot(kind="wide"), 2.0, source) is None
    assert build_shot_filter(None, 2.0, source) is None
    assert build_shot_filter(Shot(kind="cutin"), 2.0, source) is not None


def test_shots_count_mismatch_rejected(tmp_path):
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [MaterialInfo(provider="local", url=_make_video(tmp_path / "a.mp4", 2))]
    narration = NarrationTiming(sections=(_section("hook", 2.0, 0.0),))

    with pytest.raises(RetimeError, match="샷 선언"):
        retime_materials(materials, narration, local_dir, shots=[(), ()])


# --- 템플릿 v2 headline: 배너 합성 ------------------------------------------


def _band_signature(path: str, top: bool, at_s: float = 0.2) -> str:
    """클립 프레임의 위/아래 절반을 각각 해시한다 (배너가 어디에 얹혔는지 판별)."""
    import hashlib
    import tempfile

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        png = f"{tmp}/frame.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(at_s), "-i", path, "-frames:v", "1", png,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        image = Image.open(png).convert("RGB")
        width, height = image.size
        box = (0, 0, width, height // 2) if top else (0, height // 2, width, height)
        return hashlib.sha256(image.crop(box).tobytes()).hexdigest()


def _headline_png(tmp_path, text="신메뉴 출시!") -> str:
    from app.promo.materials.headline import render_headline_png

    return render_headline_png(
        text, 640, 360, tmp_path / "banner.png", font_path=_FONT_PATH
    )


def _band_mean_diff(path_a: str, path_b: str, top: bool, at_s: float = 0.2) -> float:
    """두 클립 프레임의 위/아래 절반 평균 픽셀 차이 (0~255)."""
    import tempfile

    from PIL import Image, ImageChops, ImageStat

    def _band(path, tmp, name):
        png = f"{tmp}/{name}.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(at_s), "-i", path, "-frames:v", "1", png,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        image = Image.open(png).convert("RGB")
        width, height = image.size
        box = (0, 0, width, height // 2) if top else (0, height // 2, width, height)
        return image.crop(box)

    with tempfile.TemporaryDirectory() as tmp:
        diff = ImageChops.difference(_band(path_a, tmp, "a"), _band(path_b, tmp, "b"))
        return sum(ImageStat.Stat(diff).mean) / 3


def test_headline_overlay_changes_only_top_half(tmp_path):
    """배너는 상단만 바꾼다 — 하단(소재 본 화면)은 사실상 그대로여야 한다.

    필터 그래프가 달라지면 인코딩 결과가 비트 단위로 같지 않으므로 평균
    픽셀 차이로 본다 (상단은 확연히, 하단은 인코딩 오차 수준).
    """
    source = _pattern_video(tmp_path / "src.mp4", 3)
    plain = str(tmp_path / "plain.mp4")
    banner = str(tmp_path / "banner.mp4")

    retime_material(source, 1.5, plain, shot=Shot(kind="wide"))
    retime_material(
        source, 1.5, banner, shot=Shot(kind="wide"), headline_png=_headline_png(tmp_path)
    )

    assert _band_mean_diff(banner, plain, top=True) > 5.0
    assert _band_mean_diff(banner, plain, top=False) < 1.0


def test_missing_headline_png_rejected(tmp_path):
    source = _pattern_video(tmp_path / "src.mp4", 2)

    with pytest.raises(RetimeError, match="헤드라인 배너 파일"):
        retime_material(
            source, 1.0, str(tmp_path / "out.mp4"), headline_png=str(tmp_path / "no.png")
        )


def test_headline_lands_on_first_shot_only(tmp_path):
    """배너는 섹션 첫 컷에만 (컷인까지 깔면 시각 밀도가 무너진다)."""
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [
        MaterialInfo(provider="local", url=_pattern_video(tmp_path / "a.mp4", 6))
    ]
    narration = NarrationTiming(sections=(_section("hook", 4.0, 0.0),))
    shots = [(Shot(kind="wide"), Shot(kind="cutin"))]

    with_banner = retime_materials(
        materials,
        narration,
        local_dir,
        shots=shots,
        headlines=["신메뉴 출시!"],
        font_path=_FONT_PATH,
        retime_id="h1",
        tail_padding_s=0.5,
    )
    without = retime_materials(
        materials,
        narration,
        local_dir,
        shots=shots,
        retime_id="h0",
        tail_padding_s=0.5,
    )

    assert _band_signature(with_banner[0].material.url, top=True) != _band_signature(
        without[0].material.url, top=True
    )
    assert _band_signature(with_banner[1].material.url, top=True) == _band_signature(
        without[1].material.url, top=True
    )


def test_headline_count_mismatch_rejected(tmp_path):
    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    materials = [MaterialInfo(provider="local", url=_make_video(tmp_path / "a.mp4", 2))]
    narration = NarrationTiming(sections=(_section("hook", 2.0, 0.0),))

    with pytest.raises(RetimeError, match="헤드라인"):
        retime_materials(materials, narration, local_dir, headlines=["a", "b"])
