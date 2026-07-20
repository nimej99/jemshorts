# M0 Docker 기동 검증 리포트

검증일: 2026-07-20 (macOS Apple Silicon / Docker Desktop 25.0.2)

## 결과 요약

| 항목 | 결과 |
|---|---|
| `docker compose config` 문법 | 통과 |
| 이미지 빌드 (`promo-shorts-app`) | 성공, 1.75GB (classic builder, ~24분) |
| `docker compose up -d` 단일 커맨드 기동 | 성공 (단일 `app` 컨테이너) |
| API `http://127.0.0.1:8080/docs` | 200 |
| WebUI `http://127.0.0.1:8501` | 200 |
| 기동 로그 ERROR/Traceback 스캔 | 무오류 |
| config 영속화 (`./data/config.toml`) | 생성 확인 (13,384B, entrypoint의 `ensure_config`) |
| `docker compose down` 정리 | 정상 |

## 발견 결함 및 수정 (docs/FORK_NOTES.md 등재)

1. **볼륨 마운트 경로 오류**: compose가 `/app/storage`에 마운트했으나 이미지 WORKDIR는 `/MoneyPrinterTurbo` — 코어 `storage_dir()`와 연결되지 않아 영속화 미동작. `./data:/MoneyPrinterTurbo/storage`로 수정.
2. **entrypoint 부재**: Dockerfile CMD는 webui만 실행. `scripts/docker-entrypoint.sh` 신설 — `ensure_config` 호출 후 api(백그라운드) + streamlit(포그라운드) 기동.
3. **시크릿 이미지 번들 위험**: `.dockerignore`에 `data/`, `bridge-secret/` 추가 (`COPY . .` 시 브리지 시크릿 유출 방지). `.gitignore`에도 동일 append.

## 사후 변경 (아키텍트 리뷰 반영, 2026-07-20 — 위 검증 이후)

- **config 영속화 재설계**: `ensure_config` 의 루트 `config.toml` symlink 연결을
  제거하고, 코어가 env `MPT_CONFIG_FILE=/MoneyPrinterTurbo/storage/config.toml`
  (entrypoint export + compose environment)로 영속본을 직접 읽고 쓰도록 변경
  (코어 최소 수정 — docs/FORK_NOTES.md '코어 수정 예외' 참조). `ensure_config`
  는 시드 + SaaS 차단 주입만 담당.
- **entrypoint 경화**: storage 경로를 셸 보간 대신 env(`PROMO_STORAGE_DIR`)로
  파이썬에 전달.
- **healthcheck 추가**: compose 에 api `:8080` 대상 python3 urllib healthcheck.
- 위 항목들은 Docker 재검증 미수행 상태이며, 재검증은 별도 수행 예정.

## 인프라 이슈 기록 (환경 한정, 레포 무관)

- 검증 머신의 Docker Desktop 데몬 pull 경로가 정지 상태(컨테이너 네트워크는 정상)여서, 베이스 이미지 `python:3.11-slim-bullseye`를 컨테이너 내 skopeo로 사이드로드(`docker load`)한 뒤 classic builder(`DOCKER_BUILDKIT=0`)로 빌드함. 정상 환경에서는 `docker compose up -d --build` 한 번으로 동작.

## 재현 명령

```bash
mkdir -p data bridge-secret
docker compose up -d --build
curl http://127.0.0.1:8080/docs   # 200
curl http://127.0.0.1:8501        # 200
docker compose down
```
