"""품질 게이트 3층.

- 1층 technical_gate: 렌더 산출물을 ffprobe(subprocess)로 실측 검증.
  해상도(기본 1080x1920), duration 이 템플릿 total_duration_range±허용오차
  내, 비디오+오디오 스트림 존재.
- 2층 structural_gate: 스크립트 구조(필수 역할 섹션 존재 — 계약은
  templates.schema.REQUIRED_ROLES 단일 상수)와 소재 구성(브랜드 소재 >= 1)을
  검증.
- 3층 timeline_gate: 템플릿 v2 `timing.owner = "narration"` 에서 실측된
  내레이션 타임라인이 템플릿 허용 길이 범위 안인지 검증
  (docs/TEMPLATE_V2_DESIGN.md §5).

failures/warnings 문자열은 `CODE: 사람이 읽는 사유` 형식이다. 코드는 기계
판독용(운영 집계/자동 재시도 판단)이고 뒤 문장은 그대로 사용자에게 보여준다
(Orkas CompositionManifest 검증기의 오류 코드 체계 차용).

조용한 강등 금지 원칙: 검증 완화(fail -> warning 강등)가 일어나면 그
사유를 반드시 GateResult.warnings 에 기록한다. 기록 없는 완화는 없다.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Sequence

from app.promo.templates.schema import DEFAULT_TIMING_TOLERANCE_S, REQUIRED_ROLES

_FFPROBE_TIMEOUT_S = 30

# --- 기계 판독 코드 (failures/warnings 접두) ---
CODE_FILE_MISSING = "FILE_MISSING"
CODE_PROBE_FAILED = "PROBE_FAILED"
CODE_VIDEO_STREAM_MISSING = "VIDEO_STREAM_MISSING"
CODE_AUDIO_STREAM_MISSING = "AUDIO_STREAM_MISSING"
CODE_RESOLUTION_MISMATCH = "RESOLUTION_MISMATCH"
CODE_DURATION_UNREADABLE = "DURATION_UNREADABLE"
CODE_DURATION_OUT_OF_RANGE = "DURATION_OUT_OF_RANGE"
CODE_SECTION_ROLE_MISSING = "SECTION_ROLE_MISSING"
CODE_MATERIALS_EMPTY = "MATERIALS_EMPTY"
CODE_BRAND_MATERIAL_MISSING = "BRAND_MATERIAL_MISSING"
CODE_BRAND_MATERIAL_DOWNGRADED = "BRAND_MATERIAL_DOWNGRADED"
CODE_TIMELINE_COVERAGE_MISMATCH = "TIMELINE_COVERAGE_MISMATCH"
CODE_SECTION_DURATION_DRIFT = "SECTION_DURATION_DRIFT"

# 섹션 목표 대비 실측 편차 경고 기준 (비율) — 허용오차보다 큰 편차만 본다.
_SECTION_DRIFT_RATIO = 0.25


def _coded(code: str, message: str) -> str:
    return f"{code}: {message}"


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
        timeout=_FFPROBE_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def technical_gate(video_path: str, expected: TechnicalExpectation) -> GateResult:
    """렌더 산출물을 ffprobe 로 실측 검증한다. 예외 대신 failures 로 수렴."""
    failures: list[str] = []

    if not video_path or not os.path.isfile(video_path):
        return GateResult(
            passed=False,
            failures=[_coded(CODE_FILE_MISSING, f"영상 파일이 없습니다: {video_path}")],
        )

    try:
        probe = _ffprobe(video_path)
    except subprocess.TimeoutExpired:
        return GateResult(
            passed=False,
            failures=[
                _coded(
                    CODE_PROBE_FAILED,
                    f"ffprobe 실측 실패: timeout {_FFPROBE_TIMEOUT_S}초 초과",
                )
            ],
        )
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return GateResult(
            passed=False,
            failures=[_coded(CODE_PROBE_FAILED, f"ffprobe 실측 실패: {exc}")],
        )

    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        failures.append(_coded(CODE_VIDEO_STREAM_MISSING, "비디오 스트림이 없습니다"))
    if not audio_streams:
        failures.append(_coded(CODE_AUDIO_STREAM_MISSING, "오디오 스트림이 없습니다"))

    if video_streams:
        width = int(video_streams[0].get("width") or 0)
        height = int(video_streams[0].get("height") or 0)
        if (width, height) != (expected.width, expected.height):
            failures.append(
                _coded(
                    CODE_RESOLUTION_MISMATCH,
                    f"해상도 불일치: {width}x{height} "
                    f"(기대: {expected.width}x{expected.height})",
                )
            )

    duration_raw = (probe.get("format") or {}).get("duration")
    if duration_raw is None:
        failures.append(_coded(CODE_DURATION_UNREADABLE, "duration 을 읽을 수 없습니다"))
    else:
        duration = float(duration_raw)
        lo, hi = expected.duration_range
        tol = expected.duration_tolerance_s
        if not (lo - tol <= duration <= hi + tol):
            failures.append(
                _coded(
                    CODE_DURATION_OUT_OF_RANGE,
                    f"duration {duration:.2f}초가 허용 범위를 벗어납니다 "
                    f"(기대: {lo:g}~{hi:g}초 ±{tol:g}초)",
                )
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

    - 필수 역할 섹션(templates.schema.REQUIRED_ROLES — hook, cta)이 모두
      존재해야 한다 (body 는 선택 — 스키마 계약과 동일).
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
            failures.append(_coded(CODE_SECTION_ROLE_MISSING, f"{role} 섹션이 없습니다"))

    if not video_materials:
        failures.append(_coded(CODE_MATERIALS_EMPTY, "video_materials 가 비어 있습니다"))

    if used_brand_count < 1:
        if photo_warning:
            warnings.append(
                _coded(
                    CODE_BRAND_MATERIAL_DOWNGRADED,
                    "브랜드 소재 0개: photo_warning 동반으로 warning 강등 "
                    "(스톡 소재만으로 구성됨 — 품질 경고 노출 필요)",
                )
            )
        else:
            failures.append(
                _coded(
                    CODE_BRAND_MATERIAL_MISSING,
                    "브랜드 소재가 0개입니다 (photo_warning 미동반 — 최소 1개 필요)",
                )
            )

    return GateResult(passed=not failures, failures=failures, warnings=warnings)


def timeline_gate(narration: object, template: object) -> GateResult:
    """실측 내레이션 타임라인을 템플릿 계약과 대조한다.

    - 총 길이가 `total_duration_range` ± `timing.tolerance_s` 를 벗어나면
      fail (TIMELINE_COVERAGE_MISMATCH) — 렌더 비용을 쓰기 전에 잡는다.
    - 섹션별 목표 대비 편차가 크면 warning (SECTION_DURATION_DRIFT).
      실측이 타임라인의 주인이므로 편차 자체는 실패가 아니다.

    섹션 경계는 0 부터 누적으로 계산되므로 공백/겹침은 구조적으로 발생하지
    않는다 (별도 GAP/OVERLAP 코드 불필요 — app/promo/timing.py 참고).
    """
    failures: list[str] = []
    warnings: list[str] = []

    timing = getattr(template, "timing", None)
    tolerance = timing.tolerance_s if timing is not None else DEFAULT_TIMING_TOLERANCE_S
    lo, hi = template.total_duration_range
    total = narration.total_s
    if not (lo - tolerance <= total <= hi + tolerance):
        failures.append(
            _coded(
                CODE_TIMELINE_COVERAGE_MISMATCH,
                f"내레이션 실측 총 길이 {total:g}초가 템플릿 허용 범위를 "
                f"벗어납니다 (기대: {lo:g}~{hi:g}초 ±{tolerance:g}초)",
            )
        )

    for section in narration.sections:
        allowed = max(tolerance, section.target_s * _SECTION_DRIFT_RATIO)
        if abs(section.drift_s) > allowed:
            warnings.append(
                _coded(
                    CODE_SECTION_DURATION_DRIFT,
                    f"{section.role} 섹션 실측 {section.measured_s:g}초가 목표 "
                    f"{section.target_s:g}초와 {section.drift_s:+g}초 차이납니다",
                )
            )

    return GateResult(passed=not failures, failures=failures, warnings=warnings)
