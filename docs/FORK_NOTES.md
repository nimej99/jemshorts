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
| `.gitignore` | append 만: `data/`, `bridge-secret/` (compose 볼륨 디렉터리, M0) |
| `.dockerignore` | append 만: `data/`, `bridge-secret/` — `COPY . .` 시 브리지 시크릿/로컬 데이터가 이미지에 구워지는 것을 방지 (M0) |

## 코어 수정 예외 (등재 필수)

현재 없음.

| 파일 | 사유 | diff 위치 |
| --- | --- | --- |

## 참고: 코어 우회 사례

- `app/config/config.py:13-14` 는 `config.toml` 경로를 레포 루트로
  하드코딩한다. 코어를 수정하지 않고 설정을 영속 스토리지에 두기 위해
  `app/promo/configmap.py` 의 `ensure_config()` 가 앱 기동 전에
  `<storage>/config.toml` 을 준비하고 루트 `config.toml` 을 symlink 로
  연결한다 (symlink 불가 시 복사 폴백).
