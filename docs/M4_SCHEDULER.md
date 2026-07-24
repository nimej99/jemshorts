# M4: 스케줄 오토파일럿 + 브랜드킷 API/크롤 연결

M3 의 "남은 조립" 중 스케줄 자동 실행과 브랜드킷 크롤 UI 연결을 완결한
기록. 코어 수정 없음.

## 구성

- `app/promo/scheduler.py` — 주간 스케줄 + 오토파일럿 (v1 `schedule` 테이블)
  - `set_frequency(freq_per_week)`: 1~21 검증, next_runs 를 균등 간격
    (7일/freq)으로 재계산. missed 이력은 보존
  - `tick()`: 만기 런 소비 -> 오토파일럿 실행 -> next_runs 보충.
    실패는 missed_runs(최근 50개 유지) + v1 `fallback_log` 기록,
    상한 도달은 skipped (missed 아님)
  - `run_autopilot_once()`: 상한 확인 -> 템플릿 로테이션(누적 delivered
    수 기준) -> LLM 스크립트 -> 플랜(사전 게이트) -> 렌더(기술 게이트) ->
    업로드 -> delivered 기록. 모든 산출물이 promo_plans/videos 원장에
    남아 webui/API 에서 추적 가능
  - **cron 친화 설계** (데몬 없음, 코어 asgi 무수정):
    `python -m app.promo.scheduler` == `POST /api/v1/promo/schedule/tick`
- API 6개 추가 — `GET/PUT /schedule`, `POST /schedule/tick`,
  `GET/PUT /brandkit`, `POST /brandkit/crawl`
  - 크롤은 M1 crawler(SSRF 가드) 재사용. **빈 필드만 채움** (큐레이션
    우선), og_image 는 원격 URL 이라 photos 에 넣지 않음 (보안 경로 규칙)
- `app/promo/templates/schema.py` 에 `load_raw`/`load_all_raw` 추가 —
  api/scheduler 가 템플릿 원본 dict 로더 공유
- webui promo 페이지: 브랜드킷 섹션에 URL 크롤 버튼 추가

## 운영 (cron 예시)

```cron
# 매시 정각 tick — 만기 런이 없으면 no-op
0 * * * * cd /path/to/jemshorts && uv run python -m app.promo.scheduler
```

빈도 설정: `PUT /api/v1/promo/schedule {"freq_per_week": 3}` (또는 webui).
일일 상한(`promo_upload_daily_cap`, 기본 3)은 오토파일럿 시작 전에
확인하므로 상한 도달 시 렌더 비용을 쓰지 않는다.

## 검증 (2026-07-24)

- 전체 스위트: **598 passed / 11 skipped / 4032 subtests** (28.2초)
- CLI 실기동: `python -m app.promo.scheduler` -> `{"status":"no-schedule",...}`
- 라우트 등재 실측: promo 14개 (GET/PUT 중복 경로 포함)
- 신규 테스트 (`test_scheduler.py` 12개 + API 9개):
  - 균등 간격 계산, 범위 검증, missed 이력 보존
  - tick: 만기 소비+보충, 실패->missed+fallback_log, SkipRun 은 미기록
  - 오토파일럿: 해피패스(플랜 rendered + delivered 1), 브랜드킷 부재,
    상한 도달 skip, 기술 게이트 실패 시 결과 보존+업로드 차단
  - 스케줄/브랜드킷 API: 404/422, tick 만기 실행, 크롤 빈 필드만 반영,
    크롤 실패 502

## 남은 조립 (M5 후보)

- 업로드 성과 회수 루프 (YouTube Data API 자격 필요 — 외부 의존이라 보류)
- 스톡 소재 자동 소싱을 오토파일럿에 연결 (현재 브랜드 소재만 사용)
