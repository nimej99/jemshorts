"""업로드 백엔드 라우터: upload_post(기본) | postiz(셀프호스트).

- upload_post: 코어 서비스(upload-post.com, 유료 서드파티) 그대로 위임.
- postiz: 셀프호스트 Postiz(AGPL-3.0) 의 공개 API 를 HTTP 로 호출한다.
  별도 프로세스 호출이라 라이선스 결합 없음 (docs/FORK_NOTES.md 등재).
  흐름 (docs.postiz.com/public-api 기준):
    1) POST {base}/upload  (multipart file) -> {"id", "path"}
    2) POST {base}/posts   (type "now", youtube settings __type)

config:
  promo_publish_backend            "upload_post"(기본) | "postiz"
  postiz_api_url                   예: http://localhost:4007/api/public/v1 (/api 프리픽스 필수)
  postiz_api_key                   Settings > Developers > Public API 키
  postiz_youtube_integration_id    GET {base}/integrations 로 확인

반환 계약은 upload_post 와 동일하게 {"success": bool, ...} 로 수렴한다
(호출자 = api.upload_plan / scheduler 오토파일럿, 실패 시 502 승격).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests
from loguru import logger

from app.config import config

BACKEND_UPLOAD_POST = "upload_post"
BACKEND_POSTIZ = "postiz"
_POSTIZ_TIMEOUT_S = 300


def backend() -> str:
    value = str(config.app.get("promo_publish_backend", BACKEND_UPLOAD_POST)).strip()
    return value if value in (BACKEND_UPLOAD_POST, BACKEND_POSTIZ) else BACKEND_UPLOAD_POST


def _postiz_config() -> tuple[str, str, str] | None:
    base = str(config.app.get("postiz_api_url", "")).strip().rstrip("/")
    api_key = str(config.app.get("postiz_api_key", "")).strip()
    integration_id = str(
        config.app.get("postiz_youtube_integration_id", "")
    ).strip()
    if not base or not api_key or not integration_id:
        return None
    return base, api_key, integration_id


def _postiz_publish(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
) -> dict:
    conf = _postiz_config()
    if conf is None:
        return {
            "success": False,
            "backend": BACKEND_POSTIZ,
            "error": (
                "Postiz 미설정: config 에 postiz_api_url / postiz_api_key / "
                "postiz_youtube_integration_id 를 설정하세요"
            ),
        }
    base, api_key, integration_id = conf
    headers = {"Authorization": api_key}

    try:
        with open(video_path, "rb") as fh:
            upload_resp = requests.post(
                f"{base}/upload",
                headers=headers,
                files={"file": (os.path.basename(video_path), fh, "video/mp4")},
                timeout=_POSTIZ_TIMEOUT_S,
            )
        upload_resp.raise_for_status()
        media = upload_resp.json()

        payload = {
            "type": "now",
            "date": datetime.now(timezone.utc).isoformat(),
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [
                        {
                            "content": description,
                            "image": [
                                {"id": media.get("id"), "path": media.get("path")}
                            ],
                        }
                    ],
                    "settings": {
                        "__type": "youtube",
                        "title": title[:100],
                        "type": privacy_status,
                        "tags": tags,
                        "selfDeclaredMadeForKids": False,
                    },
                }
            ],
        }
        post_resp = requests.post(
            f"{base}/posts",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=_POSTIZ_TIMEOUT_S,
        )
        post_resp.raise_for_status()
        result = post_resp.json()
    except (OSError, requests.RequestException, ValueError) as exc:
        logger.error(f"postiz 업로드 실패: {exc}")
        return {"success": False, "backend": BACKEND_POSTIZ, "error": str(exc)}

    # posts 응답은 생성된 포스트(들)의 id 목록/객체 — 대표 id 를 request_id 로 노출
    post_id = None
    if isinstance(result, list) and result:
        post_id = (result[0] or {}).get("postId") or (result[0] or {}).get("id")
    elif isinstance(result, dict):
        post_id = result.get("postId") or result.get("id")
    return {
        "success": True,
        "backend": BACKEND_POSTIZ,
        "request_id": post_id,
        "raw": result,
    }


def publish_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "public",
    platforms: list[str] | None = None,
) -> dict:
    """설정된 백엔드로 영상을 게시한다. 반환: {"success": bool, ...}.

    postiz 백엔드는 유튜브 단일 채널(integration_id)로 게시한다 —
    platforms 인자는 upload_post 백엔드에서만 의미가 있다.
    """
    if backend() == BACKEND_POSTIZ:
        return _postiz_publish(video_path, title, description, tags, privacy_status)

    from app.services import upload_post  # 지연 임포트 (코어 서비스)

    result = upload_post.cross_post_video(
        video_path,
        title,
        platforms=platforms or ["youtube"],
        youtube_extra={
            "youtube_title": title,
            "youtube_description": description,
            "tags": tags,
            "privacyStatus": privacy_status,
        },
    )
    return {**result, "backend": BACKEND_UPLOAD_POST}
