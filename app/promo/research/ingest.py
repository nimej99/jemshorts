"""레퍼런스 쇼츠 자막 수집(yt-dlp) + 훅/구조 분석.

용도: 참고 영상의 페이싱(초당 글자수), 훅 문구(첫 3초), 총 길이를 실측해
스크립트 생성 힌트로 쓴다. agent-reach 의 채널 라우팅에서 착안했지만
제품에는 yt-dlp 직접 호출만 임베드한다 (무로그인·읽기 전용 원칙,
docs/FORK_NOTES.md 참고).

- parse_vtt / analyze_reference: 순수 함수 (오프라인 테스트 대상).
- fetch_subtitles: yt-dlp subprocess (네트워크). yt-dlp 미설치면
  ResearchToolMissingError — 조용한 빈 결과 금지.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Sequence

from loguru import logger

# 훅 구간 정의 (쇼츠 이탈 판정 통념 기준 첫 3초)
HOOK_WINDOW_S = 3.0
_FETCH_TIMEOUT_S = 120

# "00:00:01.319 --> 00:00:03.470 ..." (뒤 정렬 속성은 무시)
_TIMESTAMP_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
# 인라인 태그 (<c>, <00:00:01.319> 등) 제거
_TAG_RE = re.compile(r"<[^>]+>")


class ResearchToolMissingError(RuntimeError):
    """yt-dlp 실행 파일을 찾을 수 없음."""


@dataclass(frozen=True)
class Cue:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class ReferenceStats:
    """레퍼런스 자막 실측 통계."""

    duration_s: float
    hook_text: str
    total_chars: int
    chars_per_sec: float
    cue_count: int


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str) -> list[Cue]:
    """WEBVTT 텍스트를 Cue 리스트로 파싱한다.

    유튜브 자동 자막의 롤링 중복(직전 큐 텍스트 반복)은 제거한다.
    타임스탬프 없는 헤더/메타 라인은 무시한다.
    """
    cues: list[Cue] = []
    current: tuple[float, float] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current is None:
            buffer = []
            return
        joined = " ".join(part for part in (line.strip() for line in buffer) if part)
        joined = _TAG_RE.sub("", joined).strip()
        if joined and (not cues or cues[-1].text != joined):
            cues.append(Cue(start_s=current[0], end_s=current[1], text=joined))
        current = None
        buffer = []

    for line in text.splitlines():
        match = _TIMESTAMP_RE.match(line.strip())
        if match:
            flush()
            groups = match.groups()
            current = (
                _to_seconds(*groups[0:4]),
                _to_seconds(*groups[4:8]),
            )
            continue
        if current is not None:
            if line.strip() == "":
                flush()
            else:
                buffer.append(line)
    flush()
    return cues


def analyze_reference(cues: Sequence[Cue]) -> ReferenceStats:
    """Cue 리스트에서 훅/페이싱 통계를 계산한다. 빈 입력이면 ValueError."""
    if not cues:
        raise ValueError("자막 큐가 비어 있습니다: 분석할 수 없습니다")

    duration_s = max(cue.end_s for cue in cues)
    hook_parts = [cue.text for cue in cues if cue.start_s < HOOK_WINDOW_S]
    total_chars = sum(len(cue.text.replace(" ", "")) for cue in cues)
    chars_per_sec = round(total_chars / duration_s, 2) if duration_s > 0 else 0.0
    return ReferenceStats(
        duration_s=round(duration_s, 2),
        hook_text=" ".join(hook_parts).strip(),
        total_chars=total_chars,
        chars_per_sec=chars_per_sec,
        cue_count=len(cues),
    )


def fetch_subtitles(
    url: str,
    *,
    lang_priority: Sequence[str] = ("ko", "en"),
    timeout_s: float = _FETCH_TIMEOUT_S,
) -> str | None:
    """yt-dlp 로 자막(.vtt) 텍스트를 받아온다. 자막이 없으면 None.

    업로더 자막을 우선하고 없으면 자동 자막을 받는다. 영상 본체는
    다운로드하지 않는다.
    """
    ytdlp = shutil.which("yt-dlp")
    if ytdlp is None:
        raise ResearchToolMissingError(
            "yt-dlp 를 찾을 수 없습니다 (설치: uv tool install yt-dlp)"
        )

    with tempfile.TemporaryDirectory(prefix="promo-research-") as tmp_dir:
        cmd = [
            ytdlp,
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(lang_priority),
            "--sub-format",
            "vtt",
            "-o",
            os.path.join(tmp_dir, "ref.%(ext)s"),
            url,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
        if result.returncode != 0:
            logger.warning(f"yt-dlp 자막 수집 실패 ({url}): {result.stderr.strip()[:500]}")
            return None

        # 언어 우선순위대로 첫 번째로 존재하는 .vtt 를 채택한다.
        for lang in lang_priority:
            for name in sorted(os.listdir(tmp_dir)):
                if name.endswith(f".{lang}.vtt"):
                    with open(os.path.join(tmp_dir, name), encoding="utf-8") as fh:
                        return fh.read()
        logger.warning(f"yt-dlp 는 성공했으나 자막 파일이 없습니다 ({url})")
        return None
