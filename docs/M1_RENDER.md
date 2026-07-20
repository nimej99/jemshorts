# M1: 소재 합성 + 품질 게이트 렌더 검증

시드 템플릿 1개 + 가상 BrandKit 으로 `compose_materials` -> MPT task 렌더
1회 -> `technical_gate` + `structural_gate` 통과를 검증한 기록.
스크립트: `scripts/m1_render_check.py`

## 구성

- 템플릿: `templates-data/upbeat-new-menu.json` (upbeat-new-menu-v1,
  hook(video) 3s / body(any) 12s / cta(photo) 5s, total_duration_range [15, 30])
- 가상 BrandKit: ffmpeg lavfi 브랜드 클립 2개 (`m1-brand-amber.mp4`,
  `m1-brand-teal.mp4`, 1080x1920 각 6초) — 임시 디렉터리에 생성해
  `storage/local_videos` 보안 경로 복사를 실측
- 스톡 클립 1개: `m1-stock-pattern.mp4` (testsrc2, 1080x1920 6초) —
  선다운로드 미구현 인터페이스(`stock_paths` 주입) 경유
- 스크립트: 고정 한국어 (LLM 우회), TTS: EdgeTTS `ko-KR-SunHiNeural`,
  폰트: `NotoSansKR-Bold.otf`, 동시성 1 (`n_threads=1`), 렌더 1회

## 실행 명령

```bash
uv run python scripts/m1_render_check.py
```

## 실측치 (2026-07-20, task_id: m1-check-104a1fa8)

- exit code: 0 (모든 assert 통과)
- 렌더 소요시간(`task.start` 구간): 46.4초 (출력 19.27초 기준 60초물 환산 약 144초)
- 합성 결과: `used_brand_count=2`, `photo_warning=false`
- 소재 배치 순서 (섹션 순서 = hook/body/cta):
  1. `brand-00-m1-brand-amber.mp4` (hook, video 슬롯 — 브랜드 우선)
  2. `brand-01-m1-brand-teal.mp4` (body, any 슬롯)
  3. `stock-00-m1-stock-pattern.mp4` (cta, photo 슬롯 — photo 소재 부재로
     video 대체, compose 가 warning 로그 기록: 조용한 강등 금지)
- 오디오 19.25초 대비 소재 12초 -> MPT 가 클립 2개 루프로 채움 (코어 동작)

## ffprobe 결과 (final-1.mp4)

- 해상도: 1080x1920 (9:16)
- duration: 19.27초 — total_duration_range [15, 30] ± 2.0초 내
- 스트림: video h264 + audio aac (둘 다 존재)
- 파일 크기: 3,081,775 bytes

## 게이트 판정

- technical_gate: passed=True, failures=[], warnings=[]
  (해상도 1080x1920 일치, duration 범위 내, video+audio 스트림 존재)
- structural_gate: passed=True, failures=[], warnings=[]
  (hook/body/cta 섹션 존재, video_materials 3개, 브랜드 소재 2개 >= 1)

## 생성 파일 경로

- 최종 영상: `storage/tasks/m1-check-104a1fa8/final-1.mp4` (storage/는 gitignore 대상)
- 복사된 소재: `storage/local_videos/{brand-00-m1-brand-amber,brand-01-m1-brand-teal,stock-00-m1-stock-pattern}.mp4`
- 중간 산출물: `storage/tasks/m1-check-104a1fa8/{audio.mp3,subtitle.srt,combined-1.mp4}`
