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
from app.promo.brandkit.models import BrandKit, PromotionLink  # noqa: E402

st.set_page_config(page_title="Promo Shorts", page_icon="🎬", layout="wide")
st.title("🎬 Promo Shorts — 홍보 쇼츠 파이프라인")


def _detail(exc: HTTPException) -> str:
    return str(exc.detail)


def _render_narration_panel(narration: dict, timeline_gate: dict, plan: dict) -> None:
    """템플릿 v2 실측 타임라인을 승인 게이트에 표시한다.

    TTS 실측이 섹션 경계를 결정하므로, 승인자는 여기서 "이 스크립트가 이
    템플릿 길이에 맞는지"와 컷/헤드라인 배치를 렌더 전에 확인한다.
    """
    st.markdown("**내레이션 실측 타임라인** (TTS 실측이 섹션 경계를 결정)")
    for failure in timeline_gate["failures"]:
        st.error(f"타임라인 게이트: {failure}")
    for warning in timeline_gate["warnings"]:
        st.warning(f"타임라인 게이트: {warning}")

    headlines = plan.get("headlines") or []
    rows = []
    for index, section in enumerate(narration["sections"]):
        headline = headlines[index] if index < len(headlines) else None
        rows.append(
            {
                "섹션": section["role"],
                "헤드라인": headline or "—",
                "목표(초)": f"{section['target_s']:g}",
                "실측(초)": f"{section['measured_s']:g}",
                "편차(초)": f"{section['drift_s']:+g}",
                "구간(초)": f"{section['start_s']:g}–{section['end_s']:g}",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    clips = plan.get("clip_seconds") or []
    st.caption(
        f"실측 총 길이 {narration['total_s']:g}초 · 클립 {len(clips)}개 (컷 분할 포함)"
    )

# ── 0. 키워드 갭 선별 (커머스 추천) ─────────────────────────────
st.header("0. 키워드 갭 선별")
gap_keywords_text = st.text_area(
    "후보 키워드 (줄바꿈 구분)",
    help="키워드별 유튜브 공급(상위 영상 수/조회수)을 실측하고 수요 신호를 결합해 랭킹합니다. "
    "수요: 네이버 데이터랩(네이버 키 설정 시) > Google Trends 캐시. "
    "'수요 있는데 영상 적은' 커머스 상품을 고르세요.",
)
if st.button("갭 실측", disabled=not gap_keywords_text.strip()):
    gap_keywords = [
        line.strip() for line in gap_keywords_text.splitlines() if line.strip()
    ]
    try:
        gap_result = promo_api.research_gap(
            promo_api.GapRequest(keywords=gap_keywords)
        )
    except HTTPException as exc:
        st.error(_detail(exc))
    else:
        gap_rows = gap_result.get("results") or []
        if not gap_rows:
            st.warning("실측에 성공한 키워드가 없습니다.")
        else:
            st.dataframe(
                [
                    {
                        "키워드": row["keyword"],
                        "수요": row["demand"],
                        "영상 수": row["result_count"],
                        "상위 조회수 합": row["top_view_sum"],
                        "갭 점수": row["score"],
                    }
                    for row in gap_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

# ── 1. 브랜드킷 ──────────────────────────────────────────────────────
st.header("1. 브랜드킷")
conn = promo_db.connect()
try:
    kit = brandkit_store.load(conn)
finally:
    conn.close()

crawl_col, crawl_btn_col = st.columns([4, 1])
with crawl_col:
    crawl_url = st.text_input(
        "가게 홈페이지/인스타그램 URL 수집 (선택)",
        help="인스타그램 프로필은 Instaloader(오픈소스)로, 일반 URL 은 OG 메타로 "
        "비어 있는 필드(가게 이름/소개)만 채웁니다. 네이버 정보는 아래 지역검색 사용.",
    )
with crawl_btn_col:
    st.write("")
    st.write("")
    if st.button("크롤", disabled=not crawl_url.strip()):
        try:
            crawl = promo_api.crawl_brandkit(
                promo_api.BrandkitCrawlRequest(url=crawl_url.strip())
            )
            applied = ", ".join(crawl["applied_fields"]) or "없음 (이미 채워짐)"
            st.success(f"크롤 {crawl['status']} — 반영 필드: {applied}")
            st.rerun()
        except HTTPException as exc:
            st.error(_detail(exc))

naver_col, naver_btn_col = st.columns([4, 1])
with naver_col:
    naver_query = st.text_input(
        "네이버 지역검색 (선택)",
        help="네이버 공식 오픈API 로 상호를 검색해 카테고리/주소/전화를 채웁니다. "
        "비워두면 저장된 상호명으로 검색. config 에 naver_client_id/secret 필요.",
    )
with naver_btn_col:
    st.write("")
    st.write("")
    if st.button("지역검색"):
        try:
            found = promo_api.naver_local_brandkit(
                promo_api.NaverLocalRequest(query=naver_query or None)
            )
            applied = ", ".join(found["applied_fields"]) or "없음 (이미 채워짐)"
            st.success(
                f"'{found['top']['business_name']}' 적용 — 반영 필드: {applied}"
            )
            st.rerun()
        except HTTPException as exc:
            st.error(_detail(exc))

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
    links_text = st.text_area(
        "홍보 링크 (줄바꿈 구분, `라벨 | URL` 또는 URL 만)",
        value="\n".join(
            f"{link.label} | {link.url}" if link.label else link.url
            for link in (kit.promotion_links if kit else [])
        ),
        help="업로드 캡션 본문 뒤에 붙습니다. 예약/스마트스토어/쿠팡 파트너스 링크 등. "
        "제휴 링크는 표시광고법상 의무 문구를 캡션에 함께 실어야 합니다.",
    )
    if st.form_submit_button("브랜드킷 저장"):
        photos = [line.strip() for line in photos_text.splitlines() if line.strip()]
        promotion_links = []
        for line in links_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                label, url = line.split("|", 1)
                promotion_links.append(
                    PromotionLink(label=label.strip(), url=url.strip())
                )
            else:
                promotion_links.append(PromotionLink(url=line))
        new_kit = BrandKit(
            business_name=business_name.strip(),
            category=category.strip(),
            description=description.strip(),
            photos=photos,
            promotion_links=promotion_links,
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
# 템플릿별 동적 변수 (캡션/헤드라인에 채워질 값 — LLM 생성 또는 직접 입력)
template_vars = {
    t["template_id"]: t.get("required_variables", []) for t in templates
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

trends_data = promo_api.get_trends()
trend_keywords = [item["keyword"] for item in trends_data["items"]]
trend_col, trend_btn_col = st.columns([4, 1])
with trend_col:
    if trend_keywords:
        stale_mark = " (오래됨 — 갱신 권장)" if trends_data["stale"] else ""
        st.caption(f"급상승 검색어{stale_mark}: " + ", ".join(trend_keywords[:8]))
    else:
        st.caption("급상승 검색어 캐시 없음 — 갱신을 눌러 수집하세요.")
with trend_btn_col:
    if st.button("트렌드 갱신"):
        try:
            refreshed = promo_api.refresh_trends()
            st.success(f"{refreshed['count']}건 수집")
            st.rerun()
        except HTTPException as exc:
            st.error(_detail(exc))
use_trends = st.checkbox(
    "스크립트에 트렌드 반영",
    value=False,
    disabled=not trend_keywords,
    help="급상승 키워드를 프롬프트에 참고로 포함합니다 (억지 반영은 프롬프트가 차단).",
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
                        use_trends=use_trends,
                    )
                )
            st.session_state["promo_script"] = result["script"]
            # LLM 이 스크립트와 함께 생성한 동적 변수를 변수 입력란에 미리 채운다.
            st.session_state["promo_variables"] = result.get("variables", {})
        except HTTPException as exc:
            st.error(_detail(exc))
with col_prompt:
    if st.button("프롬프트만 보기"):
        try:
            result = promo_api.script_prompt(
                promo_api.ScriptPromptRequest(
                    template_id=template_id,
                    reference_url=reference_url or None,
                    use_trends=use_trends,
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

# ── 3-1. 템플릿 변수 (캡션/헤드라인에 채워질 동적 값) ─────────────────
required_vars = template_vars.get(template_id, [])
edited_vars: dict[str, str] = {}
if required_vars:
    st.markdown("**템플릿 변수** — 캡션/헤드라인 플레이스홀더에 채워집니다")
    saved_vars = st.session_state.get("promo_variables", {})
    var_cols = st.columns(min(len(required_vars), 3))
    for index, name in enumerate(required_vars):
        with var_cols[index % len(var_cols)]:
            edited_vars[name] = st.text_input(
                f"`{{{name}}}`",
                value=saved_vars.get(name, ""),
                key=f"promo_var_{name}",
            )
    st.caption(
        "LLM 스크립트 생성 시 자동 채워집니다. 비어 있으면 해당 헤드라인은 "
        "생략되고 캡션은 가게명 폴백 — 원문 `{...}` 이 공개되지 않습니다."
    )

# ── 4. 플랜 (승인 게이트) ────────────────────────────────────────────
st.header("4. 플랜 — 승인 게이트")
if st.button("플랜 생성", type="primary", disabled=not script.strip()):
    try:
        plan = promo_api.create_plan(
            promo_api.PlanCreateRequest(
                template_id=template_id,
                script=script,
                variables={k: v.strip() for k, v in edited_vars.items() if v.strip()},
            )
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

    version = plan.get("template_version", 1)
    title = f"플랜 {plan['plan_id']} — {plan['status']}"
    if version >= 2:
        title += f"  ·  템플릿 v{version}"
        if plan.get("style_preset"):
            title += f" ({plan['style_preset']})"
    st.subheader(title)

    narration = plan.get("narration")
    timeline_gate = plan.get("timeline_gate")

    metric_cols = st.columns(4 if narration else 3)
    metric_cols[0].metric("브랜드 소재", plan["used_brand_count"])
    metric_cols[1].metric("소재 수", len(plan["materials"]))
    metric_cols[2].metric(
        "구조 게이트", "통과" if plan["structural_gate"]["passed"] else "실패"
    )
    if narration:
        metric_cols[3].metric(
            "타임라인 게이트", "통과" if timeline_gate["passed"] else "실패"
        )

    if plan["photo_warning"]:
        st.warning("브랜드 소재 부족 — 스톡/대체 소재 비중이 높습니다")
    for failure in plan["structural_gate"]["failures"]:
        st.error(f"구조 게이트: {failure}")

    if narration:
        _render_narration_panel(narration, timeline_gate, plan)

    st.caption("소재 배치: " + " → ".join(plan["materials"]))

    used_vars = plan.get("variables") or {}
    if used_vars:
        st.caption(
            "채워진 변수: " + ", ".join(f"{k}={v}" for k, v in used_vars.items())
        )

    missing_vars = plan.get("missing_variables") or []
    if missing_vars:
        st.warning(
            "미충전 변수: " + ", ".join(missing_vars)
            + " — 해당 헤드라인은 생략되고 캡션은 가게명으로 폴백됩니다."
        )

    upload_caption = plan.get("upload_caption")
    if upload_caption:
        st.markdown("**업로드 캡션 미리보기**")
        st.info(upload_caption)

    # 소재 클립 미리보기 — 컷 분할·헤드라인이 반영된 실제 소재를 렌더 전에 본다
    # (승인한 것 = 렌더되는 것).
    material_paths = plan.get("material_paths", [])
    clip_seconds = plan.get("clip_seconds", [])
    if material_paths:
        st.markdown("**소재 클립 미리보기** (컷 분할·헤드라인 반영됨)")

        def _clip_label(idx: int) -> str:
            base = os.path.basename(material_paths[idx])
            secs = f" ({clip_seconds[idx]:g}초)" if idx < len(clip_seconds) else ""
            return f"{idx + 1}. {base}{secs}"

        selected = st.selectbox(
            "미리볼 클립",
            options=list(range(len(material_paths))),
            format_func=_clip_label,
            key="promo_clip_preview",
        )
        preview_path = material_paths[selected]
        if os.path.exists(preview_path):
            st.video(preview_path)
        else:
            st.caption(f"클립 파일이 없습니다: {os.path.basename(preview_path)}")

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
