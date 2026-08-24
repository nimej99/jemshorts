"""업로드 백엔드 라우터(publish) 테스트 — 실 네트워크 없음."""

import pytest

from app.config import config
from app.promo import publish


@pytest.fixture()
def postiz_config(monkeypatch):
    monkeypatch.setitem(config.app, "promo_publish_backend", "postiz")
    monkeypatch.setitem(config.app, "postiz_api_url", "http://localhost:4007/public/v1")
    monkeypatch.setitem(config.app, "postiz_api_key", "key-1")
    monkeypatch.setitem(config.app, "postiz_youtube_integration_id", "int-1")


def test_backend_default_and_invalid(monkeypatch):
    monkeypatch.delitem(config.app, "promo_publish_backend", raising=False)
    assert publish.backend() == publish.BACKEND_UPLOAD_POST

    monkeypatch.setitem(config.app, "promo_publish_backend", "nope")
    assert publish.backend() == publish.BACKEND_UPLOAD_POST

    monkeypatch.setitem(config.app, "promo_publish_backend", "postiz")
    assert publish.backend() == publish.BACKEND_POSTIZ


def test_upload_post_backend_delegates(monkeypatch):
    monkeypatch.setitem(config.app, "promo_publish_backend", "upload_post")
    from app.services import upload_post

    calls = {}

    def fake(video_path, title, platforms=None, youtube_extra=None):
        calls.update(
            video_path=video_path, platforms=platforms, youtube_extra=youtube_extra
        )
        return {"success": True, "request_id": "up-1"}

    monkeypatch.setattr(upload_post, "cross_post_video", fake)

    result = publish.publish_video(
        "/tmp/v.mp4", "제목", "설명", ["태그"], privacy_status="unlisted"
    )

    assert result["success"] is True
    assert result["backend"] == publish.BACKEND_UPLOAD_POST
    assert calls["youtube_extra"]["privacyStatus"] == "unlisted"
    assert calls["youtube_extra"]["tags"] == ["태그"]


def test_postiz_not_configured(monkeypatch):
    monkeypatch.setitem(config.app, "promo_publish_backend", "postiz")
    for key in ("postiz_api_url", "postiz_api_key", "postiz_youtube_integration_id"):
        monkeypatch.delitem(config.app, key, raising=False)

    result = publish.publish_video("/tmp/v.mp4", "제목", "설명", [])

    assert result["success"] is False
    assert "postiz_api_url" in result["error"]


def test_postiz_upload_then_post(postiz_config, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    requests_made = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, headers=None, files=None, json=None, timeout=None):
        requests_made.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/upload"):
            assert files is not None
            return _Resp({"id": "media-1", "path": "http://up/uploads/v.mp4"})
        assert url.endswith("/posts")
        return _Resp([{"postId": "post-1"}])

    monkeypatch.setattr(publish.requests, "post", fake_post)

    result = publish.publish_video(
        str(video), "제목" * 60, "설명 #태그", ["태그"], privacy_status="public"
    )

    assert result["success"] is True
    assert result["request_id"] == "post-1"
    assert requests_made[0]["headers"]["Authorization"] == "key-1"

    payload = requests_made[1]["json"]
    assert payload["type"] == "now"
    post = payload["posts"][0]
    assert post["integration"]["id"] == "int-1"
    assert post["value"][0]["image"][0]["id"] == "media-1"
    # SSRF 차단 우회: '/uploads' 접두 제거한 로컬 경로 전달 (자체 URL 아님)
    assert post["value"][0]["image"][0]["path"] == "/v.mp4"
    assert post["settings"]["__type"] == "youtube"
    assert len(post["settings"]["title"]) <= 100  # 제목 상한 절단
    assert post["settings"]["selfDeclaredMadeForKids"] == "no"
    # 현재 Postiz DTO: tags 는 {value, label} 객체 배열 (문자열 배열 아님)
    assert post["settings"]["tags"] == [{"value": "태그", "label": "태그"}]


def test_postiz_http_failure_converges(postiz_config, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")

    def broken_post(url, **kwargs):
        raise publish.requests.ConnectionError("connection refused")

    monkeypatch.setattr(publish.requests, "post", broken_post)

    result = publish.publish_video(str(video), "제목", "설명", [])

    assert result["success"] is False
    assert "refused" in result["error"]
