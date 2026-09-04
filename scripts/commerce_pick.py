"""단일 상품 조사/렌더 도구: 상품 정보 -> 브랜드킷 -> 스크립트 -> 로컬 렌더.

상품 선별 결과의 대표 이미지를 신뢰 기준으로 삼고, 상세 이미지 후보는
동일 상품 검증을 통과한 것만 사용한다. 상품 페이지의 전체 img 수집은
추천상품이 섞이므로 금지한다. 템플릿은 commerce-pick-review-v2 고정
(autopilot 제외 수동 전용 시드 — 캡션에 [광고] + 의무 문구 자동 포함).

기본 커머스 게시 경로는 `scripts/commerce_roundup.py`의 일일 TOP3다.
공개 발행은 지원하지 않는다. 검증 소재 확보와 TOP3 후보별 사전 렌더에만 쓴다.

예시:
  uv run python scripts/commerce_pick.py \
    --product-name "차량용 방향제 디퓨저" \
    --link "https://link.coupang.com/XXXX" \
    --primary-image ./storage/promo_photos/primary.jpg \
    --images ./storage/promo_photos/detail-a.jpg \
    --description "고체형, 30일 지속, 송풍구 클립형" \
    --dry-run        # 렌더까지만, 업로드하지 않음

스크립트: --script 를 주면 그 내레이션을 그대로 쓰고, 없으면 코어 LLM 이
템플릿 가이드대로 생성한다 (타임라인 게이트 실패 시 분량 힌트 재시도).
pain_point/price_deal 은 LLM 의 [변수] 블록에서 채워지지만 --pain-point /
--price-deal 로 확정값을 덮어쓸 수 있다. 필수 변수가 끝내 비면 캡션 폴백으로
의무 광고 문구가 사라지므로 업로드를 중단한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from app.promo import db as promo_db  # noqa: E402
from app.promo import pipeline, plans  # noqa: E402
from app.promo.brandkit import store as brandkit_store  # noqa: E402
from app.promo.brandkit.models import BrandKit, PromotionLink  # noqa: E402
from app.promo.materials.product_images import validate_product_images  # noqa: E402
from app.promo.research import build_script_prompt, parse_script_response  # noqa: E402
from app.promo.templates.schema import load_raw, load_template  # noqa: E402
from app.promo.templates.variables import required_variables  # noqa: E402
from app.config import config  # noqa: E402
from app.utils import utils  # noqa: E402

TEMPLATE_PATH = os.path.join(ROOT_DIR, "templates-data", "commerce-pick-review-v2.json")


def _fail(message: str) -> None:
    print(f"[commerce-pick] 실패: {message}", file=sys.stderr)
    sys.exit(1)


def _generate_script(template, kit) -> tuple[str, dict]:
    """코어 LLM 로 스크립트 생성 ([변수] 블록 포함)."""
    from app.services import llm  # 지연 임포트

    response = (llm._generate_response(build_script_prompt(template, kit)) or "").strip()
    if not response or response.startswith("Error:"):
        _fail(f"스크립트 생성 실패: {response or '빈 응답'} (config LLM 설정 확인)")
    script, variables = parse_script_response(response, required_variables(template))
    if not script:
        _fail("스크립트 생성 실패: 내레이션이 비어 있습니다")
    return script, variables


def main() -> int:
    parser = argparse.ArgumentParser(description="커머스 추천 쇼츠 원샷 생성")
    parser.add_argument("--product-name", required=True, help="상품명 (캡션/헤드라인에 노출)")
    parser.add_argument("--link", required=True, help="파트너스 단축 링크 (link.coupang.com/...)")
    parser.add_argument(
        "--primary-image",
        required=True,
        help="상품 선별 결과에서 받은 대표 이미지 (신뢰 기준)",
    )
    parser.add_argument(
        "--images",
        nargs="*",
        default=[],
        help="동일 상품 상세 이미지 후보 (불일치 후보는 자동 제외)",
    )
    parser.add_argument("--description", default="", help="상품 장점/설명 (스크립트 힌트로 사용)")
    parser.add_argument("--script", default="", help="내레이션 직접 지정 (없으면 LLM 생성)")
    parser.add_argument("--pain-point", default="", help="훅 헤드라인 변수 확정값")
    parser.add_argument("--price-deal", default="", help="CTA 헤드라인 변수 확정값")
    parser.add_argument("--title", default="", help="업로드 제목 (기본: [광고] {상품명} 추천)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="호환용 플래그. 단일 상품은 항상 로컬 렌더만 수행",
    )
    args = parser.parse_args()

    images, image_checks = validate_product_images(args.primary_image, args.images)
    for check in image_checks:
        status = "통과" if check.accepted else f"제외({check.reason})"
        print(
            f"[commerce-pick] 이미지 검증 {status}: "
            f"{os.path.basename(check.path)} similarity={check.similarity:.4f}"
        )

    template_raw = load_raw(TEMPLATE_PATH)
    template = load_template(TEMPLATE_PATH)
    kit = BrandKit(
        business_name=args.product_name.strip(),
        description=args.description.strip(),
        photos=images,
        promotion_links=[PromotionLink(label="구매", url=args.link.strip())],
        source="manual",
    )

    # 1. 스크립트 + 변수
    required = required_variables(template)
    if args.script.strip():
        script = args.script.strip()
        variables: dict = {}
    else:
        script, variables = _generate_script(template, kit)
    variables = dict(variables)
    variables["product_name"] = args.product_name.strip()  # 공식 상품명 확정
    if args.pain_point.strip():
        variables["pain_point"] = args.pain_point.strip()
    if args.price_deal.strip():
        variables["price_deal"] = args.price_deal.strip()
    missing = [name for name in required if not str(variables.get(name, "")).strip()]
    if missing:
        _fail(
            f"필수 변수 미충전: {missing} — 캡션이 subject 로 폴백하면 의무 광고 "
            "문구가 빠지므로 중단합니다. --pain-point / --price-deal 로 지정하세요."
        )

    # 2. 브랜드킷 저장 (커머스 상품은 브랜드킷 1행 = 상품 1개 운영)
    conn = promo_db.connect()
    try:
        brandkit_store.save(conn, kit)

        # 3. 플랜 (사전 구조/타임라인 게이트)
        local_dir = utils.storage_dir("local_videos", create=True)
        plan = pipeline.plan_render(template, kit, [], script, local_dir, variables=variables)
        if not plan.structural.passed:
            _fail(f"사전 구조 게이트 실패: {plan.structural.failures}")
        if plan.timeline is not None and not plan.timeline.passed:
            _fail(
                f"타임라인 게이트 실패: {plan.timeline.failures} — 내레이션 분량을 "
                f"템플릿 범위({template.total_duration_range[0]:g}~"
                f"{template.total_duration_range[1]:g}초)에 맞추세요"
            )
        plans.save_plan(conn, plan, template_raw)

        # 4. 렌더
        task_id = f"promo-pick-{plan.plan_id}"
        plans.mark_rendering(conn, plan.plan_id, task_id)
        result = pipeline.execute_render(plan, task_id=task_id)
        if not result.technical.passed:
            plans.finish(conn, plan.plan_id, plans.STATUS_FAILED, {"error": str(result.technical.failures)})
            _fail(f"렌더 게이트 실패: {result.technical.failures}")
        video_path = result.videos[0]

        # 5. 캡션/제목 구성 (캡션에 [광고] + 의무 문구 + 구매 링크 자동 포함)
        hashtags = [f"#{tag}" for tag in template.hashtags_base]
        hub_url = str(config.app.get("product_hub_url", "")).strip()
        hub_line = f"\n\n▶ 영상 속 제품 모아보기: {hub_url}" if hub_url else ""
        description = (
            f"{pipeline.upload_caption(plan, kit)}{hub_line}\n\n{' '.join(hashtags)}"
        )
        title = args.title.strip() or f"[광고] {args.product_name.strip()} 추천"
        print(f"[commerce-pick] 산출물: {video_path}")
        print(f"[commerce-pick] 제목: {title}")
        print(f"[commerce-pick] 캡션:\n{description}\n")

        plans.finish(conn, plan.plan_id, plans.STATUS_RENDERED, {
            "task_id": task_id, "videos": [video_path],
            "render_seconds": result.render_seconds,
        })
        print("[commerce-pick] 단일 상품 공개 발행 금지 — 로컬 렌더만 완료")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
