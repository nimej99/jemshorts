"""promo API 라우터: plan(승인 게이트) / render / 리서치.

경로: /api/v1/promo/*  (코어 v1 라우터와 분리된 promo 전용 라우터.
app/router.py 에서 include — 코어 수정 2줄, docs/FORK_NOTES.md 등재)

플로우:
1. GET  /templates                — 사용 가능한 템플릿 목록
2. POST /plans                    — 소재 합성 + 사전 구조 게이트 -> 플랜 저장
3. POST /plans/{plan_id}/render   — 승인된 플랜을 백그라운드 렌더로 실행
4. GET  /plans/{plan_id}          — 플랜 상태 + 렌더 진행/결과 조회
5. POST /research/reference       — 참고 쇼츠 자막 실측 (yt-dlp)
6. POST /script-prompt            — 템플릿+브랜드킷(+레퍼런스) 스크립트 프롬프트

렌더는 스레드로 실행하고 진행률은 코어 task state 를 그대로 읽는다.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.promo import db as promo_db
from app.promo import pipeline, plans, uploads
from app.promo.brandkit import store as brandkit_store
from app.promo.research import (
    ResearchToolMissingError,
    analyze_reference,
    build_script_prompt,
    fetch_subtitles,
    parse_vtt,
)
from app.promo.templates.schema import (
    TemplateValidationError,
    validate_template,
)
from app.utils import utils

router = APIRouter(prefix="/api/v1/promo", tags=["Promo"])


def templates_data_dir() -> str:
    return os.path.join(utils.root_dir(), "templates-data")


def _load_raw_templates() -> list[dict]:
    """templates-data/*.json 원본 dict 목록 (검증 통과분만, 파일명 순)."""
    directory = templates_data_dir()
    if not os.path.isdir(directory):
        raise HTTPException(status_code=500, detail="templates-data 디렉터리가 없습니다")
    result = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "index.json":
            continue
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        validate_template(data, source=name)  # 위반 시 아래에서 500 으로 수렴
        result.append(data)
    return result


def _find_raw_template(template_id: str) -> dict:
    for data in _load_raw_templates():
        if data.get("template_id") == template_id:
            return data
    raise HTTPException(
        status_code=404, detail=f"템플릿을 찾을 수 없습니다: {template_id}"
    )


def _load_brandkit():
    conn = promo_db.connect()
    try:
        kit = brandkit_store.load(conn)
    finally:
        conn.close()
    if kit is None:
        raise HTTPException(
            status_code=409,
            detail="브랜드킷이 없습니다: 먼저 브랜드킷을 저장하세요",
        )
    return kit


class PlanCreateRequest(BaseModel):
    template_id: str
    script: str
    stock_paths: list[str] = Field(default_factory=list)
    voice_name: str = pipeline.DEFAULT_VOICE_NAME
    font_name: str = pipeline.DEFAULT_FONT_NAME


class RenderRequest(BaseModel):
    force: bool = False


class ReferenceRequest(BaseModel):
    url: str


class ScriptPromptRequest(BaseModel):
    template_id: str
    reference_url: str | None = None


class UploadRequest(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    title: str | None = None
    description: str | None = None
    privacy_status: str = "public"


def _gate_dict(gate) -> dict:
    return {
        "passed": gate.passed,
        "failures": list(gate.failures),
        "warnings": list(gate.warnings),
    }


def _plan_summary(plan: pipeline.RenderPlan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "template_id": plan.template.template_id,
        "subject": plan.subject,
        "materials": [os.path.basename(m.url) for m in plan.materials],
        "used_brand_count": plan.used_brand_count,
        "photo_warning": plan.photo_warning,
        "structural_gate": _gate_dict(plan.structural),
        "approved_ready": plan.approved_ready,
    }


@router.get("/templates")
def list_templates():
    try:
        raws = _load_raw_templates()
    except (TemplateValidationError, json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"템플릿 로드 실패: {exc}")
    return {
        "templates": [
            {
                "template_id": raw["template_id"],
                "name": raw["name"],
                "mood": raw["mood"],
                "total_duration_range": raw["total_duration_range"],
                "sections": [s["role"] for s in raw["structure"]],
            }
            for raw in raws
        ]
    }


@router.post("/plans")
def create_plan(body: PlanCreateRequest):
    raw = _find_raw_template(body.template_id)
    template = validate_template(raw, source=body.template_id)
    kit = _load_brandkit()

    local_dir = utils.storage_dir("local_videos", create=True)
    try:
        plan = pipeline.plan_render(
            template,
            kit,
            body.stock_paths,
            body.script,
            local_dir,
            voice_name=body.voice_name,
            font_name=body.font_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    conn = promo_db.connect()
    try:
        plans.save_plan(conn, plan, raw)
    finally:
        conn.close()
    return _plan_summary(plan)


def _run_render(plan_id: str, task_id: str) -> None:
    """백그라운드 렌더 워커 (스레드 전용 커넥션 사용)."""
    conn = promo_db.connect()
    try:
        row = plans.get_row(conn, plan_id)
        plan = plans.restore_plan(row)
        result = pipeline.execute_render(plan, task_id=task_id)
        plans.finish(
            conn,
            plan_id,
            plans.STATUS_RENDERED,
            {
                "task_id": result.task_id,
                "videos": list(result.videos),
                "render_seconds": result.render_seconds,
                "technical_gate": _gate_dict(result.technical),
                "warnings": result.warnings,
            },
        )
    except Exception as exc:  # 워커 스레드 — 실패를 행에 기록 (조용한 실패 금지)
        logger.exception(f"render[{plan_id}] 실패")
        plans.finish(conn, plan_id, plans.STATUS_FAILED, {"error": str(exc)})
    finally:
        conn.close()


@router.post("/plans/{plan_id}/render", status_code=202)
def render_plan(plan_id: str, body: RenderRequest | None = None):
    body = body or RenderRequest()
    conn = promo_db.connect()
    try:
        row = plans.get_row(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"플랜이 없습니다: {plan_id}")
        plan = plans.restore_plan(row)
        if not plan.approved_ready and not body.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "구조 게이트 실패 플랜입니다 (force=true 로 강제 가능)",
                    "structural_gate": _gate_dict(plan.structural),
                },
            )
        task_id = f"promo-{plan_id}-{uuid.uuid4().hex[:6]}"
        if not plans.mark_rendering(conn, plan_id, task_id):
            raise HTTPException(status_code=409, detail="이미 렌더링 중입니다")
    finally:
        conn.close()

    thread = threading.Thread(
        target=_run_render, args=(plan_id, task_id), daemon=True
    )
    thread.start()
    return {"plan_id": plan_id, "task_id": task_id, "status": plans.STATUS_RENDERING}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    conn = promo_db.connect()
    try:
        row = plans.get_row(conn, plan_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"플랜이 없습니다: {plan_id}")

    plan = plans.restore_plan(row)
    response = _plan_summary(plan)
    response["status"] = row["status"]
    response["task_id"] = row["task_id"]
    if row["result_json"]:
        response["result"] = json.loads(row["result_json"])
    if row["status"] == plans.STATUS_RENDERING and row["task_id"]:
        from app.services import state as sm  # 지연 임포트 (테스트 경량화)

        response["task_state"] = sm.state.get_task(row["task_id"]) or {}
    return response


@router.post("/research/reference")
def research_reference(body: ReferenceRequest):
    try:
        vtt = fetch_subtitles(body.url)
    except ResearchToolMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if vtt is None:
        raise HTTPException(
            status_code=502, detail="자막을 가져오지 못했습니다 (자막 없음/수집 실패)"
        )
    cues = parse_vtt(vtt)
    if not cues:
        raise HTTPException(status_code=502, detail="자막이 비어 있습니다")
    stats = analyze_reference(cues)
    return {
        "url": body.url,
        "duration_s": stats.duration_s,
        "hook_text": stats.hook_text,
        "total_chars": stats.total_chars,
        "chars_per_sec": stats.chars_per_sec,
        "cue_count": stats.cue_count,
    }


def _resolve_script_prompt(body: ScriptPromptRequest) -> tuple[str, bool]:
    """템플릿+브랜드킷(+레퍼런스 실측)으로 프롬프트를 만든다.

    반환: (prompt, reference_used). 레퍼런스 URL 이 주어졌는데 실측에
    실패하면 502 (조용한 강등 금지 — 레퍼런스 없이 진행하려면 URL 을 빼라).
    """
    raw = _find_raw_template(body.template_id)
    template = validate_template(raw, source=body.template_id)
    kit = _load_brandkit()

    reference = None
    if body.reference_url:
        try:
            vtt = fetch_subtitles(body.reference_url)
        except ResearchToolMissingError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if vtt:
            cues = parse_vtt(vtt)
            if cues:
                reference = analyze_reference(cues)
        if reference is None:
            raise HTTPException(
                status_code=502,
                detail="레퍼런스 자막을 가져오지 못했습니다 (URL 확인)",
            )

    return build_script_prompt(template, kit, reference), reference is not None


@router.post("/script-prompt")
def script_prompt(body: ScriptPromptRequest):
    prompt, reference_used = _resolve_script_prompt(body)
    return {
        "template_id": body.template_id,
        "prompt": prompt,
        "reference_used": reference_used,
    }


@router.post("/scripts")
def generate_script(body: ScriptPromptRequest):
    """프롬프트를 코어 LLM 으로 실행해 스크립트를 생성한다.

    코어 `llm._generate_response` 는 실패를 "Error: ..." 문자열로
    반환하므로 여기서 502 로 승격한다 (조용한 오류 문자열 전파 금지).
    """
    prompt, reference_used = _resolve_script_prompt(body)

    from app.services import llm  # 지연 임포트 (LLM SDK 로드 비용)

    response = llm._generate_response(prompt)
    script = (response or "").strip()
    if not script or script.startswith("Error:"):
        raise HTTPException(
            status_code=502,
            detail=f"스크립트 생성 실패: {script or '빈 응답'}",
        )
    return {
        "template_id": body.template_id,
        "script": script,
        "reference_used": reference_used,
    }


@router.post("/plans/{plan_id}/upload")
def upload_plan(plan_id: str, body: UploadRequest | None = None):
    """렌더 완료된 플랜의 산출물을 업로드한다 (일일 상한 강제).

    업로드는 코어 upload_post(upload-post.com) 서비스를 경유한다.
    성공 시 videos 테이블에 delivered 로 기록 — 이 기록이 상한의 근거다.
    """
    body = body or UploadRequest()
    conn = promo_db.connect()
    try:
        row = plans.get_row(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"플랜이 없습니다: {plan_id}")
        if row["status"] != plans.STATUS_RENDERED:
            raise HTTPException(
                status_code=409,
                detail=f"렌더 완료 플랜만 업로드할 수 있습니다 (현재: {row['status']})",
            )
        result = json.loads(row["result_json"] or "{}")
        videos = result.get("videos") or []
        if not videos or not os.path.exists(videos[0]):
            raise HTTPException(
                status_code=409, detail="렌더 산출물 파일을 찾을 수 없습니다"
            )
        if uploads.cap_reached(conn):
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "일일 업로드 상한에 도달했습니다",
                    "uploads_today": uploads.count_delivered_today(conn),
                    "daily_cap": uploads.daily_cap(),
                },
            )

        plan = plans.restore_plan(row)
        hashtags = [f"#{tag}" for tag in plan.template.hashtags_base]
        title = body.title or plan.subject
        description = body.description or f"{plan.subject}\n\n{' '.join(hashtags)}"

        from app.services import upload_post  # 지연 임포트

        upload_result = upload_post.cross_post_video(
            videos[0],
            title,
            platforms=body.platforms,
            youtube_extra={
                "youtube_title": title,
                "youtube_description": description,
                "tags": [tag.lstrip("#") for tag in hashtags],
                "privacyStatus": body.privacy_status,
            },
        )
        if not upload_result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"업로드 실패: {upload_result.get('error') or upload_result}",
            )

        video_id = row["task_id"] or f"promo-{plan_id}"
        uploads.record_delivered(
            conn, video_id, plan.template.template_id, description, hashtags
        )
        return {
            "plan_id": plan_id,
            "video_id": video_id,
            "platforms": body.platforms,
            "request_id": upload_result.get("request_id"),
            "uploads_today": uploads.count_delivered_today(conn),
            "daily_cap": uploads.daily_cap(),
        }
    finally:
        conn.close()
