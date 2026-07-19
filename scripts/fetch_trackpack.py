#!/usr/bin/env python3
"""promo-shorts BGM 트랙팩 재현 다운로드 스크립트.

docs/TRACK_PACK.md 표에 등재된 트랙을 resource/songs/ 에 다시 내려받는다.
원 출처 FreePD.com(CC0)이 2026년 초 서비스를 종료했기 때문에, 다운로드는
Internet Archive Wayback Machine 의 원본 바이너리 스냅샷(id_ 접미사 URL)을
사용한다. 표준 라이브러리만 사용하며 추가 의존성이 없다.

사용법:
    python3 scripts/fetch_trackpack.py [--force]

--force 를 주면 기존 파일을 덮어쓴다. 기본은 이미 존재하는 파일 건너뛰기.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Wayback 원본 바이너리 스냅샷 프리픽스. 타임스탬프는 근사값이며,
# Wayback 이 가장 가까운 실제 캡처로 302 리다이렉트한다.
WAYBACK_PREFIX = "https://web.archive.org/web/20250107id_/"

# (파일명, 원본 URL) — docs/TRACK_PACK.md 표와 1:1 로 일치해야 한다.
TRACKS: list[tuple[str, str]] = [
    # mood: upbeat
    ("upbeat-city-sunshine.mp3", "https://freepd.com/music/City Sunshine.mp3"),
    ("upbeat-happy-whistling-ukulele.mp3", "https://freepd.com/music/Happy Whistling Ukulele.mp3"),
    ("upbeat-advertime.mp3", "https://freepd.com/music/Advertime.mp3"),
    # mood: calm
    ("calm-lovely-piano-song.mp3", "https://freepd.com/music/Lovely Piano Song.mp3"),
    ("calm-nostalgic-piano.mp3", "https://freepd.com/music/Nostalgic Piano.mp3"),
    ("calm-landras-dream.mp3", "https://freepd.com/music/Landra's Dream.mp3"),
    # mood: energetic
    ("energetic-arpent.mp3", "https://freepd.com/music/Arpent.mp3"),
    ("energetic-beat-one.mp3", "https://freepd.com/music/Beat One.mp3"),
    ("energetic-goodnightmare.mp3", "https://freepd.com/music/Goodnightmare.mp3"),
]

SONGS_DIR = Path(__file__).resolve().parent.parent / "resource" / "songs"


def archived_url(original_url: str) -> str:
    """원본 URL 을 Wayback 원본 바이너리 스냅샷 URL 로 변환한다."""
    # 경로의 공백/아포스트로피만 인코딩하고 스킴·호스트는 그대로 둔다.
    parts = urllib.parse.urlsplit(original_url)
    encoded = parts._replace(path=urllib.parse.quote(parts.path)).geturl()
    return WAYBACK_PREFIX + encoded


def fetch(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "promo-shorts-trackpack/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) < 100_000:
        # mp3 트랙이 100KB 미만이면 오류 페이지일 가능성이 높다.
        raise RuntimeError(f"suspiciously small download ({len(data)} bytes) from {url}")
    if not (data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        raise RuntimeError(f"not an mp3 payload from {url}")
    dest.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    args = parser.parse_args()

    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for filename, original in TRACKS:
        dest = SONGS_DIR / filename
        if dest.exists() and not args.force:
            print(f"skip (exists): {filename}")
            continue
        url = archived_url(original)
        try:
            fetch(url, dest)
            print(f"ok: {filename} ({dest.stat().st_size} bytes)")
        except Exception as exc:  # noqa: BLE001 - 실패 트랙을 모아서 보고
            failures.append(filename)
            print(f"FAIL: {filename}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} track(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\nall {len(TRACKS)} tracks present in {SONGS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
