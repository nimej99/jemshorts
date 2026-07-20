"""config.toml 영속화 매핑 계층.

MPT 코어(app/config/config.py)는 config.toml 경로를 기본으로 레포 루트에
두고 import 시점에 로드하지만, 포크 예외로 env `MPT_CONFIG_FILE` 오버라이드를
지원한다 (docs/FORK_NOTES.md '코어 수정 예외' 참조). 설정을 영속 스토리지
(컨테이너 기준 /MoneyPrinterTurbo/storage, 호스트 ./data)에 보관하기 위해,
앱 기동 시(scripts/docker-entrypoint.sh) `MPT_CONFIG_FILE` 을
<storage>/config.toml 로 지정하고 app.config 를 import 하기 전에
ensure_config() 를 호출해 다음을 보장한다.

1. <storage>/config.toml 이 없으면 생성(시드)한다.
   - 레포 루트에 기존 config.toml(일반 파일)이 있으면 그 내용을 승계하고,
   - 없으면 config.example.toml 을 복사한다.
2. 유료 SaaS 경로 차단 기본값을 강제 주입한다 (upload_post_enabled=false 등).

경로 연결(코어가 영속본을 읽고 쓰게 하는 것)은 전적으로 `MPT_CONFIG_FILE`
env 가 담당하며, 과거의 루트 config.toml symlink 방식은 제거되었다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import toml

# 유료 SaaS 경로 차단 기본값. [app] 섹션에 강제 주입된다.
# 키/값을 추가하면 다음 ensure_config() 호출 때부터 적용된다.
SAAS_BLOCK_DEFAULTS: dict[str, object] = {
    "upload_post_enabled": False,
    "upload_post_auto_upload": False,
}

# 레포 루트: app/promo/configmap.py -> app/promo -> app -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_config(
    storage_dir: str | os.PathLike,
    repo_root: str | os.PathLike | None = None,
) -> Path:
    """storage 하위에 config.toml 을 시드하고 SaaS 차단값을 주입한다.

    반환값은 영속 config.toml 경로(<storage_dir>/config.toml)다.
    이미 존재하는 영속 config.toml 은 보존하며, SAAS_BLOCK_DEFAULTS 만
    강제로 덮어쓴다. 코어가 이 경로를 사용하게 하려면 app.config import
    전에 env `MPT_CONFIG_FILE` 을 이 반환 경로로 설정해야 한다.
    """
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)

    persistent = storage / "config.toml"
    root_cfg = root / "config.toml"
    example = root / "config.example.toml"

    if not persistent.exists():
        if root_cfg.is_file() and not root_cfg.is_symlink():
            # 기존 루트 config.toml 승계 (사용자 설정 유실 방지)
            shutil.copyfile(root_cfg, persistent)
        elif example.is_file():
            shutil.copyfile(example, persistent)
        else:
            raise FileNotFoundError(
                f"config source not found: {root_cfg} or {example}"
            )

    _inject_saas_block(persistent)
    return persistent


def _inject_saas_block(persistent: Path) -> None:
    """영속 config 의 [app] 섹션에 SaaS 차단값을 강제 주입한다."""
    cfg = toml.load(persistent)
    app_section = cfg.setdefault("app", {})
    changed = False
    for key, value in SAAS_BLOCK_DEFAULTS.items():
        if app_section.get(key) != value:
            app_section[key] = value
            changed = True
    if changed:
        # toml.dumps 는 주석을 보존하지 않지만, 코어 save_config 도 동일한
        # 방식으로 재직렬화하므로 포크에서 새로운 손실을 만들지는 않는다.
        persistent.write_text(toml.dumps(cfg), encoding="utf-8")
