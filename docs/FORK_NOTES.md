# FORK_NOTES

promo-shorts 는 [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT)의
포크다. upstream remote 는 유지하며, 작업 브랜치는 `v1-dev` 다.
이 문서는 포크 운영 원칙과 upstream 대비 변경 사항의 단일 장부(ledger)다.

## 원칙

1. **MPT 코어 수정 0이 기본.** 신규 코드는 전부 `app/promo/` (테스트는
   `test/promo/`) 하위에 둔다. 코어 동작 변경이 필요하면 코어 파일을 고치는
   대신 래핑/매핑 계층(예: `app/promo/configmap.py`)으로 우회한다.
2. **불가피한 코어 수정은 반드시 이 문서에 등재한다.** 항목마다
   사유, 대상 파일, diff 위치(커밋 해시 또는 라인 범위)를 기록한다.
   등재 없는 코어 수정은 리뷰에서 반려한다.
3. **upstream cherry-pick 원칙.**
   - upstream 은 정기적으로 `git fetch upstream` 으로만 추적한다.
   - 통째 merge 대신 필요한 커밋만 `git cherry-pick -x` 로 가져온다
     (`-x` 로 원본 커밋 해시를 메시지에 남긴다).
   - cherry-pick 이 아래 "코어 수정 예외" 또는 "허용된 리소스 수정"과
     충돌하면, 포크 측 변경을 우선하고 충돌 해소 내용을 이 문서에 추가한다.

## 허용된 리소스 수정 (코어 수정으로 취급하지 않음)

| 대상 | 사유 |
| --- | --- |
| `resource/songs/` | 기본 BGM 트랙 팩 교체 (M0). 코드가 아닌 번들 리소스이며, 파일명 규약만 유지하면 코어 로직에 영향 없음 |
| `docker-compose.yml` | 로컬 배포 구성을 단일 `app` 서비스로 재작성 (M0). upstream 의 webui/api 2컨테이너 구성을 대체. `docker-compose.release.yml`, `docker-compose.gpu.yml` 은 손대지 않음 (release 는 후속 마일스톤) |
| `docker-compose.yml` (M0 docker boot) | 볼륨 마운트 대상 오류 수정: `/app/*` → `/MoneyPrinterTurbo/*` (이미지 WORKDIR 기준. 기존 경로는 코어 `storage_dir()` 와 연결되지 않아 영속화가 동작하지 않음). `command` 를 `scripts/docker-entrypoint.sh` 로 교체해 기동 전 `ensure_config()` 훅 연결 |
| `scripts/docker-entrypoint.sh` | 신규 (M0). 단일 컨테이너 엔트리포인트: `ensure_config(/MoneyPrinterTurbo/storage)` 호출 후 api(백그라운드) + webui(포그라운드) 기동 |
| `scripts/docker-entrypoint.sh` (M0 review fixes) | 경로를 셸 보간 대신 env(`PROMO_STORAGE_DIR`)로 파이썬에 전달하고, `MPT_CONFIG_FILE` 을 export 해 코어가 영속 config 를 직접 사용하게 함 |
| `resource/fonts/NotoSansKR-Bold.otf` | 한국어 기본 폰트 추가 (M0). OFL 1.1 라이선스, 출처: [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk). 코드가 아닌 번들 리소스 |
| `.gitignore` | append 만: `data/`, `bridge-secret/` (compose 볼륨 디렉터리, M0) |
| `.dockerignore` | append 만: `data/`, `bridge-secret/` — `COPY . .` 시 브리지 시크릿/로컬 데이터가 이미지에 구워지는 것을 방지 (M0) |

## 코어 수정 예외 (등재 필수)

| 파일 | 사유 | 내용 / diff 위치 |
| --- | --- | --- |
| `app/config/config.py` | config 영속화 (M0 review fixes). symlink 우회 방식은 심볼릭 링크 미지원 파일시스템·컨테이너 재빌드 시 깨지기 쉬워 아키텍트 리뷰에서 반려됨. env 오버라이드가 최소·명시적 해법 | `config_file = os.environ.get("MPT_CONFIG_FILE", f"{root_dir}/config.toml")` 1줄 + `save_config()` 의 임시파일 디렉터리를 `os.path.dirname(config_file)` 로 변경(볼륨 경계에서 `os.replace` 원자성 유지). load/save 모두 같은 `config_file` 경로 사용 (v1-dev, M0 review fixes 커밋) |
| `app/router.py` | promo API 노출 (M2). promo 라우터(`/api/v1/promo/*`)를 앱에 등재하려면 루트 라우터 include 가 유일한 진입점 — 래핑 우회 불가 | import 1줄 + `root_api_router.include_router(promo_api.router)` 1줄 (v1-dev, M2 커밋). 라우터 구현 전체는 `app/promo/api.py` 에 격리 |

## 참고: 코어 우회 사례

- `app/config/config.py` 는 `config.toml` 경로를 기본으로 레포 루트에 두고
  import 시점에 로드한다. 설정을 영속 스토리지에 두기 위해
  `app/promo/configmap.py` 의 `ensure_config()` 가 앱 기동 전에
  `<storage>/config.toml` 을 시드(+SaaS 차단값 주입)하고, 코어는 env
  `MPT_CONFIG_FILE`(위 코어 수정 예외)로 그 경로를 직접 읽고 쓴다.
  과거의 루트 `config.toml` symlink 연결 방식은 제거되었다.

## 외부 코드/도구 채택 결정 (2026-07-24)

- **레포 공개**: `nimej99/jemshorts` 는 PUBLIC 이다 (오너 결정).
- **OpenMontage (AGPL-3.0) 코드 차용 허용**: 오너가 소스 공개를 전제로 승인.
  AGPL 코드를 처음 들여오는 커밋에서 **루트 LICENSE 를 AGPL-3.0 으로 교체**하고
  (MIT upstream 고지는 유지 — MIT→AGPL 결합은 적법), 차용 파일마다 출처
  (OpenMontage 경로 + 커밋 해시)를 파일 헤더와 이 문서에 등재한다.
  아직 차용된 코드는 없다 — 현재 LICENSE 는 MIT 그대로.
- **agent-reach (MIT)**: 제품 임베드 아님. 개발/리서치 도구로 로컬 설치
  (`uv tool install git+https://github.com/Panniantong/agent-reach`).
  용도: 경쟁 쇼츠 자막 추출 (`yt-dlp --skip-download --write-auto-subs
  --sub-langs "ko,en" --js-runtimes node <URL>`), 레퍼런스 리서치.
- **agency-agents (MIT)**: 개별 페르소나 파일만 참고용으로
  `~/.claude/agents/` 에 설치 (video-streaming-engineer, prompt-engineer).

## 외부 코드/도구 채택 결정 2차 (2026-07-25)

- **Postiz (AGPL-3.0) 채택**: 셀프호스트 소셜 스케줄링. 별도 서비스로
  띄우고 공개 API 만 HTTP 호출 (`app/promo/publish.py`) — 코드 결합이
  없어 LICENSE 영향 없음. upload-post.com(유료) 대체 경로.
  셀프호스트 실기동 검증은 운영 셋업 시점에 수행 (어댑터는 공식 API 문서
  docs.postiz.com/public-api 기준 구현 + 모킹 테스트 완료).
- **Scrapling (BSD-3) 조건부 채택**: 네이버 공식 지역검색 API 로 부족할
  때의 보조 수집 카드. enrich 계층(PR #1)에 의존하므로 **PR #1 머지 후**
  config 옵션 플래그로 통합한다. 봇차단 우회는 약관 리스크 상존 — 기본
  비활성.
- **last30days-skill (MIT)**: dev-time 리서치 스킬. `~/.claude/skills/
  last30days` 에 설치 완료. 니치/경쟁 조사용, 제품 임베드 아님.
- **reelforge (Apache-2.0) 참고 채택**: Korean-first 동일 도메인. 한국어
  자막/폰트(Pretendard, D2Coding OFL) 처리 벤치마크 대상. 코드 차용 시
  Apache 고지 유지.
- **vox-director / Orkas-VideoStudio (MIT) 설계 차용**: beats/timeline
  스펙 분석 → 템플릿 v2 설계 (`docs/TEMPLATE_V2_DESIGN.md`). 코드 차용
  아님 (필요 시 MIT 라 가능).
