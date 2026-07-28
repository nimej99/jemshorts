"""v2 템플릿 전수 시나리오 검증 (수동 실행 — CI 아님).

번들 v2 시드 3종을 전부 실제 edge-tts 로 plan 까지 돌리고, 가장 위험한
경로(energetic — 헤드라인이 전부 동적 변수)는 실제 렌더까지 완주한다.
e2e_render_v2.py 가 upbeat-new-menu-v2 하나만 보던 것을 전 템플릿으로 확장.

검증 항목:
- 템플릿별 voice.speed 가 plan.voice_rate 로 반영되는가
- 실측 타이밍 + 타임라인 게이트 통과
- shots 선언대로 컷이 분할되는가 (clip 개수)
- 브랜드킷 헤드라인은 채워지고, 동적 헤드라인은 변수 유무에 따라 채워짐/생략
- 동적 변수가 없으면 헤드라인 배너 PNG 가 0개 (원문 배너 금지 안전장치)
- energetic 전체 렌더: 산출물·게이트·캡션(변수 3개 충전)·배너 3개

실행: uv run python scripts/scenario_v2_all.py
"""

import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from app.promo.brandkit.models import BrandKit  # noqa: E402
from app.promo.pipeline import (  # noqa: E402
    execute_render,
    headline_texts,
    plan_render,
    upload_caption,
)
from app.promo.templates import load_template  # noqa: E402
from app.utils import utils  # noqa: E402

TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates-data")

SCRIPT = (
    "드디어 나왔다, 우리 가게 신메뉴! 매콤한 양념에 담백한 육수가 어우러집니다. "
    "직접 우려낸 육수로 매일 아침 준비합니다. 가격은 만이천원, 점심에도 부담 없습니다. "
    "이번 주말까지 시식 이벤트, 지금 방문하세요!"
)

# 템플릿별 동적 변수 (LLM 이 채워주는 값 가정)
VARIABLES_BY_TEMPLATE = {
    "upbeat-new-menu-v2": {"menu_name": "매운 떡볶이", "highlight": "첫 주문 20% 할인"},
    "calm-space-mood-v2": {"mood_point": "잔잔한 음악과 원두 향"},
    "energetic-event-sale-v2": {
        "event_name": "여름 대전",
        "benefit": "전 품목 30% 할인",
        "period": "이번 주 일요일까지",
    },
}

# 템플릿별 기대 voice.speed / 컷 분할 수(총 클립 개수) / 배너 개수(변수 충전 시)
EXPECTED = {
    "upbeat-new-menu-v2": {"voice_rate": 1.0, "clips": 4, "banners": 2},
    "calm-space-mood-v2": {"voice_rate": 0.95, "clips": 4, "banners": 2},
    "energetic-event-sale-v2": {"voice_rate": 1.05, "clips": 5, "banners": 3},
}


def generate_clip(directory: str, filename: str, source: str) -> str:
    clip_path = os.path.join(directory, filename)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", source, "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_path],
        capture_output=True, text=True, check=True,
    )
    return clip_path


def ffprobe_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def count_banners(local_dir: str, plan_id: str) -> int:
    return len(glob.glob(os.path.join(local_dir, f"headline-{plan_id}-*.png")))


def main() -> int:
    source_dir = tempfile.mkdtemp(prefix="scenario-materials-")
    brand_paths = [
        generate_clip(source_dir, "brand-a.mp4", "testsrc2=size=1080x1920:duration=8:rate=30"),
        generate_clip(source_dir, "brand-b.mp4", "smptebars=size=1080x1920:duration=8:rate=30"),
    ]
    brandkit = BrandKit(
        business_name="우리동네 분식", category="음식점",
        description="시나리오 전수 검증용 가상 브랜드킷", photos=brand_paths, source="manual",
    )
    local_dir = utils.storage_dir("local_videos", create=True)

    results = {}

    # ── 1. v2 시드 3종 전부 실측 plan ─────────────────────────────────
    for template_id, variables in VARIABLES_BY_TEMPLATE.items():
        template = load_template(os.path.join(TEMPLATES_DIR, f"{template_id}.json"))
        exp = EXPECTED[template_id]

        plan = plan_render(template, brandkit, [], SCRIPT, local_dir, variables=variables)
        assert plan.narration is not None, f"{template_id}: 내레이션 실측 없음"
        assert plan.timeline is not None and plan.timeline.passed, (
            f"{template_id}: 타임라인 게이트 실패 {plan.timeline.failures}"
        )
        assert plan.approved_ready, f"{template_id}: 승인 불가 {plan.structural.failures}"

        # voice.speed 반영
        assert plan.voice_rate == exp["voice_rate"], (
            f"{template_id}: voice_rate {plan.voice_rate} != {exp['voice_rate']}"
        )
        # 컷 분할
        assert len(plan.clip_seconds) == exp["clips"], (
            f"{template_id}: 클립 {len(plan.clip_seconds)}개 != 기대 {exp['clips']}개"
        )
        # 헤드라인: 변수 충전 시 기대 개수만큼 배너 생성
        banners = count_banners(local_dir, plan.plan_id)
        assert banners == exp["banners"], (
            f"{template_id}: 배너 {banners}개 != 기대 {exp['banners']}개"
        )
        # 캡션이 변수로 채워졌는지 (원문 금지)
        caption = upload_caption(plan, brandkit)
        for value in variables.values():
            assert value in caption, f"{template_id}: 캡션에 변수 '{value}' 없음: {caption}"
        assert "{" not in caption, f"{template_id}: 캡션에 원문 플레이스홀더: {caption}"

        results[template_id] = {
            "narration_total_s": plan.narration.total_s,
            "voice_rate": plan.voice_rate,
            "clips": len(plan.clip_seconds),
            "banners": banners,
            "caption": caption,
        }
        print(
            f"[plan] {template_id}: {plan.narration.total_s:g}초, "
            f"voice {plan.voice_rate}x, 클립 {len(plan.clip_seconds)}개, "
            f"배너 {banners}개 OK"
        )

    # ── 2. 안전장치: 동적 변수 없으면 헤드라인 배너 0개 ────────────────
    energetic = load_template(os.path.join(TEMPLATES_DIR, "energetic-event-sale-v2.json"))
    plan_novar = plan_render(energetic, brandkit, [], SCRIPT, local_dir)  # 변수 없음
    texts_novar = headline_texts(energetic, brandkit)  # 전부 동적 → 전부 생략
    assert texts_novar == [None, None, None], (
        f"변수 없이 헤드라인이 채워지면 안 된다: {texts_novar}"
    )
    assert count_banners(local_dir, plan_novar.plan_id) == 0, (
        "변수 없이 배너 PNG 가 생성되면 안 된다 (원문 배너 금지)"
    )
    # 캡션은 subject 로 폴백 (원문 금지)
    caption_novar = upload_caption(plan_novar, brandkit)
    assert "{" not in caption_novar, f"폴백 캡션에 원문: {caption_novar}"
    print(f"[safety] energetic 변수 없음: 헤드라인 전부 생략, 캡션 폴백='{caption_novar}' OK")

    # ── 3. energetic 전체 렌더 (가장 위험한 경로: 동적 헤드라인 3개) ────
    plan_render2 = plan_render(
        energetic, brandkit, [], SCRIPT, local_dir,
        variables=VARIABLES_BY_TEMPLATE["energetic-event-sale-v2"],
    )
    result = execute_render(plan_render2)
    assert result.technical.passed, f"energetic 렌더 게이트 실패: {result.technical.failures}"
    final_video = result.videos[0]
    output_duration = ffprobe_duration(final_video)
    audio_file = plan_render2.narration_audio.audio_file
    reuse_drift = abs(output_duration - ffprobe_duration(audio_file))
    assert reuse_drift < 0.5, f"energetic 오디오 재사용 drift {reuse_drift:.2f}초"
    energetic_banners = count_banners(local_dir, plan_render2.plan_id)
    assert energetic_banners == 3, f"energetic 렌더 배너 {energetic_banners}개 != 3개"

    results["energetic-event-sale-v2"]["render"] = {
        "final_video": final_video,
        "output_duration_s": round(output_duration, 2),
        "audio_reuse_drift_s": round(reuse_drift, 2),
        "render_seconds": result.render_seconds,
        "banners": energetic_banners,
    }
    print(
        f"[render] energetic: 산출물 {output_duration:.2f}초, "
        f"재사용 drift {reuse_drift:.2f}초, 배너 {energetic_banners}개 OK"
    )

    # ── 4. v1 회귀 (upbeat-new-menu, 내레이션/타임라인 없어야 함) ──────
    v1 = load_template(os.path.join(TEMPLATES_DIR, "upbeat-new-menu.json"))
    plan_v1 = plan_render(v1, brandkit, [], SCRIPT, local_dir)
    assert plan_v1.narration is None, "v1 은 내레이션 실측이 없어야 한다"
    assert plan_v1.timeline is None, "v1 은 타임라인 게이트가 없어야 한다"
    assert plan_v1.voice_rate == 1.0, "v1 은 기본 voice_rate"
    assert plan_v1.approved_ready, f"v1 승인 불가: {plan_v1.structural.failures}"
    print("[v1] upbeat-new-menu: 내레이션/타임라인 없음(하위호환), 승인 OK")

    print("SCENARIO_RESULT " + json.dumps(results, ensure_ascii=False))
    print("ALL SCENARIOS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
