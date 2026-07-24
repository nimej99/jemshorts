# M2: 파이프라인 배선 + promo API + 리서치 계층

M1 검증 스크립트에만 있던 플로우를 제품 계층으로 승격하고 API 로 노출한
기록. 실렌더 검증 스크립트: `scripts/m2_pipeline_check.py`

## 구성

- `app/promo/pipeline.py` — plan/render 2단계 오케스트레이터
  - `plan_render()`: compose + **사전 structural_gate** -> `RenderPlan`
    (렌더 비용 지출 전 승인 게이트. OpenMontage 스토리보드 게이트에서
    아이디어 차용 — 코드 차용 아님)
  - `execute_render()`: MPT task 렌더 -> technical_gate 실측.
    task 미완료/무산출은 `PipelineError`, 게이트 실패는 결과로 반환
- `app/promo/plans.py` + 마이그레이션 v3 (`promo_plans`) — 플랜 영속화.
  payload 에 템플릿 원본 dict 포함 (plan-render 간 템플릿 변경 무영향).
  상태 전이 planned -> rendering -> rendered|failed, rendering 재진입 차단
- `app/promo/api.py` — `/api/v1/promo/*` 라우터
  - `GET /templates`, `POST /plans`, `POST /plans/{id}/render`(202, 스레드),
    `GET /plans/{id}`(진행률은 코어 task state 그대로 조회),
    `POST /research/reference`, `POST /script-prompt`
  - 구조 게이트 실패 플랜은 렌더 409 (`force=true` 로 강제 가능)
- `app/promo/research/` — 레퍼런스 리서치
  - `ingest.py`: yt-dlp 자막 수집(`fetch_subtitles`) + VTT 파싱(롤링 중복
    제거) + 훅(첫 3초)/페이싱(초당 글자수) 실측(`analyze_reference`)
  - `hints.py`: 템플릿+브랜드킷+실측 페이싱 -> 스크립트 생성 프롬프트
    (`build_script_prompt`). 레퍼런스 없으면 기본 4.5자/초
- 코어 수정: `app/router.py` 2줄 (FORK_NOTES 코어 수정 예외 등재)

## 실측치 (2026-07-24, plan a71c9bef / task promo-e6ead1da)

- exit code: 0 (스크립트 assert 전부 통과)
- plan: `used_brand_count=2`, `photo_warning=false`, structural passed
- 렌더 소요: 38.0초 (M1 46.4초 대비 동급)
- technical_gate: passed=True, failures=[], warnings=[]
  (1080x1920, duration 범위 [15,30]±2.0s 내, video+audio 스트림)
- 소재 배치: brand-amber(hook) -> brand-teal(body) -> stock-pattern(cta)
- 라우트 등재 실측 (`app.asgi` 기동): `/api/v1/promo/{templates,plans,
  plans/{id},plans/{id}/render,research/reference,script-prompt}`

## 테스트

- `test/promo/test_pipeline.py` — plan 합성/게이트 결합, params 매핑,
  주입 러너 배선, 미완료/무산출 실패 수렴
- `test/promo/test_plans.py` — 저장/복원 왕복 동등성, rendering 재진입
  차단, failed 재렌더 허용
- `test/promo/test_promo_api.py` — 플랜->렌더->조회 플로우(가짜 러너),
  404/409/422/503 경계
- `test/promo/test_research.py` / `test_hints.py` — VTT 파싱, 훅/페이싱
  실측, 프롬프트 조립
- 전체 스위트: 567 passed / 11 skipped / 4032 subtests (실행 51.2초)

## 남은 조립 (M3 후보)

- webui 통합 (plan 승인 UI — 현재는 API 로만 노출)
- 스크립트 프롬프트 -> 코어 LLM 호출 연결 (현재 프롬프트 문자열까지 제공)
- 업로드 자동화 (`app/services/upload_post.py` 활용) + 일일 상한 스케줄러
