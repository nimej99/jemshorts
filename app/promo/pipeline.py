"""promo 파이프라인 오케스트레이터: plan(승인 게이트) -> render 2단계.

M1 검증 스크립트(scripts/m1_render_check.py)의 플로우를 제품 계층으로 옮긴 것.

- plan_render(): 템플릿 + 브랜드킷 + 스크립트로 소재 합성과 사전
  structural_gate 판정까지 수행한다. 렌더 비용을 지출하기 전에 호출자
  (API/webui)가 소재 배치·경고·게이트 판정을 확인하고 승인하는 게이트
  지점이다 (OpenMontage 의 storyboard 승인 게이트에서 차용한 2단계 구조.
  아이디어 차용이며 코드 차용 아님 — docs/FORK_NOTES.md 참고).
- execute_render(): 승인된 플랜으로 MPT 로컬 렌더를 실행하고
  technical_gate 로 산출물을 실측 검증한다.

MPT 코어는 수정하지 않는다. task_service/state 는 execute_render 내부에서
지연 임포트한다 (plan 단계 단위 테스트가 moviepy 등 무거운 코어 의존을
로드하지 않도록).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from loguru import logger

from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode, VideoParams
from app.promo.brandkit.models import BrandKit
from app.promo.materials import compose_materials
from app.promo.quality import (
    GateResult,
    TechnicalExpectation,
    structural_gate,
    technical_gate,
    timeline_gate,
)
from app.promo.templates.schema import Template
from app.promo.timing import MeasureFn, NarrationTiming, measure_narration, tts_measurer

# 한국어 기본값 (M0 에서 번들된 리소스 기준)
DEFAULT_VOICE_NAME = "ko-KR-SunHiNeural-Female"
DEFAULT_FONT_NAME = "NotoSansKR-Bold.otf"
DEFAULT_LANGUAGE = "ko-KR"
# M1 실측과 동일한 렌더 허용 오차 (오디오 길이에 따른 상하 여유)
DEFAULT_DURATION_TOLERANCE_S = 2.0


class PipelineError(RuntimeError):
    """파이프라인 실행 실패 (task 미완료/산출물 없음 등)."""


@dataclass(frozen=True)
class RenderPlan:
    """렌더 승인 게이트에 제시되는 플랜. execute_render 의 입력."""

    plan_id: str
    template: Template
    script: str
    materials: tuple[MaterialInfo, ...]
    used_brand_count: int
    photo_warning: bool
    structural: GateResult
    voice_name: str = DEFAULT_VOICE_NAME
    font_name: str = DEFAULT_FONT_NAME
    language: str = DEFAULT_LANGUAGE
    subject: str = ""
    narration: NarrationTiming | None = None
    timeline: GateResult | None = None

    @property
    def approved_ready(self) -> bool:
        """승인 게이트 통과 가능 상태 (구조 + 타임라인 게이트 통과 기준)."""
        return self.structural.passed and (
            self.timeline is None or self.timeline.passed
        )

    @property
    def voice_rate(self) -> float:
        """TTS 낭독 속도. 템플릿 v2 `voice.speed` 선언을 그대로 따른다.

        영속화하지 않는다 — 템플릿 스냅샷(payload)에서 매번 파생되므로
        복원된 플랜도 승인 시점과 같은 값을 갖는다.
        """
        return self.template.voice.speed if self.template.voice else 1.0


@dataclass(frozen=True)
class RenderResult:
    """렌더 실행 + 실측 게이트 결과."""

    task_id: str
    plan_id: str
    videos: tuple[str, ...]
    render_seconds: float
    technical: GateResult
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.technical.passed


def plan_render(
    template: Template,
    brandkit: BrandKit,
    stock_paths: Sequence[str],
    script: str,
    storage_local_dir: str,
    *,
    plan_id: str | None = None,
    voice_name: str = DEFAULT_VOICE_NAME,
    font_name: str = DEFAULT_FONT_NAME,
    language: str = DEFAULT_LANGUAGE,
    measure: MeasureFn | None = None,
) -> RenderPlan:
    """소재를 합성하고 사전 구조 게이트까지 판정한 RenderPlan 을 만든다.

    렌더는 하지 않는다. structural_gate 는 소재 배치만으로 판정 가능하므로
    이 단계에서 미리 실행해 승인 게이트에 함께 제시한다.
    스크립트가 비어 있으면 ValueError (조용한 빈 렌더 금지).

    템플릿이 v2 `timing.owner = "narration"` 을 선언하면 섹션별 내레이션을
    실측(기본: 코어 TTS, `measure` 로 주입 가능)해 타임라인 게이트까지
    판정한다 — 렌더 비용을 쓰기 전에 "이 스크립트가 이 템플릿 길이에
    맞는가"를 확정한다. 선언이 없으면 실측하지 않는다(v1 동작 그대로).
    """
    if not script or not script.strip():
        raise ValueError("script 가 비어 있습니다: 렌더 플랜을 만들 수 없습니다")

    if plan_id is None:
        plan_id = uuid.uuid4().hex[:8]

    materials, used_brand_count, photo_warning = compose_materials(
        template, brandkit, stock_paths, storage_local_dir, compose_id=plan_id
    )
    structural = structural_gate(
        template.structure,
        materials,
        used_brand_count,
        photo_warning=photo_warning,
    )
    if not structural.passed:
        logger.warning(f"plan[{plan_id}] 사전 구조 게이트 실패: {structural.failures}")

    narration = None
    timeline = None
    if template.timing is not None and template.timing.owner == "narration":
        if measure is None:
            measure = tts_measurer(
                voice_name, template.voice.speed if template.voice else 1.0
            )
        narration = measure_narration(script, template, measure)
        timeline = timeline_gate(narration, template)
        if not timeline.passed:
            logger.warning(
                f"plan[{plan_id}] 타임라인 게이트 실패: {timeline.failures}"
            )

    subject = f"{brandkit.business_name} — {template.name}"
    return RenderPlan(
        plan_id=plan_id,
        template=template,
        script=script,
        materials=tuple(materials),
        used_brand_count=used_brand_count,
        photo_warning=photo_warning,
        structural=structural,
        voice_name=voice_name,
        font_name=font_name,
        language=language,
        subject=subject,
        narration=narration,
        timeline=timeline,
    )


def build_video_params(plan: RenderPlan, *, n_threads: int = 1) -> VideoParams:
    """RenderPlan 을 MPT 렌더 입력(VideoParams)으로 변환한다.

    값 정책은 M1 실측 구성(scripts/m1_render_check.py)과 동일하다.
    """
    return VideoParams(
        video_subject=plan.subject,
        video_script=plan.script,
        video_aspect=VideoAspect.portrait,
        video_concat_mode=VideoConcatMode.sequential,
        video_clip_duration=4,
        video_count=1,
        video_source="local",
        video_materials=list(plan.materials),
        video_language=plan.language,
        voice_name=plan.voice_name,
        voice_rate=plan.voice_rate,
        bgm_type="random",
        bgm_volume=0.2,
        subtitle_enabled=True,
        font_name=plan.font_name,
        font_size=60,
        text_fore_color="#FFFFFF",
        stroke_color="#000000",
        stroke_width=1.5,
        n_threads=n_threads,
    )


def execute_render(
    plan: RenderPlan,
    *,
    task_id: str | None = None,
    n_threads: int = 1,
    duration_tolerance_s: float = DEFAULT_DURATION_TOLERANCE_S,
    task_runner: Callable[..., dict | None] | None = None,
    state_getter: Callable[[str], dict | None] | None = None,
) -> RenderResult:
    """플랜을 MPT task 로 렌더하고 technical_gate 실측까지 수행한다.

    - task 미완료/산출물 없음 -> PipelineError (조용한 실패 금지).
    - 게이트 실패는 예외가 아니라 RenderResult.technical 로 반환한다
      (재시도/반려 판단은 호출자 몫).
    - task_runner/state_getter 는 테스트 주입용. 기본값이면 MPT 코어를
      지연 임포트한다.
    """
    if task_runner is None or state_getter is None:
        from app.services import state as sm
        from app.services import task as task_service

        if task_runner is None:
            task_runner = task_service.start
        if state_getter is None:
            state_getter = sm.state.get_task

    from app.models import const

    if task_id is None:
        task_id = f"promo-{uuid.uuid4().hex[:8]}"

    params = build_video_params(plan, n_threads=n_threads)
    started_at = time.monotonic()
    result = task_runner(task_id, params, stop_at="video")
    render_seconds = time.monotonic() - started_at

    task_state = state_getter(task_id) or {}
    if task_state.get("state") != const.TASK_STATE_COMPLETE:
        raise PipelineError(
            f"렌더 task 미완료: task_id={task_id}, "
            f"state={task_state.get('state')}, error={task_state.get('error')}"
        )
    videos = tuple((result or {}).get("videos") or ())
    if not videos:
        raise PipelineError(f"렌더 산출물이 없습니다: task_id={task_id}, result={result}")

    technical = technical_gate(
        videos[0],
        TechnicalExpectation(
            duration_range=plan.template.total_duration_range,
            duration_tolerance_s=duration_tolerance_s,
        ),
    )
    warnings: list[str] = []
    if plan.photo_warning:
        warnings.append("브랜드 소재 부족 — 스톡/대체 소재 비중이 높습니다")
    if not technical.passed:
        logger.warning(
            f"render[{task_id}] technical_gate 실패: {technical.failures}"
        )

    return RenderResult(
        task_id=task_id,
        plan_id=plan.plan_id,
        videos=videos,
        render_seconds=round(render_seconds, 1),
        technical=technical,
        warnings=warnings,
    )
