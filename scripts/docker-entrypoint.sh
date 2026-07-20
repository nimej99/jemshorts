#!/bin/sh
# promo-shorts 단일 컨테이너 엔트리포인트 (M0).
#
# MPT 코어(app/config/config.py)는 import 시점에 레포 루트 config.toml 을
# 로드하므로, api/webui 기동 전에 app/promo/configmap.ensure_config 를 호출해
# <storage>/config.toml 을 준비하고 루트 config.toml 을 연결한다.
# storage 는 docker-compose.yml 에서 ./data 볼륨으로 영속화된다.
set -eu

STORAGE_DIR="${PROMO_STORAGE_DIR:-/MoneyPrinterTurbo/storage}"

python3 -c "from app.promo.configmap import ensure_config; ensure_config('$STORAGE_DIR')"

# api (fastapi/uvicorn, :8080) 백그라운드
python3 main.py &

# webui (streamlit, :8501) 포그라운드. 컨테이너 내부는 0.0.0.0 리슨,
# 호스트 노출은 compose ports 의 127.0.0.1 바인딩으로 제한한다.
exec streamlit run ./webui/Main.py \
    --server.address=0.0.0.0 --server.port=8501 \
    --browser.serverAddress=127.0.0.1 --server.enableCORS=True \
    --browser.gatherUsageStats=False --client.toolbarMode=minimal \
    --logger.hideWelcomeMessage=True --server.showEmailPrompt=False
