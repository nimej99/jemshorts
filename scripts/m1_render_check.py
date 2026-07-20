"""M1 소재 합성 + 품질 게이트 렌더 검증 (렌더 1회).

시드 템플릿 1개(templates-data/upbeat-new-menu.json) + 가상 BrandKit
(ffmpeg lavfi 로 생성한 브랜드 클립 2개 + 스톡 클립 1개)로:

1. compose_materials 로 MPT 로컬 렌더 입력을 구성하고 (브랜드/스톡 클립은
   storage/local_videos 밖의 임시 디렉터리에 만들어 보안 경로 복사를 실측)
2. app.services.task 파이프라인으로 1회 렌더 (LLM 우회 — 고정 한국어
   스크립트, EdgeTTS 한국어 음성, 동시성 1)
3. technical_gate + structural_gate 통과를 assert 한다.

실측 결과는 docs/M1_RENDER.md 에 기록한다.

Usage: uv run python scripts/m1_render_check.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from app.models import const  # noqa: E402
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams  # noqa: E402
from app.promo.brandkit.models import BrandKit  # noqa: E402
from app.promo.materials import compose_materials  # noqa: E402
from app.promo.quality import (  # noqa: E402
    TechnicalExpectation,
    structural_gate,
    technical_gate,
)
from app.promo.templates import load_template  # noqa: E402
from app.services import state as sm  # noqa: E402
from app.services import task as task_service  # noqa: E402
from app.utils import utils  # noqa: E402

TEMPLATE_PATH = os.path.join(ROOT_DIR, "templates-data", "upbeat-new-menu.json")

# 신메뉴 템플릿 structure(hook/body/cta) 가이드를 따르는 고정 스크립트.
# EdgeTTS 낭독 기준 약 17~18초 — total_duration_range [15, 30] 내부.
KOREAN_SCRIPT = (
    "드디어 나왔다, 우리 가게 신메뉴! "
    "직접 만든 소스로 매콤하면서도 담백한 맛을 살렸고, 가격은 부담 없는 구천 원입니다. "
    "정성껏 담아낸 한 접시를 지금 바로 확인해 보세요. "
    "이번 주말까지 시식 이벤트, 지금 방문하세요!"
)
VOICE_NAME = "ko-KR-SunHiNeural-Female"
FONT_NAME = "NotoSansKR-Bold.otf"

# 브랜드 클립 2개(단색) + 스톡 클립 1개(테스트 패턴). lavfi — 완전 오프라인.
BRAND_CLIP_SOURCES = [
    ("m1-brand-amber.mp4", "color=c=0xB45309:size=1080x1920:duration=6:rate=30"),
    ("m1-brand-teal.mp4", "color=c=0x0F766E:size=1080x1920:duration=6:rate=30"),
]
STOCK_CLIP_SOURCE = ("m1-stock-pattern.mp4", "testsrc2=size=1080x1920:duration=6:rate=30")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}"
        )
    return result


def generate_clip(directory: str, filename: str, source: str) -> str:
    clip_path = os.path.join(directory, filename)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            clip_path,
        ]
    )
    print(f"generated clip: {clip_path}")
    return clip_path


def build_params(materials) -> VideoParams:
    return VideoParams(
        video_subject="promo-shorts m1 compose + gates check",
        video_script=KOREAN_SCRIPT,
        video_aspect=VideoAspect.portrait,
        video_concat_mode=VideoConcatMode.sequential,
        video_clip_duration=4,
        video_count=1,
        video_source="local",
        video_materials=materials,
        video_language="ko-KR",
        voice_name=VOICE_NAME,
        bgm_type="random",
        bgm_volume=0.2,
        subtitle_enabled=True,
        font_name=FONT_NAME,
        font_size=60,
        text_fore_color="#FFFFFF",
        stroke_color="#000000",
        stroke_width=1.5,
        n_threads=1,
    )


def ffprobe_summary(video_path: str) -> dict:
    probe = json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                video_path,
            ]
        ).stdout
    )
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    return {
        "resolution": f"{video_streams[0]['width']}x{video_streams[0]['height']}",
        "duration_seconds": round(float(probe["format"]["duration"]), 2),
        "video_codec": video_streams[0]["codec_name"],
        "audio_codec": audio_streams[0]["codec_name"] if audio_streams else None,
        "size_bytes": int(probe["format"]["size"]),
    }


def main() -> int:
    template = load_template(TEMPLATE_PATH)
    task_id = f"m1-check-{uuid.uuid4().hex[:8]}"

    # 브랜드/스톡 클립은 local_videos 밖에서 생성 -> compose 의 보안 경로
    # 복사(storage/local_videos 화이트리스트 수렴)를 실제로 검증한다.
    source_dir = tempfile.mkdtemp(prefix="m1-materials-")
    brand_paths = [
        generate_clip(source_dir, filename, source)
        for filename, source in BRAND_CLIP_SOURCES
    ]
    stock_path = generate_clip(source_dir, *STOCK_CLIP_SOURCE)

    brandkit = BrandKit(
        business_name="우리동네 분식",
        category="음식점",
        description="신메뉴 검증용 가상 브랜드킷",
        photos=brand_paths,
        source="manual",
    )

    local_videos_dir = utils.storage_dir("local_videos", create=True)
    materials, used_brand_count, photo_warning = compose_materials(
        template, brandkit, [stock_path], local_videos_dir
    )
    print(
        f"composed materials: {[os.path.basename(m.url) for m in materials]}, "
        f"used_brand_count={used_brand_count}, photo_warning={photo_warning}"
    )

    params = build_params(materials)
    render_started_at = time.monotonic()
    result = task_service.start(task_id, params, stop_at="video")
    render_seconds = time.monotonic() - render_started_at

    task_state = sm.state.get_task(task_id) or {}
    assert task_state.get("state") == const.TASK_STATE_COMPLETE, (
        f"task did not complete: state={task_state.get('state')}, "
        f"error={task_state.get('error')}"
    )
    videos = (result or {}).get("videos") or []
    assert videos, f"pipeline returned no videos: {result}"
    final_video = videos[0]

    technical = technical_gate(
        final_video,
        TechnicalExpectation(
            duration_range=template.total_duration_range,
            duration_tolerance_s=2.0,
        ),
    )
    structural = structural_gate(
        template.structure,
        materials,
        used_brand_count,
        photo_warning=photo_warning,
    )
    assert technical.passed, f"technical_gate failed: {technical.failures}"
    assert structural.passed, f"structural_gate failed: {structural.failures}"

    report = {
        "task_id": task_id,
        "template_id": template.template_id,
        "final_video": final_video,
        "render_seconds": round(render_seconds, 1),
        "used_brand_count": used_brand_count,
        "photo_warning": photo_warning,
        "materials": [os.path.basename(m.url) for m in materials],
        "ffprobe": ffprobe_summary(final_video),
        "technical_gate": {
            "passed": technical.passed,
            "failures": technical.failures,
            "warnings": technical.warnings,
        },
        "structural_gate": {
            "passed": structural.passed,
            "failures": structural.failures,
            "warnings": structural.warnings,
        },
    }
    print("M1_RESULT " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
