"""app/promo/configmap.ensure_config 단위 테스트 (tmpdir 기반)."""

from pathlib import Path

import toml

from app.promo.configmap import SAAS_BLOCK_DEFAULTS, ensure_config


def _make_root(tmp_path: Path, example_body: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config.example.toml").write_text(example_body, encoding="utf-8")
    return root


def test_ensure_config_creates_new_config_from_example(tmp_path):
    """신규 생성: example 을 storage/config.toml 로 시드한다 (symlink 없음)."""
    root = _make_root(tmp_path, '[app]\nvideo_source = "pexels"\n')
    storage = tmp_path / "storage"

    persistent = ensure_config(storage, repo_root=root)

    assert persistent == storage / "config.toml"
    assert persistent.is_file()
    cfg = toml.load(persistent)
    assert cfg["app"]["video_source"] == "pexels"

    # 경로 연결은 MPT_CONFIG_FILE env 가 담당하므로 루트 config.toml 은
    # 건드리지 않는다 (symlink 로직 제거됨).
    root_cfg = root / "config.toml"
    assert not root_cfg.exists()


def test_ensure_config_preserves_existing_storage_config(tmp_path):
    """기존 보존: 영속 config 의 사용자 설정을 example 로 덮어쓰지 않는다."""
    root = _make_root(tmp_path, '[app]\nvideo_source = "pexels"\n')
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "config.toml").write_text(
        '[app]\nvideo_source = "local"\npexels_api_keys = ["user-key"]\n',
        encoding="utf-8",
    )

    persistent = ensure_config(storage, repo_root=root)

    cfg = toml.load(persistent)
    assert cfg["app"]["video_source"] == "local"
    assert cfg["app"]["pexels_api_keys"] == ["user-key"]


def test_ensure_config_forces_saas_block_defaults(tmp_path):
    """SaaS 차단값 주입: 기존 값이 켜져 있어도 강제로 차단값을 덮어쓴다."""
    root = _make_root(tmp_path, "[app]\n")
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "config.toml").write_text(
        "[app]\n"
        "upload_post_enabled = true\n"
        "upload_post_auto_upload = true\n"
        'upload_post_api_key = "user-key"\n',
        encoding="utf-8",
    )

    persistent = ensure_config(storage, repo_root=root)

    cfg = toml.load(persistent)
    for key, value in SAAS_BLOCK_DEFAULTS.items():
        assert cfg["app"][key] == value
    # 차단 대상이 아닌 키는 보존한다.
    assert cfg["app"]["upload_post_api_key"] == "user-key"
