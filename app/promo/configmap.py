"""config.toml 영속화 매핑 계층.

MPT 코어(app/config/config.py:13-14)는 config.toml 경로를 레포 루트로
하드코딩하고 import 시점에 로드한다. 코어를 수정하지 않고 설정을 영속
스토리지(/app/storage)에 보관하기 위해, 앱 기동 시 app.config 를 import
하기 전에 ensure_config() 를 호출해 다음을 보장한다.

1. <storage>/config.toml 이 없으면 생성한다.
   - 레포 루트에 기존 config.toml(일반 파일)이 있으면 그 내용을 승계하고,
   - 없으면 config.example.toml 을 복사한다.
2. 유료 SaaS 경로 차단 기본값을 강제 주입한다 (upload_post_enabled=false 등).
3. 레포 루트 config.toml 을 <storage>/config.toml 로 향하는 symlink 로
   교체한다. symlink 를 지원하지 않는 파일시스템에서는 복사로 폴백한다
   (이 경우 storage 본이 원본이며, 재기동 시 다시 동기화된다).
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
    """storage 하위에 config.toml 을 영속화하고 루트 config.toml 을 연결한다.

    반환값은 영속 config.toml 경로(<storage_dir>/config.toml)다.
    이미 존재하는 영속 config.toml 은 보존하며, SAAS_BLOCK_DEFAULTS 만
    강제로 덮어쓴다.
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
    _link_root_config(root_cfg, persistent)
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


def _link_root_config(root_cfg: Path, persistent: Path) -> None:
    """루트 config.toml 이 영속본을 가리키도록 symlink(폴백: 복사)한다."""
    target = persistent.resolve()
    if root_cfg.is_symlink():
        if root_cfg.resolve() == target:
            return
        root_cfg.unlink()
    elif root_cfg.exists():
        # 영속본이 이미 원본이므로 루트 사본은 링크로 대체한다.
        root_cfg.unlink()
    try:
        root_cfg.symlink_to(target)
    except OSError:
        # symlink 미지원 파일시스템 폴백. storage 본이 원본이라는 전제는
        # 유지되며, 런타임 중 루트 사본의 변경은 재기동 시 덮어써진다.
        shutil.copyfile(persistent, root_cfg)
