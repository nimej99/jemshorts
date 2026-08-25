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

import math
import os
import random
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from loguru import logger

from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode, VideoParams
from app.promo.brandkit.models import BrandKit
from app.promo.materials import compose_materials, retime_materials
from app.promo.materials.retime import RetimedClip
from app.promo.quality import (
    GateResult,
    TechnicalExpectation,
    structural_gate,
    technical_gate,
    timeline_gate,
)
from app.promo.templates.schema import Template
from app.promo.timing import (
    MeasureFn,
    NarrationAudio,
    NarrationTiming,
    measure_narration,
    narrate_full_script,
)

# 한국어 기본값 (M0 에서 번들된 리소스 기준)
DEFAULT_VOICE_NAME = "ko-KR-SunHiNeural-Female"
DEFAULT_FONT_NAME = "NotoSansKR-Bold.otf"
DEFAULT_LANGUAGE = "ko-KR"
# M1 실측과 동일한 렌더 허용 오차 (오디오 길이에 따른 상하 여유)
DEFAULT_DURATION_TOLERANCE_S = 2.0
# 코어 combine_videos 의 클립 분할 단위 기본값 (M1 실측 구성과 동일)
DEFAULT_CLIP_DURATION_S = 4


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
    # 리타이밍된 클립별 정확한 길이(초). materials 와 같은 순서/개수.
    clip_seconds: tuple[float, ...] = ()
    # 실측에 쓴 전체 스크립트 오디오 (같은 프로세스에서 렌더로 이어질 때 재사용).
    # 영속화하지 않는다 — sub_maker 는 직렬화 대상이 아니다.
    narration_audio: NarrationAudio | None = None
    # 영상마다 다른 동적 값 (menu_name, event_name, ...). LLM/호출자가 제공하며
    # caption_template · headline 을 채우는 데 쓴다. 영속화된다.
    variables: dict = field(default_factory=dict)

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

    @property
    def clip_duration_s(self) -> int:
        """코어 `max_clip_duration`. 리타이밍된 클립 중 가장 긴 것에 맞춘다.

        클립이 이미 섹션(또는 컷) 길이이므로 이 값이 그보다 크거나 같아야
        코어가 클립을 더 쪼개지 않는다 (= 화면 전환이 섹션/컷 경계와 일치).
        마지막 클립의 꼬리 여유까지 이미 clip_seconds 에 반영돼 있다.
        """
        if not self.clip_seconds:
            return DEFAULT_CLIP_DURATION_S
        return max(DEFAULT_CLIP_DURATION_S, math.ceil(max(self.clip_seconds)))


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
    retime: Callable[..., list[RetimedClip]] | None = None,
    variables: dict | None = None,
) -> RenderPlan:
    """소재를 합성하고 사전 구조 게이트까지 판정한 RenderPlan 을 만든다.

    렌더는 하지 않는다. structural_gate 는 소재 배치만으로 판정 가능하므로
    이 단계에서 미리 실행해 승인 게이트에 함께 제시한다.
    스크립트가 비어 있으면 ValueError (조용한 빈 렌더 금지).

    템플릿이 v2 `timing.owner = "narration"` 을 선언하면 섹션별 내레이션을
    실측(기본: 코어 TTS, `measure` 로 주입 가능)해 타임라인 게이트까지
    판정하고, 섹션 소재를 실측 길이 클립으로 다시 만든다(`retime` 로 주입
    가능) — 렌더 비용을 쓰기 전에 "이 스크립트가 이 템플릿 길이에 맞는가"를
    확정한다. 선언이 없으면 실측도 리타이밍도 하지 않는다(v1 동작 그대로).
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
    narration_audio = None
    clip_seconds: tuple[float, ...] = ()
    timeline = None
    if template.timing is not None and template.timing.owner == "narration":
        voice_rate = template.voice.speed if template.voice else 1.0
        if measure is not None:
            # 주입된 실측 함수 (테스트/대체 TTS 경로) — 섹션별로 잰다.
            narration = measure_narration(script, template, measure)
        else:
            # 기본 경로: 전체 스크립트를 한 번만 합성해 경계를 끊고, 그 오디오를
            # 렌더에 재사용한다 (TTS 호출 1회 + 실측 합계 = 실제 렌더 오디오 길이).
            narration, narration_audio = narrate_full_script(
                script,
                template,
                voice_name=voice_name,
                voice_rate=voice_rate,
                audio_dir=narration_audio_dir(),
            )
        timeline = timeline_gate(narration, template)
        if not timeline.passed:
            logger.warning(f"plan[{plan_id}] 타임라인 게이트 실패: {timeline.failures}")
        # 실측 경계를 화면에 반영한다: 섹션 소재를 실측 길이 클립으로 다시 만들고,
        # 섹션이 shots 를 선언했으면 같은 소재에서 와이드/컷인 파생 클립을 만든다.
        # 승인 게이트에 제시되는 소재 = 실제 렌더되는 소재를 유지하기 위해
        # 렌더 시점이 아니라 플랜 시점에 만든다.
        retime_fn = retime or retime_materials
        clips = retime_fn(
            materials,
            narration,
            storage_local_dir,
            shots=[section.shots for section in template.structure],
            headlines=headline_texts(template, brandkit, variables),
            retime_id=plan_id,
        )
        materials = [clip.material for clip in clips]
        clip_seconds = tuple(clip.seconds for clip in clips)

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
        narration_audio=narration_audio,
        clip_seconds=clip_seconds,
        timeline=timeline,
        variables=dict(variables or {}),
    )



def narration_audio_dir() -> str:
    """실측 내레이션 오디오를 두는 디렉터리 (storage/promo_narration)."""
    from app.utils import utils

    return utils.storage_dir("promo_narration", create=True)


def build_voice_preview(plan: RenderPlan, task_id: str) -> dict | None:
    """플랜 실측 오디오를 코어 task 가 재사용할 수 있는 형태로 만든다.

    코어(`app/services/task.py:_resolve_reusable_voice_preview`)는 오디오가
    `utils.task_dir(task_id)` 안에 있고 문안/음성 파라미터가 일치할 때만
    재사용한다. 그래서 여기서 task 디렉터리로 복사한 뒤 sub_maker 와 함께
    넘긴다 — sub_maker 가 같이 가야 자막이 그대로 생성된다.

    재사용할 오디오가 없으면 None (코어가 평소대로 TTS 한다).
    """
    audio = plan.narration_audio
    if audio is None:
        return None

    from app.utils import utils

    if not os.path.isfile(audio.audio_file):
        logger.warning(
            f"plan[{plan.plan_id}] 실측 오디오가 없어 재사용을 건너뜁니다: "
            f"{audio.audio_file}"
        )
        return None

    destination = os.path.join(utils.task_dir(task_id), "audio.mp3")
    if os.path.realpath(destination) != os.path.realpath(audio.audio_file):
        shutil.copy2(audio.audio_file, destination)
    return {
        "script": plan.script.strip(),
        "voice_name": plan.voice_name,
        "voice_rate": float(plan.voice_rate),
        "voice_volume": 1.0,
        "audio_file": destination,
        "duration": float(audio.duration_s),
        "sub_maker": audio.sub_maker,
    }


def headline_texts(
    template: Template, brandkit: BrandKit, variables: dict | None = None
) -> list[str | None]:
    """섹션별 헤드라인 문구를 브랜드 정보 + 동적 변수로 채운다.

    show=false 이거나 **플레이스홀더를 다 채우지 못한** 섹션은 None(배너 생략)이다.
    영상에 `{event_name}` 같은 원문 배너가 박히면 안 되므로 — 자동 공개 영상에서는
    배너를 빼는 게 깨진 배너보다 낫다. 미충전 안내는 승인 UI 가 템플릿의
    required_variables 로 별도로 한다(조용한 빈칸 금지 원칙은 그쪽에서 지킨다).
    """
    from app.promo.materials.headline import format_headline
    from app.promo.templates.variables import placeholder_names, template_context

    context = template_context(template, brandkit, variables)
    texts: list[str | None] = []
    for section in template.structure:
        headline = section.headline
        if headline is None or not headline.show:
            texts.append(None)
            continue
        unfilled = [
            name
            for name in placeholder_names(headline.template)
            if not str(context.get(name, "")).strip()
        ]
        if unfilled:
            texts.append(None)  # 원문 배너 금지 — 이번 렌더에서는 배너 생략
            continue
        texts.append(format_headline(headline.template, context))
    return texts


def upload_caption(plan: "RenderPlan", brandkit: BrandKit) -> str:
    """업로드 설명 본문. `caption_template` 을 채우되 못 채운 값이 있으면
    `plan.subject` 로 폴백한다.

    자동(스케줄러)으로 공개되는 캡션에 `{menu_name}` 같은 원문이 찍히면
    안 되므로 unfilled 가 하나라도 있으면 안전한 subject 로 물러선다.
    브랜드킷 홍보 링크(예약/쇼핑몰/제휴)는 본문 뒤에 붙는다 — 조회를
    수익 전환으로 잇는 퍼널이며, 폴백 시에도 함께 붙는다.
    """
    from app.promo.templates.variables import render_caption

    caption, unfilled = render_caption(plan.template, brandkit, plan.variables)
    body = plan.subject if unfilled else caption
    links = "\n".join(link.caption_line() for link in brandkit.promotion_links)
    return f"{body}\n\n{links}" if links else body


def list_bgm_for_mood(mood: str) -> list[str]:
    """템플릿 무드에 맞는 BGM 파일명 목록 (resource/songs 의 `{mood}-` 프리픽스).

    트랙 팩은 docs/TRACK_PACK.md 규약대로 `{mood}-{이름}.mp3` 로 정리돼 있다.
    무드별로 골라 영상 톤과 음악이 어긋나지 않게 한다 (energetic 영상에
    잔잔한 피아노가 깔리는 식의 부조화 방지).
    """
    from app.utils import utils

    song_dir = utils.song_dir()
    if not mood or not os.path.isdir(song_dir):
        return []
    prefix = f"{mood.lower()}-"
    return sorted(
        name
        for name in os.listdir(song_dir)
        if name.lower().startswith(prefix) and name.lower().endswith(".mp3")
    )


def pick_bgm_file(mood: str) -> str:
    """무드에 맞는 BGM 파일명을 무작위 선택. 없으면 빈 문자열(랜덤 폴백).

    코어 `get_bgm_file(bgm_file=...)` 가 파일명을 resource/songs 로 해석하므로
    파일명만 넘기면 된다 (코어 무접촉). 빈 문자열이면 코어가 전체에서 랜덤 선택.
    """
    matches = list_bgm_for_mood(mood)
    return random.choice(matches) if matches else ""


def build_video_params(plan: RenderPlan, *, n_threads: int = 1) -> VideoParams:
    """RenderPlan 을 MPT 렌더 입력(VideoParams)으로 변환한다.

    값 정책은 M1 실측 구성(scripts/m1_render_check.py)과 동일하되, BGM 은
    템플릿 무드에 맞는 트랙을 고른다 (없으면 전체 랜덤 폴백).
    """
    return VideoParams(
        video_subject=plan.subject,
        video_script=plan.script,
        video_aspect=VideoAspect.portrait,
        video_concat_mode=VideoConcatMode.sequential,
        video_clip_duration=plan.clip_duration_s,
        video_count=1,
        video_source="local",
        video_materials=list(plan.materials),
        video_language=plan.language,
        voice_name=plan.voice_name,
        voice_rate=plan.voice_rate,
        bgm_type="random",
        bgm_file=pick_bgm_file(plan.template.mood),
        bgm_volume=0.2,
        subtitle_enabled=True,
        # Shorts 하단에는 제목/채널/음원 UI 가 겹친다. 코어의 bottom 배치는
        # 렌더 파일에서는 보여도 실제 앱에서 가려지므로 세로 62% 안전영역 사용.
        subtitle_position="custom",
        custom_position=62.0,
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
    voice_preview = build_voice_preview(plan, task_id)
    started_at = time.monotonic()
    if voice_preview is None:
        result = task_runner(task_id, params, stop_at="video")
    else:
        # 플랜 실측 때 만든 오디오를 그대로 넘긴다 — TTS 재호출도, 실측과
        # 렌더 오디오 길이가 어긋날 일도 없다.
        logger.info(
            f"render[{task_id}] 실측 오디오 재사용 "
            f"({voice_preview['duration']:g}초, TTS 재합성 생략)"
        )
        result = task_runner(
            task_id, params, stop_at="video", voice_preview=voice_preview
        )
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
