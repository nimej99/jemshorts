"""promo-shorts 승인 UI (Streamlit 추가 페이지 — 코어 Main.py 무수정).

플로우: 브랜드킷 -> 템플릿 -> 스크립트(LLM/수동) -> 플랜(승인 게이트)
-> 렌더 -> 업로드. 모든 동작은 app/promo/api.py 핸들러를 직접 호출한다
(HTTP 왕복 없이 단일 코드 경로 재사용 — API 테스트가 이 페이지의 로직
테스트를 겸한다).
"""

import os
import sys
import time

import streamlit as st

# 독립 엔트리 실행 대비: 프로젝트 루트를 모듈 경로에 추가 (Main.py 와 동일 규약)
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from fastapi import HTTPException  # noqa: E402

from app.promo import api as promo_api  # noqa: E402
from app.promo import db as promo_db  # noqa: E402
from app.promo import plans  # noqa: E402
from app.promo.brandkit import store as brandkit_store  # noqa: E402
from app.promo.brandkit.models import BrandKit  # noqa: E402

st.set_page_config(page_title="Promo Shorts", page_icon="🎬", layout="wide")
st.title("🎬 Promo Shorts — 홍보 쇼츠 파이프라인")


def _detail(exc: HTTPException) -> str:
    return str(exc.detail)


# ── 1. 브랜드킷 ──────────────────────────────────────────────────────
st.header("1. 브랜드킷")
conn = promo_db.connect()
try:
    kit = brandkit_store.load(conn)
finally:
    conn.close()

with st.form("brandkit_form"):
    business_name = st.text_input(
        "가게 이름", value=kit.business_name if kit else ""
    )
    category = st.text_input("업종", value=kit.category if kit else "")
    description = st.text_area("소개", value=kit.description if kit else "")
    photos_text = st.text_area(
        "소재 파일 경로 (줄바꿈 구분)",
        value="\n".join(kit.photos) if kit else "",
        help="가게 사진/영상 파일의 서버 경로. 렌더 시 storage/local_videos 로 복사됩니다.",
    )
    if st.form_submit_button("브랜드킷 저장"):
        photos = [line.strip() for line in photos_text.splitlines() if line.strip()]
        new_kit = BrandKit(
            business_name=business_name.strip(),
            category=category.strip(),
            description=description.strip(),
            photos=photos,
            source="manual",
        )
        conn = promo_db.connect()
        try:
            brandkit_store.save(conn, new_kit)
        finally:
            conn.close()
        st.success("브랜드킷 저장 완료")
        st.rerun()

if kit is None:
    st.warning("브랜드킷이 없습니다 — 위 폼으로 먼저 저장하세요.")
    st.stop()

# ── 2. 템플릿 ────────────────────────────────────────────────────────
st.header("2. 템플릿")
templates = promo_api.list_templates()["templates"]
template_labels = {
    t["template_id"]: f"{t['name']} ({t['mood']}, {'/'.join(t['sections'])})"
    for t in templates
}
template_id = st.selectbox(
    "템플릿 선택",
    options=list(template_labels),
    format_func=lambda tid: template_labels[tid],
)

# ── 3. 스크립트 ──────────────────────────────────────────────────────
st.header("3. 스크립트")
reference_url = st.text_input(
    "레퍼런스 쇼츠 URL (선택)", help="참고 영상 페이싱/훅을 실측해 프롬프트에 반영"
)
col_gen, col_prompt = st.columns(2)
with col_gen:
    if st.button("LLM 으로 스크립트 생성"):
        try:
            with st.spinner("스크립트 생성 중..."):
                result = promo_api.generate_script(
                    promo_api.ScriptPromptRequest(
                        template_id=template_id,
                        reference_url=reference_url or None,
                    )
                )
            st.session_state["promo_script"] = result["script"]
        except HTTPException as exc:
            st.error(_detail(exc))
with col_prompt:
    if st.button("프롬프트만 보기"):
        try:
            result = promo_api.script_prompt(
                promo_api.ScriptPromptRequest(
                    template_id=template_id, reference_url=reference_url or None
                )
            )
            st.code(result["prompt"])
        except HTTPException as exc:
            st.error(_detail(exc))

script = st.text_area(
    "스크립트 (직접 수정 가능)",
    value=st.session_state.get("promo_script", ""),
    height=140,
)

# ── 4. 플랜 (승인 게이트) ────────────────────────────────────────────
st.header("4. 플랜 — 승인 게이트")
if st.button("플랜 생성", type="primary", disabled=not script.strip()):
    try:
        plan = promo_api.create_plan(
            promo_api.PlanCreateRequest(template_id=template_id, script=script)
        )
        st.session_state["promo_plan_id"] = plan["plan_id"]
    except HTTPException as exc:
        st.error(_detail(exc))

plan_id = st.session_state.get("promo_plan_id")
if plan_id:
    try:
        plan = promo_api.get_plan(plan_id)
    except HTTPException as exc:
        st.error(_detail(exc))
        st.stop()

    st.subheader(f"플랜 {plan['plan_id']} — {plan['status']}")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("브랜드 소재", plan["used_brand_count"])
    col_b.metric("소재 수", len(plan["materials"]))
    col_c.metric(
        "구조 게이트", "통과" if plan["structural_gate"]["passed"] else "실패"
    )
    if plan["photo_warning"]:
        st.warning("브랜드 소재 부족 — 스톡/대체 소재 비중이 높습니다")
    for failure in plan["structural_gate"]["failures"]:
        st.error(f"구조 게이트: {failure}")
    st.caption("소재 배치: " + " → ".join(plan["materials"]))

    # ── 5. 렌더 ──────────────────────────────────────────────────────
    st.header("5. 렌더")
    if plan["status"] == plans.STATUS_PLANNED or plan["status"] == plans.STATUS_FAILED:
        if plan["status"] == plans.STATUS_FAILED:
            st.error(f"이전 렌더 실패: {plan.get('result', {}).get('error', '')}")
        if st.button("렌더 시작", disabled=not plan["approved_ready"]):
            try:
                promo_api.render_plan(plan_id, promo_api.RenderRequest())
                st.rerun()
            except HTTPException as exc:
                st.error(_detail(exc))
    elif plan["status"] == plans.STATUS_RENDERING:
        state = plan.get("task_state", {})
        st.progress(int(state.get("progress", 0)), text="렌더링 중...")
        time.sleep(2)
        st.rerun()
    elif plan["status"] == plans.STATUS_RENDERED:
        result = plan["result"]
        gate = result["technical_gate"]
        if gate["passed"]:
            st.success(f"렌더 완료 ({result['render_seconds']}초) — 기술 게이트 통과")
        else:
            st.error(f"기술 게이트 실패: {gate['failures']}")
        video_path = result["videos"][0]
        if os.path.exists(video_path):
            st.video(video_path)

        # ── 6. 업로드 ────────────────────────────────────────────────
        st.header("6. 업로드")
        if st.button("YouTube 업로드"):
            try:
                upload = promo_api.upload_plan(plan_id, promo_api.UploadRequest())
                st.success(
                    f"업로드 완료 — 오늘 {upload['uploads_today']}/{upload['daily_cap']}건"
                )
            except HTTPException as exc:
                st.error(_detail(exc))
