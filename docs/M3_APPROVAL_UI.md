# M3: LLM 스크립트 생성 + 업로드 자동화(일일 상한) + webui 승인 UI

M2 의 "남은 조립" 3건을 완결한 기록. 코어 수정 없음 (M2 의 router 2줄이
마지막 코어 수정).

## 구성

- `POST /api/v1/promo/scripts` — `_resolve_script_prompt`(템플릿+브랜드킷
  +레퍼런스 실측) -> 코어 `llm._generate_response` 호출. 코어는 실패를
  "Error: ..." 문자열로 반환하므로 502 로 승격 (조용한 오류 전파 금지)
- `POST /api/v1/promo/plans/{id}/upload` — 렌더 완료 플랜만(409),
  산출물 실존 확인, **일일 상한**(`promo_upload_daily_cap`, 기본 3) 초과 시
  429. 코어 upload_post(upload-post.com) 경유, 성공 시 v1 `videos` 테이블에
  `delivered` 기록 — 이 기록의 UTC 날짜 카운트가 상한의 근거
  (`app/promo/uploads.py`). 실패 시 미기록 (상한 소진 방지)
- `webui/pages/promo.py` — Streamlit 멀티페이지 **추가 파일** (Main.py
  무수정). 브랜드킷 폼 -> 템플릿 선택 -> 스크립트(LLM 생성/프롬프트
  미리보기/수동) -> 플랜 승인 게이트(게이트 판정·소재 배치 표시) ->
  렌더(진행률 폴링) -> 결과 영상 -> 업로드(상한 표시). 모든 동작은
  `app/promo/api.py` 핸들러를 직접 호출 — HTTP 왕복 없이 단일 코드 경로

## 검증 (2026-07-24)

- 라우트 등재 실측 (`app.asgi`): `/api/v1/promo/scripts`,
  `/api/v1/promo/plans/{plan_id}/upload` 포함 8개
- 전체 스위트: **579 passed / 11 skipped / 4032 subtests** (29.2초)
- 신규 테스트:
  - `test_uploads.py` — 상한 기본/오버라이드/비정상값 수렴, UTC 날짜
    카운트(전일 제외), cap_reached 경계
  - `test_promo_api.py` 추가 — LLM 성공/오류 502, 업로드 성공 기록,
    미렌더 409, 상한 429, 서비스 실패 502+미기록
  - `test_webui_promo_page.py` — AppTest 스모크: 브랜드킷 없으면 경고 후
    중단, 있으면 템플릿 단계 진입

## 운영 전제

- 업로드는 upload-post.com 계정 필요: config `upload_post_api_key` /
  `upload_post_username` / `upload_post_enabled` (미설정 시 502 로 명시 실패)
- LLM: config `llm_provider` + 해당 provider api_key
- 일일 상한 조정: config `promo_upload_daily_cap` (스팸 정책 방어 기본 3)

## 남은 조립 (M4 후보)

- 스케줄 자동 실행 (v1 `schedule` 테이블 활용한 주기 생성·업로드 데몬)
- 브랜드킷 크롤 UI 연결 (crawler 는 M1 부터 존재 — UI 는 수동 입력만)
- 업로드 후 성과 회수 (조회수/구독 전환 실측 루프)
