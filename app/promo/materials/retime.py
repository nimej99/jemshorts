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
from pathlib import Path
from typing import Sequence

from loguru import logger

from app.models import const
from app.models.schema import MaterialInfo
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

_PHOTO_EXTS = frozenset(const.FILE_TYPE_IMAGES)
_VIDEO_EXTS = frozenset(const.FILE_TYPE_VIDEOS)


class RetimeError(RuntimeError):
    """소재 리타이밍 실패 (ffmpeg 실패, 지원하지 않는 소재 등)."""


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


def retime_material(source_path: str, target_s: float, output_path: str) -> str:
    """소재 하나를 target_s 길이의 무음 클립으로 다시 만든다."""
    if target_s <= 0:
        raise RetimeError(f"목표 길이는 0보다 커야 합니다: {target_s}")

    kind = _material_kind(source_path)
    if kind == "photo":
        args = ["-loop", "1", "-i", source_path, *_encode_args(target_s, output_path)]
    elif kind == "video":
        source_duration = probe_duration(source_path)
        if source_duration + 0.05 < target_s:
            # 소재가 짧으면 루프해서 목표 길이를 채운다 (코어 루프에 맡기면
            # 섹션 경계가 아니라 오디오 전체 길이 기준으로 채워진다).
            args = ["-stream_loop", "-1", "-i", source_path]
        else:
            args = ["-i", source_path]
        args += _encode_args(target_s, output_path)
    else:
        raise RetimeError(f"지원하지 않는 소재 형식입니다: {source_path}")

    _run_ffmpeg(args, source_path)
    if not os.path.isfile(output_path):
        raise RetimeError(f"리타이밍 산출물이 없습니다: {output_path}")
    return output_path


def retime_materials(
    materials: Sequence[MaterialInfo],
    narration: NarrationTiming,
    storage_local_dir: str | Path,
    *,
    retime_id: str | None = None,
    tail_padding_s: float = DEFAULT_TAIL_PADDING_S,
) -> list[MaterialInfo]:
    """섹션 순서 소재를 실측 길이에 맞춘 클립 목록으로 바꾼다.

    소재 개수와 실측 섹션 개수가 다르면 RetimeError (조용한 어긋남 금지).
    산출물은 storage_local_dir(= storage/local_videos) 안에 만든다 — 코어
    로컬 소재 보안 경로 규칙을 그대로 만족한다.

    마지막 섹션 클립에는 `tail_padding_s` 만큼 여유를 붙인다 (코어가 영상
    부족분을 앞 클립 재사용으로 채우는 것을 막는다 — 위 상수 주석 참고).
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

    local_dir_real = os.path.realpath(str(storage_local_dir))
    os.makedirs(local_dir_real, exist_ok=True)
    if retime_id is None:
        retime_id = uuid.uuid4().hex[:8]

    retimed: list[MaterialInfo] = []
    last_index = len(materials) - 1
    for index, (material, section) in enumerate(zip(materials, narration.sections)):
        output_path = os.path.join(
            local_dir_real, f"retimed-{retime_id}-{index:02d}-{section.role}.mp4"
        )
        clip_s = section.measured_s + (tail_padding_s if index == last_index else 0.0)
        retime_material(material.url, clip_s, output_path)
        logger.info(
            f"retime[{retime_id}] section[{index}] {section.role}: "
            f"{os.path.basename(material.url)} -> {clip_s:g}초"
            + (f" (실측 {section.measured_s:g}초 + 꼬리 여유)" if index == last_index else "")
        )
        retimed.append(
            MaterialInfo(
                provider="local",
                url=output_path,
                duration=int(round(clip_s)),
            )
        )
    return retimed
