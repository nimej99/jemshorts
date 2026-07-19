"""Offline Korean E2E render check for the promo-shorts M0 slice.

Runs the full app.services.task pipeline without any LLM call:
- generates three local test clips with ffmpeg (1080x1920, 6s each)
- injects them via video_source=local + video_materials
- provides a fixed Korean promo script (LLM bypass)
- narrates with a Korean EdgeTTS voice and burns in Korean subtitles
  using resource/fonts/NotoSansKR-Bold.otf

After rendering, the output is verified with ffprobe (1080x1920,
duration > 0, one video + one audio stream) and a mid frame is
extracted to docs/e2e-frame.jpg for visual subtitle inspection.
Any failed assertion exits non-zero.

Usage: uv run python scripts/e2e_render_ko.py
"""

import json
import os
import subprocess
import sys
import time
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from app.models import const  # noqa: E402
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode, VideoParams  # noqa: E402
from app.services import state as sm  # noqa: E402
from app.services import task as task_service  # noqa: E402
from app.utils import utils  # noqa: E402

KOREAN_SCRIPT = (
    "하루를 바꾸는 가장 쉬운 방법을 지금 소개합니다. "
    "복잡한 편집 없이 몇 분 만에 나만의 홍보 영상이 완성됩니다. "
    "자연스러운 한국어 음성과 선명한 자막까지 한 번에 담았습니다. "
    "지금 바로 시작해 보세요."
)
VOICE_NAME = "ko-KR-SunHiNeural-Female"
FONT_NAME = "NotoSansKR-Bold.otf"
FRAME_OUTPUT = os.path.join(ROOT_DIR, "docs", "e2e-frame.jpg")

# 서로 다른 단색 2개 + 테스트 패턴 1개. lavfi 소스라서 완전 오프라인이다.
CLIP_SOURCES = [
    ("e2e-ko-clip-blue.mp4", "color=c=0x1E3A8A:size=1080x1920:duration=6:rate=30"),
    ("e2e-ko-clip-red.mp4", "color=c=0xB91C1C:size=1080x1920:duration=6:rate=30"),
    ("e2e-ko-clip-pattern.mp4", "testsrc2=size=1080x1920:duration=6:rate=30"),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}"
        )
    return result


def generate_test_clips() -> list[str]:
    clip_dir = utils.storage_dir("local_videos", create=True)
    paths = []
    for filename, source in CLIP_SOURCES:
        clip_path = os.path.join(clip_dir, filename)
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
        paths.append(clip_path)
        print(f"generated test clip: {clip_path}")
    return paths


def build_params(clip_paths: list[str]) -> VideoParams:
    return VideoParams(
        video_subject="promo-shorts korean offline e2e",
        video_script=KOREAN_SCRIPT,
        video_aspect=VideoAspect.portrait,
        video_concat_mode=VideoConcatMode.sequential,
        video_clip_duration=3,
        video_count=1,
        video_source="local",
        video_materials=[
            MaterialInfo(provider="local", url=clip_path, duration=0)
            for clip_path in clip_paths
        ],
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


def ffprobe_video(video_path: str) -> dict:
    result = run(
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
    )
    return json.loads(result.stdout)


def assert_output(video_path: str) -> dict:
    assert os.path.isfile(video_path), f"final video is missing: {video_path}"
    probe = ffprobe_video(video_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert video_streams, "no video stream in final output"
    assert audio_streams, "no audio stream in final output"

    width = int(video_streams[0]["width"])
    height = int(video_streams[0]["height"])
    assert (width, height) == (1080, 1920), f"unexpected resolution: {width}x{height}"

    duration = float(probe["format"]["duration"])
    assert duration > 0, f"non-positive duration: {duration}"

    return {
        "resolution": f"{width}x{height}",
        "duration_seconds": round(duration, 2),
        "video_codec": video_streams[0]["codec_name"],
        "audio_codec": audio_streams[0]["codec_name"],
        "size_bytes": int(probe["format"]["size"]),
    }


def extract_mid_frame(video_path: str, duration: float) -> None:
    os.makedirs(os.path.dirname(FRAME_OUTPUT), exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{duration / 2:.2f}",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            FRAME_OUTPUT,
        ]
    )
    print(f"mid frame extracted: {FRAME_OUTPUT}")


def main() -> int:
    task_id = f"e2e-ko-{uuid.uuid4().hex[:8]}"
    clip_paths = generate_test_clips()
    params = build_params(clip_paths)

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
    metrics = assert_output(final_video)
    extract_mid_frame(final_video, metrics["duration_seconds"])

    per_60s_seconds = render_seconds / metrics["duration_seconds"] * 60
    report = {
        "task_id": task_id,
        "final_video": final_video,
        "render_seconds": round(render_seconds, 1),
        "render_seconds_per_60s_output": round(per_60s_seconds, 1),
        **metrics,
    }
    print("E2E_RESULT " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
