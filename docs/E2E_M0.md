# E2E M0: 한국어 오프라인 렌더 검증

LLM 호출 없이 `app.services.task` 파이프라인을 직접 구동해 한국어 쇼츠 1편을
렌더링하는 스모크 테스트 기록. 스크립트: `scripts/e2e_render_ko.py`

## 환경

```bash
uv python install 3.11     # cpython-3.11.15-macos-aarch64
uv sync --frozen           # uv.lock 고정 해석 (117 packages)
```

- 실행 머신: Apple M2 (macOS darwin 25.5.0), ffmpeg 8.1.2 (homebrew)
- LLM: 미사용 (`video_script` 고정 제공으로 우회)
- TTS: EdgeTTS `ko-KR-SunHiNeural` (네트워크 필요, LLM 외 유일한 외부 호출)
- 소재: ffmpeg lavfi로 생성한 로컬 테스트 클립 3개 (완전 오프라인)
- BGM: `bgm_type=random` — `resource/songs/` 로컬 트랙 팩에서 선택

## 한국어 폰트

- 파일: `resource/fonts/NotoSansKR-Bold.otf` (4,816,044 bytes)
- 출처: <https://github.com/notofonts/noto-cjk> (`Sans/SubsetOTF/KR/NotoSansKR-Bold.otf`)
- 라이선스: SIL Open Font License 1.1 (OFL) — Copyright Adobe / Google, Noto Sans CJK 프로젝트

## 실행 명령

```bash
uv run python scripts/e2e_render_ko.py
# 피크 메모리 측정 시:
/usr/bin/time -l uv run python scripts/e2e_render_ko.py
```

## 실측치 (2026-07-20, task_id: e2e-ko-2a91d5ad)

- exit code: 0 (모든 assert 통과)
- 렌더 소요시간(`task.start` 구간): 95.9초
- 전체 wall time(`/usr/bin/time -l`, 클립 생성 + TTS 포함): 134.05초 (user 108.46s, sys 19.42s)
- 피크 메모리(maximum resident set size): 677,822,464 bytes (약 646 MiB)
- 60초물 환산 렌더시간: 95.9 / 17.37 * 60 = 331.3초 (약 5.5분) — 10분 미만, ESCALATION 없음

## ffprobe 결과 (final-1.mp4)

- 해상도: 1080x1920 (9:16)
- duration: 17.37초 (> 0)
- 스트림: video h264 + audio aac (둘 다 존재)
- 파일 크기: 3,825,906 bytes

## 생성 파일 경로

- 최종 영상: `storage/tasks/e2e-ko-2a91d5ad/final-1.mp4` (storage/는 gitignore 대상)
- 테스트 소재: `storage/local_videos/e2e-ko-clip-{blue,red,pattern}.mp4` (1080x1920, 각 6초)
- 중간 산출물: `storage/tasks/e2e-ko-2a91d5ad/{audio.mp3,subtitle.srt,combined-1.mp4}`
- 중간 프레임: `docs/e2e-frame.jpg` (duration/2 = 8.69초 지점 추출)

## 자막 번인 확인

`docs/e2e-frame.jpg` 육안 대용 메모: testsrc2 패턴 프레임 하단에
"복잡한 편집 없이 몇 분 만에 나만의 홍보 영상이 완성됩니다" 2줄이
흰색 볼드 + 검정 외곽선으로 선명하게 번인됨. Noto Sans KR Bold 글리프
깨짐 없음(한글 자모 정상 렌더).
