"""실측 섹션 길이에 맞춘 소재 리타이밍 (템플릿 v2 `timing.owner = "narration"`).

docs/TEMPLATE_V2_DESIGN.md §6 (b-2). `compose_materials` 가 섹션 순서대로
배치한 소재를, 내레이션 실측 길이에 **정확히** 맞는 클립으로 다시 만든다.

왜 이걸로 충분한가 (코어 무접촉 근거):
MPT 코어 `combine_videos` 는 소재를 `max_clip_duration` 단위로 잘라 순서대로
이어붙이고 오디오 길이에 맞춰 루프한다. 소재 자체가 이미 섹션 실측 길이이고
`max_clip_duration` 이 그보다 크거나 같으면 추가 분할이 일어나지 않으므로
(clip.duration <= max_clip_duration), 화면 전환 지점이 섹션 경계와 일치한다.

- 사진: 실측 길이만큼 정지 영상 클립으로 렌더한다 (코어 preprocess_video 의
  전역 clip_duration 적용을 우회 — 섹션마다 길이가 달라야 하기 때문).
- 영상: 실측 길이보다 길면 자르고, 짧으면 루프해서 채운다.
- 오디오 트랙은 버린다 (`-an`). 내레이션/BGM 은 코어가 붙인다.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from loguru import logger

from app.models import const
from app.models.schema import MaterialInfo
from app.promo.templates.schema import Shot
from app.promo.timing import NarrationTiming

_FFMPEG_TIMEOUT_S = 300
_FFPROBE_TIMEOUT_S = 30
_OUTPUT_FPS = 30

# 코어 combine_videos 는 오디오 길이 + 안전여유만큼의 영상을 요구하고,
# 모자라면 앞 클립부터 다시 붙인다 (app/services/video.py
# _VIDEO_DURATION_SAFETY_MARGIN). 실측 합과 통합 TTS 실제 길이도 문장 사이
# 호흡 때문에 정확히 같지 않다. 마지막 섹션 클립에만 여유를 붙이면 "끝에서
# 훅 화면이 다시 번쩍이는" 이음매가 사라진다 — 남는 꼬리는 코어가 오디오
# 길이에 맞춰 잘라낸다.
CORE_TAIL_MARGIN_S = 0.1
DEFAULT_TAIL_PADDING_S = 1.0

# 컷인 프레이밍: (가로 비율, 세로 비율, 가로 앵커, 세로 앵커)
_CROP_REGIONS = {
    "center-zoom": (0.65, 0.65, "center", "center"),
    "top": (1.0, 0.7, "center", "top"),
    "bottom": (1.0, 0.7, "center", "bottom"),
    "left": (0.7, 1.0, "left", "center"),
    "right": (0.7, 1.0, "right", "center"),
}
_DEFAULT_CUTIN_CROP = "center-zoom"
_ZOOM_AMOUNT = 0.15  # push-in/pull-out 최대 확대율
_PAN_WINDOW = 0.85  # 팬/드리프트에 쓰는 창 크기 비율

_PHOTO_EXTS = frozenset(const.FILE_TYPE_IMAGES)
_VIDEO_EXTS = frozenset(const.FILE_TYPE_VIDEOS)


class RetimeError(RuntimeError):
    """소재 리타이밍 실패 (ffmpeg 실패, 지원하지 않는 소재 등)."""


@dataclass(frozen=True)
class RetimedClip:
    """실측 길이에 맞춰 새로 만든 클립 하나 (섹션/컷 위치를 함께 들고 있다)."""

    material: MaterialInfo
    seconds: float
    section_index: int
    shot_index: int


def probe_duration(path: str) -> float:
    """ffprobe 로 미디어 길이(초)를 읽는다. 못 읽으면 RetimeError."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RetimeError(f"ffprobe 실행 실패: {path} ({exc})") from exc
    if result.returncode != 0:
        raise RetimeError(f"ffprobe 실패: {path} ({result.stderr.strip()})")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RetimeError(f"ffprobe duration 파싱 실패: {path}") from exc
    if duration <= 0:
        raise RetimeError(f"소재 길이가 0입니다: {path}")
    return duration


def _run_ffmpeg(args: list[str], source: str) -> None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RetimeError(f"ffmpeg 실행 실패: {source} ({exc})") from exc
    if result.returncode != 0:
        raise RetimeError(f"ffmpeg 실패: {source} ({result.stderr.strip()})")


def _material_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext in _PHOTO_EXTS:
        return "photo"
    if ext in _VIDEO_EXTS:
        return "video"
    return "other"


def probe_dimensions(path: str) -> tuple[int, int]:
    """ffprobe 로 첫 비디오/이미지 스트림 해상도를 읽는다."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RetimeError(f"ffprobe 실행 실패: {path} ({exc})") from exc
    if result.returncode != 0:
        raise RetimeError(f"ffprobe 실패: {path} ({result.stderr.strip()})")
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RetimeError(f"ffprobe 해상도 파싱 실패: {path}") from exc
    if width <= 0 or height <= 0:
        raise RetimeError(f"해상도를 읽을 수 없습니다: {path}")
    return width, height


def _static_crop_filter(crop: str) -> str:
    """정지 크롭 영역 필터 (컷인 프레이밍)."""
    w_frac, h_frac, anchor_x, anchor_y = _CROP_REGIONS[crop]
    x = {"center": "(iw-ow)/2", "left": "0", "right": "iw-ow"}[anchor_x]
    y = {"center": "(ih-oh)/2", "top": "0", "bottom": "ih-oh"}[anchor_y]
    return (
        f"crop=w='2*trunc(iw*{w_frac}/2)':h='2*trunc(ih*{h_frac}/2)':x='{x}':y='{y}'"
    )


def _motion_filter(motion: str, seconds: float, width: int, height: int) -> str | None:
    """모션 필터. 줌은 zoompan(프레임별 z), 팬은 crop 의 시간 의존 x/y 로 만든다.

    crop 의 w/h 는 설정 시점에 한 번만 평가되므로 줌에는 쓸 수 없다 (실측 확인).
    """
    if motion in (None, "static"):
        return None
    span = max(seconds, 0.001)
    if motion in ("push-in", "pull-out"):
        peak = 1 + _ZOOM_AMOUNT
        frames = max(span * _OUTPUT_FPS, 1)
        if motion == "push-in":
            z = f"min(1+{_ZOOM_AMOUNT}*on/{frames:.3f},{peak})"
        else:
            z = f"max({peak}-{_ZOOM_AMOUNT}*on/{frames:.3f},1)"
        return (
            f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={_OUTPUT_FPS}"
        )

    progress = f"min(t/{span:.3f},1)"
    if motion == "pan-left":
        x, y = f"(iw-ow)*(1-{progress})", "(ih-oh)/2"
    elif motion == "pan-right":
        x, y = f"(iw-ow)*{progress}", "(ih-oh)/2"
    else:  # drift — 대각선 미세 이동
        x, y = f"(iw-ow)*{progress}", f"(ih-oh)*{progress}"
    return (
        f"crop=w='2*trunc(iw*{_PAN_WINDOW}/2)':h='2*trunc(ih*{_PAN_WINDOW}/2)'"
        f":x='{x}':y='{y}'"
    )


def build_shot_filter(shot: Shot | None, seconds: float, source_path: str) -> str | None:
    """샷 지시(kind/crop/motion)를 ffmpeg 필터 체인 문자열로 만든다.

    - wide: 크롭 없음(전체 화면). cutin: crop 미지정이면 중앙 확대가 기본.
    - 필터 체인 마지막에 원본 해상도로 되돌린다 (코어가 받는 소재 규격 유지).
    """
    if shot is None:
        return None
    crop = shot.crop or (_DEFAULT_CUTIN_CROP if shot.kind == "cutin" else None)
    motion = shot.motion
    if crop is None and motion in (None, "static"):
        return None

    width, height = probe_dimensions(source_path)
    chain = []
    if crop is not None:
        chain.append(_static_crop_filter(crop))
    motion_filter = _motion_filter(motion, seconds, width, height)
    if motion_filter:
        chain.append(motion_filter)
    chain.append(f"scale={width}:{height}:flags=bicubic")
    chain.append("setsar=1")
    return ",".join(chain)


def _encode_args(target_s: float, output_path: str) -> list[str]:
    return [
        "-t",
        f"{target_s:.3f}",
        "-an",
        "-r",
        str(_OUTPUT_FPS),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        output_path,
    ]


def retime_material(
    source_path: str,
    target_s: float,
    output_path: str,
    *,
    shot: Shot | None = None,
) -> str:
    """소재 하나를 target_s 길이의 무음 클립으로 다시 만든다.

    `shot` 이 있으면 크롭/모션 필터를 적용한다 (같은 소재에서 와이드/컷인
    파생 클립을 만드는 경로 — docs/TEMPLATE_V2_DESIGN.md §6 (c)).
    """
    if target_s <= 0:
        raise RetimeError(f"목표 길이는 0보다 커야 합니다: {target_s}")

    kind = _material_kind(source_path)
    if kind == "photo":
        args = ["-loop", "1", "-i", source_path]
    elif kind == "video":
        source_duration = probe_duration(source_path)
        if source_duration + 0.05 < target_s:
            # 소재가 짧으면 루프해서 목표 길이를 채운다 (코어 루프에 맡기면
            # 섹션 경계가 아니라 오디오 전체 길이 기준으로 채워진다).
            args = ["-stream_loop", "-1", "-i", source_path]
        else:
            args = ["-i", source_path]
    else:
        raise RetimeError(f"지원하지 않는 소재 형식입니다: {source_path}")

    shot_filter = build_shot_filter(shot, target_s, source_path)
    if shot_filter:
        args += ["-vf", shot_filter]
    args += _encode_args(target_s, output_path)

    _run_ffmpeg(args, source_path)
    if not os.path.isfile(output_path):
        raise RetimeError(f"리타이밍 산출물이 없습니다: {output_path}")
    return output_path


def retime_materials(
    materials: Sequence[MaterialInfo],
    narration: NarrationTiming,
    storage_local_dir: str | Path,
    *,
    shots: Sequence[Sequence[Shot]] | None = None,
    retime_id: str | None = None,
    tail_padding_s: float = DEFAULT_TAIL_PADDING_S,
) -> list[RetimedClip]:
    """섹션 순서 소재를 실측 길이에 맞춘 클립 목록으로 바꾼다.

    소재 개수와 실측 섹션 개수가 다르면 RetimeError (조용한 어긋남 금지).
    산출물은 storage_local_dir(= storage/local_videos) 안에 만든다 — 코어
    로컬 소재 보안 경로 규칙을 그대로 만족한다.

    `shots` 가 있으면 섹션 하나가 여러 컷으로 쪼개진다 (템플릿 v2
    `section.shots`): 같은 소재에서 와이드/컷인 파생 클립을 만들고 섹션
    실측 길이를 컷 수만큼 균등 분배한다 — 소재 한 장으로도 컷 변화를 준다.

    마지막 컷에는 `tail_padding_s` 만큼 여유를 붙인다 (코어가 영상 부족분을
    앞 클립 재사용으로 채우는 것을 막는다 — 위 상수 주석 참고).
    """
    if tail_padding_s < CORE_TAIL_MARGIN_S:
        raise RetimeError(
            f"tail_padding_s 는 코어 안전여유({CORE_TAIL_MARGIN_S}초) 이상이어야 "
            f"합니다: {tail_padding_s}"
        )
    if len(materials) != len(narration.sections):
        raise RetimeError(
            f"소재 {len(materials)}개와 실측 섹션 {len(narration.sections)}개가 "
            "일치하지 않습니다"
        )
    if shots is not None and len(shots) != len(narration.sections):
        raise RetimeError(
            f"샷 선언 {len(shots)}개와 실측 섹션 {len(narration.sections)}개가 "
            "일치하지 않습니다"
        )

    local_dir_real = os.path.realpath(str(storage_local_dir))
    os.makedirs(local_dir_real, exist_ok=True)
    if retime_id is None:
        retime_id = uuid.uuid4().hex[:8]

    plan: list[tuple[int, int, MaterialInfo, str, float, Shot | None]] = []
    for index, (material, section) in enumerate(zip(materials, narration.sections)):
        section_shots = list(shots[index]) if shots else []
        if not section_shots:
            plan.append((index, 0, material, section.role, section.measured_s, None))
            continue
        share = section.measured_s / len(section_shots)
        for shot_index, shot in enumerate(section_shots):
            plan.append((index, shot_index, material, section.role, share, shot))

    retimed: list[RetimedClip] = []
    last = len(plan) - 1
    for position, (index, shot_index, material, role, seconds, shot) in enumerate(plan):
        output_path = os.path.join(
            local_dir_real,
            f"retimed-{retime_id}-{position:02d}-{role}-{shot_index}.mp4",
        )
        clip_s = seconds + (tail_padding_s if position == last else 0.0)
        retime_material(material.url, clip_s, output_path, shot=shot)
        logger.info(
            f"retime[{retime_id}] section[{index}] shot[{shot_index}] {role}: "
            f"{os.path.basename(material.url)} -> {clip_s:g}초"
            + (f" ({shot.kind}/{shot.motion or 'static'})" if shot else "")
        )
        retimed.append(
            RetimedClip(
                material=MaterialInfo(
                    provider="local", url=output_path, duration=int(round(clip_s))
                ),
                seconds=round(clip_s, 3),
                section_index=index,
                shot_index=shot_index,
            )
        )
    return retimed
