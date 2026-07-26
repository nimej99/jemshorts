"""내레이션 실측 타이밍 (템플릿 v2 `timing.owner = "narration"`).

docs/TEMPLATE_V2_DESIGN.md §6 (b) 의 앞단: 스크립트를 섹션별 문장으로 나누고
문장 낭독 길이를 **실측**해서 섹션 경계를 다시 계산한다. 템플릿의
`duration_s` 는 목표값이고, owner=narration 이면 실측이 타임라인의 주인이다.

실측 함수는 주입 가능하다 (`MeasureFn`). 기본 구현은 MPT 코어 TTS 를 그대로
쓰되(`tts_measurer`), 코어 임포트는 지연 로딩해 단위 테스트가 moviepy/edge-tts
의존을 끌어오지 않게 한다.

경계는 0 에서 누적으로 계산하므로 섹션 간 공백/겹침이 구조적으로 발생하지
않는다 (Orkas 의 SCENE_GAP/SCENE_OVERLAP 검사가 우리 구조에서는 불필요).
남는 검증은 "실측 총 길이가 템플릿 허용 범위 안인가" 뿐이며 그 판정은
`app.promo.quality.gates.timeline_gate` 가 담당한다.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable, Sequence

from app.promo.templates.schema import Template

# 문장 텍스트 -> 낭독 초. 실패 시 예외를 던진다 (0 반환으로 조용히 넘어가지 않음).
MeasureFn = Callable[[str], float]

# 문장 분리: 종결 부호 뒤 공백 또는 개행. 한국어 스크립트 기준.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")


class TimingError(ValueError):
    """내레이션 타이밍 계산 실패 (문장 부족, 실측 실패 등)."""


@dataclass(frozen=True)
class SectionTiming:
    """실측으로 확정된 섹션 하나의 타임라인 조각."""

    role: str
    text: str
    target_s: float
    measured_s: float
    start_s: float

    @property
    def end_s(self) -> float:
        return round(self.start_s + self.measured_s, 3)

    @property
    def drift_s(self) -> float:
        """목표 대비 편차 (양수면 목표보다 길게 읽힘)."""
        return round(self.measured_s - self.target_s, 3)


@dataclass(frozen=True)
class NarrationTiming:
    """섹션별 실측 결과 + 총 길이."""

    sections: tuple[SectionTiming, ...]

    @property
    def total_s(self) -> float:
        return round(sum(section.measured_s for section in self.sections), 3)

    @property
    def boundaries(self) -> tuple[tuple[float, float], ...]:
        return tuple((section.start_s, section.end_s) for section in self.sections)


def split_sentences(script: str) -> list[str]:
    """스크립트를 문장 단위로 나눈다 (빈 조각 제거)."""
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(script or "") if part.strip()]


def allocate_sentences(sentences: Sequence[str], template: Template) -> list[list[str]]:
    """문장을 섹션 목표 길이 비율대로 배분한다 (섹션당 최소 1문장).

    글자수 비율 기준 그리디 배분. 남은 문장 수가 남은 섹션 수와 같아지면
    즉시 다음 섹션으로 넘겨 빈 섹션이 생기지 않게 한다.
    """
    sections = template.structure
    if len(sentences) < len(sections):
        raise TimingError(
            f"문장 수({len(sentences)})가 섹션 수({len(sections)})보다 적습니다 "
            "— 섹션마다 최소 한 문장이 필요합니다"
        )

    total_chars = sum(len(s) for s in sentences)
    total_target = template.total_duration_s
    buckets: list[list[str]] = [[] for _ in sections]

    index = 0
    for position, section in enumerate(sections):
        remaining_sections = len(sections) - position - 1
        quota = total_chars * (section.duration_s / total_target)
        taken = 0
        while index < len(sentences):
            # 뒤 섹션에 최소 1문장씩 남겨둔다.
            if len(sentences) - index <= remaining_sections:
                break
            buckets[position].append(sentences[index])
            taken += len(sentences[index])
            index += 1
            if taken >= quota and remaining_sections:
                break
    # 마지막 섹션이 남은 문장을 모두 흡수한다.
    buckets[-1].extend(sentences[index:])
    return buckets


def measure_narration(
    script: str, template: Template, measure: MeasureFn
) -> NarrationTiming:
    """섹션별 텍스트를 실측해 누적 경계를 가진 NarrationTiming 을 만든다."""
    sentences = split_sentences(script)
    if not sentences:
        raise TimingError("스크립트가 비어 있습니다")

    buckets = allocate_sentences(sentences, template)
    timings: list[SectionTiming] = []
    cursor = 0.0
    for section, bucket in zip(template.structure, buckets):
        text = " ".join(bucket)
        measured = float(measure(text))
        if measured <= 0:
            raise TimingError(f"{section.role} 섹션 낭독 실측이 0 이하입니다: {measured}")
        timings.append(
            SectionTiming(
                role=section.role,
                text=text,
                target_s=section.duration_s,
                measured_s=round(measured, 3),
                start_s=round(cursor, 3),
            )
        )
        cursor += measured
    return NarrationTiming(sections=tuple(timings))


def tts_measurer(voice_name: str, voice_rate: float = 1.0) -> MeasureFn:
    """MPT 코어 TTS 로 문장 낭독 길이를 실측하는 MeasureFn 을 만든다.

    측정용 오디오는 임시 디렉터리에 쓰고 즉시 지운다. 코어 voice 모듈은
    호출 시점에 지연 임포트한다 (플랜 단위 테스트가 TTS 스택을 로드하지
    않도록).
    """

    def _measure(text: str) -> float:
        from app.services import voice as voice_service

        with tempfile.TemporaryDirectory(prefix="promo-timing-") as tmp_dir:
            audio_file = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.mp3")
            sub_maker = voice_service.tts(
                text=text,
                voice_name=voice_service.parse_voice_name(voice_name),
                voice_rate=voice_rate,
                voice_file=audio_file,
            )
            if sub_maker is None:
                raise TimingError(f"TTS 실측 실패 (음성/네트워크 확인): {text[:20]}...")
            duration = float(voice_service.get_audio_duration(sub_maker))
        if duration <= 0:
            raise TimingError(f"TTS 실측 길이가 0입니다: {text[:20]}...")
        return duration

    return _measure
