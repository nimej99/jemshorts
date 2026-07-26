"""템플릿 v2 전체 경로 실렌더 검증 (수동 실행 스크립트 — CI 아님).

v2 번들 시드(upbeat-new-menu-v2)로 plan -> render 전 구간을 **실제 TTS**로
돌려본다. 단위/통합 테스트가 주입된 가짜로 검증하는 것과 달리, 여기서는
edge-tts 실측 + 실제 ffmpeg 리타이밍/컷인/헤드라인 + 코어 렌더가 한 줄로
이어지는지를 본다.

확인하는 것:
1. plan_render 가 전체 스크립트 1회 TTS 로 섹션 경계를 실측하고 타임라인
   게이트를 통과한다.
2. hook 의 shots(와이드+컷인)가 실제 클립 2개로 분할된다 (clip_seconds).
3. execute_render 가 실측 오디오를 재사용한다 — 산출물 길이가 실측 총 길이와
   거의 같다 (TTS 를 다시 합성하면 다른 realization 이 나와 어긋난다).
4. 산출물 프레임이 섹션/컷 경계에서 실제로 달라진다 (와이드 != 컷인).

필요 환경: config 의 TTS 제공자(edge 기본 — 네트워크), 상태 백엔드(redis),
ffmpeg/ffprobe. 없으면 크게 실패한다 (조용한 스킵 금지 — 검증 스크립트이므로).

실행: uv run python scripts/e2e_render_v2.py
"""

import hashlib
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

TEMPLATE_PATH = os.path.join(ROOT_DIR, "templates-data", "upbeat-new-menu-v2.json")
FRAME_DIR = os.path.join(ROOT_DIR, "docs", "qa")

# 섹션마다 구별되는 패턴 영상 (lavfi — 완전 오프라인). testsrc2 는 숫자/막대
# 패턴이 있어 와이드 vs 컷인(중앙 확대) 크롭이 눈에 띄게 달라진다.
KOREAN_SCRIPT = (
    "드디어 나왔다, 우리 가게 신메뉴! "
    "매콤한 양념에 담백한 육수가 어우러집니다. "
    "직접 우려낸 육수로 매일 아침 준비합니다. "
    "가격은 만이천원, 점심에도 부담 없습니다. "
    "이번 주말까지 시식 이벤트, 지금 방문하세요!"
)

BRAND_CLIP_SOURCES = [
    ("v2-brand-pattern-a.mp4", "testsrc2=size=1080x1920:duration=8:rate=30"),
    ("v2-brand-pattern-b.mp4", "smptebars=size=1080x1920:duration=8:rate=30"),
]


def generate_clip(directory: str, filename: str, source: str) -> str:
    clip_path = os.path.join(directory, filename)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", source, "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return clip_path


def ffprobe_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def frame_signature(video_path: str, at_s: float) -> str:
    """지정 시각 프레임의 전체 화면 해시 (컷이 바뀌면 값이 달라진다)."""
    from PIL import Image

    os.makedirs(FRAME_DIR, exist_ok=True)
    png = os.path.join(FRAME_DIR, f"v2-frame-{at_s:.2f}.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.3f}",
         "-i", video_path, "-frames:v", "1", "-q:v", "2", png],
        capture_output=True,
        text=True,
        check=True,
    )
    with Image.open(png) as image:
        return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def main() -> int:
    template = load_template(TEMPLATE_PATH)
    assert template.version == 2, "v2 시드가 아닙니다"
    assert template.timing and template.timing.owner == "narration"

    source_dir = tempfile.mkdtemp(prefix="v2-materials-")
    brand_paths = [
        generate_clip(source_dir, filename, source)
        for filename, source in BRAND_CLIP_SOURCES
    ]
    brandkit = BrandKit(
        business_name="우리동네 분식",
        category="음식점",
        description="v2 e2e 검증용 가상 브랜드킷",
        photos=brand_paths,
        source="manual",
    )

    local_videos_dir = utils.storage_dir("local_videos", create=True)

    # 1. 실측 플랜 (실제 edge-tts 1회 합성)
    plan = plan_render(template, brandkit, [], KOREAN_SCRIPT, local_videos_dir)
    assert plan.narration is not None, "내레이션 실측이 없습니다"
    assert plan.timeline is not None and plan.timeline.passed, (
        f"타임라인 게이트 실패: {plan.timeline.failures}"
    )
    assert plan.approved_ready, f"승인 불가: {plan.structural.failures}"

    narration = plan.narration
    print(
        f"plan[{plan.plan_id}]: 실측 총 {narration.total_s:g}초, "
        f"섹션 경계 {narration.boundaries}, "
        f"clip_seconds={plan.clip_seconds}"
    )
    # hook 은 shots 2개(와이드+컷인) -> 클립이 섹션 수보다 많아야 한다.
    assert len(plan.clip_seconds) > len(narration.sections), (
        f"컷 분할이 반영되지 않았습니다: {plan.clip_seconds}"
    )

    # 2. 실렌더 (실측 오디오 재사용)
    result = execute_render(plan)
    assert result.technical.passed, f"technical_gate 실패: {result.technical.failures}"
    final_video = result.videos[0]

    # 3. 산출물 길이 == 실측 총 길이 (오디오 재사용 증거 — 재합성하면 어긋남)
    output_duration = ffprobe_duration(final_video)
    drift = abs(output_duration - narration.total_s)
    print(
        f"render[{result.task_id}]: 산출물 {output_duration:.2f}초 vs "
        f"실측 {narration.total_s:g}초 (차이 {drift:.2f}초)"
    )
    assert drift < 1.0, (
        f"산출물 길이가 실측과 {drift:.2f}초 차이 — 오디오 재사용이 안 된 듯"
    )

    # 4. 컷/섹션 경계에서 프레임이 실제로 달라지는지
    hook_start, hook_end = narration.sections[0].start_s, narration.sections[0].end_s
    hook_mid = (hook_start + hook_end) / 2
    sig_wide = frame_signature(final_video, hook_start + 0.3)
    sig_cutin = frame_signature(final_video, hook_mid + 0.3)
    body_start, body_end = narration.sections[1].start_s, narration.sections[1].end_s
    sig_body = frame_signature(final_video, (body_start + body_end) / 2)

    assert sig_wide != sig_cutin, "hook 와이드/컷인 두 컷이 같은 화면입니다"
    assert sig_cutin != sig_body, "hook 컷인과 body 가 같은 화면입니다"
    print("컷 전환 검증: hook-wide != hook-cutin != body")

    report = {
        "plan_id": plan.plan_id,
        "task_id": result.task_id,
        "final_video": final_video,
        "render_seconds": result.render_seconds,
        "narration_total_s": narration.total_s,
        "output_duration_s": round(output_duration, 2),
        "duration_drift_s": round(drift, 2),
        "clip_seconds": plan.clip_seconds,
        "timeline_gate": {
            "passed": plan.timeline.passed,
            "failures": plan.timeline.failures,
            "warnings": plan.timeline.warnings,
        },
        "technical_gate": {
            "passed": result.technical.passed,
            "failures": result.technical.failures,
        },
    }
    print("V2_E2E_RESULT " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
