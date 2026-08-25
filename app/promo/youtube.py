"""Postiz 게시 후 YouTube 후속 처리(게시 감지/구매 댓글)."""

from __future__ import annotations

import time

import requests

from app.config import config

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/youtube/v3"


class YoutubeEngagementError(RuntimeError):
    pass


def configured() -> bool:
    return all(
        str(config.app.get(name, "")).strip()
        for name in (
            "youtube_oauth_client_id",
            "youtube_oauth_client_secret",
            "youtube_oauth_refresh_token",
            "youtube_channel_id",
        )
    )


def _access_token() -> str:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": config.app.get("youtube_oauth_client_id"),
            "client_secret": config.app.get("youtube_oauth_client_secret"),
            "refresh_token": config.app.get("youtube_oauth_refresh_token"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    token = str(response.json().get("access_token", "")).strip()
    if not token:
        raise YoutubeEngagementError("YouTube access token 응답이 비었습니다")
    return token


def _get(path: str, token: str, params: dict) -> dict:
    response = requests.get(
        f"{API_URL}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def latest_video_id(title: str, token: str) -> str | None:
    channels = _get(
        "channels",
        token,
        {"part": "contentDetails", "id": config.app.get("youtube_channel_id")},
    )
    items = channels.get("items") or []
    if not items:
        raise YoutubeEngagementError("설정한 YouTube 채널을 찾지 못했습니다")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    playlist = _get(
        "playlistItems",
        token,
        {"part": "snippet", "playlistId": uploads, "maxResults": 10},
    )
    for item in playlist.get("items") or []:
        if str(item.get("snippet", {}).get("title", "")).strip() == title.strip():
            return item["snippet"]["resourceId"]["videoId"]
    return None


def post_comment(video_id: str, text: str, token: str) -> str:
    response = requests.post(
        f"{API_URL}/commentThreads",
        headers={"Authorization": f"Bearer {token}"},
        params={"part": "snippet"},
        json={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json().get("id", ""))


def wait_and_comment(
    title: str,
    comment: str,
    *,
    timeout_s: int = 300,
    interval_s: int = 10,
) -> dict:
    if not configured():
        raise YoutubeEngagementError("YouTube 댓글 자동화 OAuth 설정이 없습니다")
    token = _access_token()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        video_id = latest_video_id(title, token)
        if video_id:
            return {
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "comment_id": post_comment(video_id, comment, token),
            }
        time.sleep(interval_s)
    raise YoutubeEngagementError(f"게시된 YouTube 영상을 {timeout_s}초 안에 찾지 못했습니다")
