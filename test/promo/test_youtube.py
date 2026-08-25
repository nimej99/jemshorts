from app.config import config
from app.promo import youtube


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _configure(monkeypatch):
    for key, value in {
        "youtube_oauth_client_id": "client",
        "youtube_oauth_client_secret": "secret",
        "youtube_oauth_refresh_token": "refresh",
        "youtube_channel_id": "channel",
    }.items():
        monkeypatch.setitem(config.app, key, value)


def test_waits_for_upload_and_posts_purchase_comment(monkeypatch):
    _configure(monkeypatch)
    calls = {"playlist": 0, "comment": None}

    monkeypatch.setattr(youtube.requests, "post", lambda *a, **k: _Resp({"access_token": "token"}))

    def fake_get(url, **kwargs):
        if url.endswith("/channels"):
            return _Resp(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "up"}}}]}
            )
        calls["playlist"] += 1
        title = "다른 영상" if calls["playlist"] == 1 else "목표 영상"
        return _Resp(
            {
                "items": [
                    {
                        "snippet": {
                            "title": title,
                            "resourceId": {"videoId": "video-1"},
                        }
                    }
                ]
            }
        )

    def fake_post(url, **kwargs):
        if url == youtube.TOKEN_URL:
            return _Resp({"access_token": "token"})
        calls["comment"] = kwargs["json"]
        return _Resp({"id": "comment-1"})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    monkeypatch.setattr(youtube.requests, "post", fake_post)
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)

    result = youtube.wait_and_comment("목표 영상", "구매 링크", timeout_s=2, interval_s=0)

    assert result["video_id"] == "video-1"
    assert result["comment_id"] == "comment-1"
    assert calls["comment"]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == "구매 링크"
