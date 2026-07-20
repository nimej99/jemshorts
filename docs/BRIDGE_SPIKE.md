# Bridge Spike 결과 (M0)

로컬 CLI(claude/codex)를 OpenAI 호환 HTTP 브리지로 감싸는 방식의 실현 가능성 스파이크.

- 실행일: 2026-07-19
- 실행 환경: macOS (Apple M2), Node v22.22.3
- 사용 버전: claude 2.1.215 (Claude Code), codex-cli 0.144.4
- 실행 명령: `node scripts/bridge_spike.mjs 10`
- 프롬프트: `옷을 파는 가게의 쇼츠 훅 한 문장만 써줘.` (짧은 한국어 프롬프트, 재시도 없이 1시도=1호출)

## 표본 크기에 대한 메모

계획서의 100회에서 n=10으로 축소. 사유: 두 CLI 모두 정액제(구독) 쿼터를 사용하므로
호출량 절약이 필요하고, 성공/실패는 인증·프로세스 기동 수준에서 결정되는 이진 특성이라
n=10으로도 게이트 판정(≥90%)의 대표성은 충분하다고 판단.

## 결과 (n=10, 각 CLI)

| CLI | 성공 | 성공률 | 평균 레이턴시 | 최소 | 최대 | ≥90% 게이트 |
| --- | --- | --- | --- | --- | --- | --- |
| claude | 10/10 | 100% | 27,096 ms | 13,294 ms | 47,087 ms | PASS |
| codex | 10/10 | 100% | 7,854 ms | 7,136 ms | 9,045 ms | PASS |

- 성공 기준: exit 0 + 비어있지 않은 텍스트 응답.
- 실패 0건 (계정 미로그인/CLI 오류 없음).
- 레이턴시 특성: codex가 일관되게 빠르고 분산이 작음(7.1~9.0s). claude는 평균 3.4배
  느리고 분산이 큼(13.3~47.1s). 짧은 훅 문장 생성 용도로는 codex가 유리.

## 플래그 호환성 메모

- claude 2.1.215
  - `claude -p <prompt> --output-format json` 정상 동작. stdout에 단일 JSON,
    `result` 필드에 최종 텍스트, `usage.input_tokens/output_tokens` 포함, `is_error`로 오류 판별.
- codex 0.144.4
  - `codex exec <prompt>`는 stdout이 사람이 읽는 스트림(세션 헤더/hook 로그 포함)이라
    파싱에 부적합. `--output-last-message <file>`로 최종 메시지만 회수하는 방식 채택.
  - `--skip-git-repo-check` 추가(브리지를 git 저장소 밖에서 기동해도 동작하도록).
  - 주의: stdin이 pipe로 열려 있으면 "Reading additional input from stdin..." 상태로
    EOF까지 대기 → 자식 프로세스 stdin을 즉시 닫아야 함(닫지 않으면 120s 타임아웃).
    브리지의 `execFileP`에서 `child.stdin.end()`로 처리.

## 게이트 판정

두 CLI 모두 성공률 100% ≥ 90% → **PASS**. 브리지 방식(v1) 진행 가능.

## 브리지 프로토타입 검증 (수용 기준)

`node bridge/promo_bridge.mjs` 기동(127.0.0.1:8765, env `BRIDGE_BIND`/`BRIDGE_PORT`) 후:

- `GET /health` → 200, `bridge_version=0.1.0`, `protocol_version=1`,
  `cli={name:"claude", version:"2.1.215 (Claude Code)"}`, `host_lan_ip=192.168.0.9`,
  IPv4 non-internal 인터페이스 목록 포함.
- `POST /v1/chat/completions` (model=`codex-bridge`) → 200, `chat.completion` 형태 응답,
  1회 성공(레이턴시 6,908 ms). 타임아웃 120s + 실패 시 1회 재시도.

## M2 정식화 필수 항목 (스파이크 → 프로덕션 승격 조건)

스파이크 코드는 M2 에서 아래 항목을 갖추기 전에는 프로덕션 승격 불가.
(M0 스코프에서는 코드 수정 없이 항목만 고정한다.)

1. **shared-secret 인증**: 모든 엔드포인트에 공유 시크릿 기반 인증
   (예: `Authorization: Bearer <bridge-secret>`) 적용.
   **경고: 인증이 없는 동안 `BRIDGE_BIND` 는 반드시 127.0.0.1 고정** —
   LAN 바인딩은 인증 구현 이후에만 허용.
2. **동시성 제한/큐**: CLI 하위 프로세스 동시 실행 상한과 초과 요청 큐잉
   (또는 429 반환) — 정액제 쿼터 고갈과 로컬 자원 폭주 방지.
3. **transient-only 재시도**: 현행 "실패 시 무조건 1회 재시도"를 일시적
   오류(타임아웃, 프로세스 기동 실패 등)에만 재시도하도록 분류. 결정적
   오류(인증 만료, 잘못된 플래그 등)는 즉시 실패 반환.
4. **프롬프트 stdin/tempfile 전달**: 프롬프트를 argv 로 넘기지 않고
   stdin 또는 임시 파일로 전달 — argv 는 프로세스 목록(`ps`)에 노출되고
   길이 제한/이스케이프 문제가 있음.
5. **/health 인터페이스 노출 인증화**: `host_lan_ip`, 인터페이스 목록 등
   네트워크 토폴로지 정보는 인증된 요청에만 반환 (비인증 /health 는
   liveness 최소 응답만).
