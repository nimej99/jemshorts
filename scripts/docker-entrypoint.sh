#!/bin/sh
# promo-shorts 단일 컨테이너 엔트리포인트 (M0).
#
# MPT 코어(app/config/config.py)는 import 시점에 config.toml 을 로드하며,
# 포크 예외로 env MPT_CONFIG_FILE 경로 오버라이드를 지원한다
# (docs/FORK_NOTES.md 참조). api/webui 기동 전에
# app/promo/configmap.ensure_config 로 <storage>/config.toml 을 시드하고,
# MPT_CONFIG_FILE 로 코어가 그 영속본을 직접 읽고 쓰게 한다.
# storage 는 docker-compose.yml 에서 ./data 볼륨으로 영속화된다.
set -eu

PROMO_STORAGE_DIR="${PROMO_STORAGE_DIR:-/MoneyPrinterTurbo/storage}"
export PROMO_STORAGE_DIR
MPT_CONFIG_FILE="${MPT_CONFIG_FILE:-$PROMO_STORAGE_DIR/config.toml}"
export MPT_CONFIG_FILE

# 경로는 셸 보간 대신 env 로 전달한다 (인용/이스케이프 사고 방지).
python3 -c "import os; from app.promo.configmap import ensure_config; ensure_config(os.environ['PROMO_STORAGE_DIR'])"

# api (fastapi/uvicorn, :8080) 백그라운드
python3 main.py &

# webui (streamlit, :8501) 포그라운드. 컨테이너 내부는 0.0.0.0 리슨,
# 호스트 노출은 compose ports 의 127.0.0.1 바인딩으로 제한한다.
exec streamlit run ./webui/Main.py \
    --server.address=0.0.0.0 --server.port=8501 \
    --browser.serverAddress=127.0.0.1 --server.enableCORS=True \
    --browser.gatherUsageStats=False --client.toolbarMode=minimal \
    --logger.hideWelcomeMessage=True --server.showEmailPrompt=False
