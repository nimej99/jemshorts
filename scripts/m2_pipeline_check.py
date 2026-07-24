"""M2 파이프라인 실렌더 검증 (plan/render 2단계 제품 경로, 렌더 1회).

M1 검증(scripts/m1_render_check.py)과 동일한 lavfi 소재/고정 스크립트를
쓰되, 검증 대상은 제품 계층이다:
  plan_render() -> (사전 structural_gate) -> execute_render() -> technical_gate

성공 기준:
- plan.approved_ready == True (사전 구조 게이트 통과)
- execute_render 정상 완료 + technical_gate 통과
- 산출물 9:16 / duration 범위 내 (technical_gate 가 실측)

실행: uv run python scripts/m2_pipeline_check.py
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from app.promo.brandkit.models import BrandKit  # noqa: E402
from app.promo.pipeline import execute_render, plan_render  # noqa: E402
from app.promo.templates import load_template  # noqa: E402
from app.utils import utils  # noqa: E402

TEMPLATE_PATH = os.path.join(ROOT_DIR, "templates-data", "upbeat-new-menu.json")

KOREAN_SCRIPT = (
    "드디어 나왔다, 우리 가게 신메뉴! "
    "매콤하면서도 담백한 맛, 정성 가득한 조리 과정까지. "
    "가격도 부담 없이 준비했어요. "
    "이번 주말까지 시식 이벤트, 지금 바로 방문하세요!"
)

BRAND_CLIP_SOURCES = [
    ("m2-brand-amber.mp4", "color=c=#ffb347:size=1080x1920:duration=6:rate=30"),
    ("m2-brand-teal.mp4", "color=c=#3aa6a0:size=1080x1920:duration=6:rate=30"),
]
STOCK_CLIP_SOURCE = ("m2-stock-pattern.mp4", "testsrc2=size=1080x1920:duration=6:rate=30")


def generate_clip(directory: str, filename: str, source: str) -> str:
    clip_path = os.path.join(directory, filename)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-c:v", "libx264",
         "-pix_fmt", "yuv420p", clip_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return clip_path


def main() -> int:
    template = load_template(TEMPLATE_PATH)

    source_dir = tempfile.mkdtemp(prefix="m2-materials-")
    brand_paths = [
        generate_clip(source_dir, filename, source)
        for filename, source in BRAND_CLIP_SOURCES
    ]
    stock_path = generate_clip(source_dir, *STOCK_CLIP_SOURCE)

    brandkit = BrandKit(
        business_name="우리동네 분식",
        category="음식점",
        description="M2 파이프라인 검증용 가상 브랜드킷",
        photos=brand_paths,
        source="manual",
    )

    local_videos_dir = utils.storage_dir("local_videos", create=True)
    plan = plan_render(
        template, brandkit, [stock_path], KOREAN_SCRIPT, local_videos_dir
    )
    print(
        f"plan[{plan.plan_id}]: materials="
        f"{[os.path.basename(m.url) for m in plan.materials]}, "
        f"used_brand_count={plan.used_brand_count}, "
        f"photo_warning={plan.photo_warning}, "
        f"structural.passed={plan.structural.passed}"
    )
    assert plan.approved_ready, f"사전 구조 게이트 실패: {plan.structural.failures}"

    result = execute_render(plan)
    assert result.technical.passed, f"technical_gate 실패: {result.technical.failures}"

    report = {
        "plan_id": plan.plan_id,
        "task_id": result.task_id,
        "final_video": result.videos[0],
        "render_seconds": result.render_seconds,
        "used_brand_count": plan.used_brand_count,
        "photo_warning": plan.photo_warning,
        "structural_gate": {
            "passed": plan.structural.passed,
            "failures": plan.structural.failures,
            "warnings": plan.structural.warnings,
        },
        "technical_gate": {
            "passed": result.technical.passed,
            "failures": result.technical.failures,
            "warnings": result.technical.warnings,
        },
        "warnings": result.warnings,
    }
    print("M2_RESULT " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
