"""품질 게이트 2층.

- 1층 technical_gate: 렌더 산출물을 ffprobe(subprocess)로 실측 검증.
  해상도(기본 1080x1920), duration 이 템플릿 total_duration_range±허용오차
  내, 비디오+오디오 스트림 존재.
- 2층 structural_gate: 스크립트 구조(hook/body/cta 섹션 존재)와 소재
  구성(브랜드 소재 >= 1)을 검증.

조용한 강등 금지 원칙: 검증 완화(fail -> warning 강등)가 일어나면 그
사유를 반드시 GateResult.warnings 에 기록한다. 기록 없는 완화는 없다.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Sequence

REQUIRED_ROLES = ("hook", "body", "cta")


@dataclass(frozen=True)
class GateResult:
    """게이트 판정 결과. failures 가 하나라도 있으면 passed=False."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TechnicalExpectation:
    """technical_gate 기대값.

    duration_range 는 템플릿 total_duration_range 를 그대로 넣는다.
    duration_tolerance_s 는 TTS 음성 길이 편차를 흡수하기 위한 허용오차로,
    범위 양끝에 각각 적용된다 (하한-오차 ~ 상한+오차).
    """

    duration_range: tuple[float, float]
    width: int = 1080
    height: int = 1920
    duration_tolerance_s: float = 1.0


def _ffprobe(video_path: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def technical_gate(video_path: str, expected: TechnicalExpectation) -> GateResult:
    """렌더 산출물을 ffprobe 로 실측 검증한다. 예외 대신 failures 로 수렴."""
    failures: list[str] = []

    if not video_path or not os.path.isfile(video_path):
        return GateResult(
            passed=False, failures=[f"영상 파일이 없습니다: {video_path}"]
        )

    try:
        probe = _ffprobe(video_path)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return GateResult(passed=False, failures=[f"ffprobe 실측 실패: {exc}"])

    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        failures.append("비디오 스트림이 없습니다")
    if not audio_streams:
        failures.append("오디오 스트림이 없습니다")

    if video_streams:
        width = int(video_streams[0].get("width") or 0)
        height = int(video_streams[0].get("height") or 0)
        if (width, height) != (expected.width, expected.height):
            failures.append(
                f"해상도 불일치: {width}x{height} "
                f"(기대: {expected.width}x{expected.height})"
            )

    duration_raw = (probe.get("format") or {}).get("duration")
    if duration_raw is None:
        failures.append("duration 을 읽을 수 없습니다")
    else:
        duration = float(duration_raw)
        lo, hi = expected.duration_range
        tol = expected.duration_tolerance_s
        if not (lo - tol <= duration <= hi + tol):
            failures.append(
                f"duration {duration:.2f}초가 허용 범위를 벗어납니다 "
                f"(기대: {lo:g}~{hi:g}초 ±{tol:g}초)"
            )

    return GateResult(passed=not failures, failures=failures, warnings=[])


def _section_role(section: object) -> str | None:
    role = getattr(section, "role", None)
    if role is None and isinstance(section, dict):
        role = section.get("role")
    return role


def structural_gate(
    script_sections: Sequence[object],
    video_materials: Sequence[object],
    used_brand_count: int,
    *,
    photo_warning: bool = False,
) -> GateResult:
    """스크립트 구조와 소재 구성을 검증한다.

    - hook/body/cta 섹션이 모두 존재해야 한다.
    - video_materials 는 비어 있으면 안 된다.
    - 브랜드 소재는 최소 1개. 0개인 경우 photo_warning 이 동반될 때만
      warning 으로 강등하며 (사유를 warnings 에 기록 — 조용한 강등 금지),
      photo_warning 이 없으면 fail.
    """
    failures: list[str] = []
    warnings: list[str] = []

    roles = {_section_role(section) for section in script_sections}
    for role in REQUIRED_ROLES:
        if role not in roles:
            failures.append(f"{role} 섹션이 없습니다")

    if not video_materials:
        failures.append("video_materials 가 비어 있습니다")

    if used_brand_count < 1:
        if photo_warning:
            warnings.append(
                "브랜드 소재 0개: photo_warning 동반으로 warning 강등 "
                "(스톡 소재만으로 구성됨 — 품질 경고 노출 필요)"
            )
        else:
            failures.append(
                "브랜드 소재가 0개입니다 (photo_warning 미동반 — 최소 1개 필요)"
            )

    return GateResult(passed=not failures, failures=failures, warnings=warnings)
