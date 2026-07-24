"""Application configuration - root APIRouter.

Defines all FastAPI application endpoints.

Resources:
    1. https://fastapi.tiangolo.com/tutorial/bigger-applications

"""

from fastapi import APIRouter

from app.controllers.v1 import llm, video
from app.promo import api as promo_api

root_api_router = APIRouter()
# v1
root_api_router.include_router(video.router)
root_api_router.include_router(llm.router)
# promo-shorts (포크 신규 계층 — docs/FORK_NOTES.md 코어 수정 예외 등재)
root_api_router.include_router(promo_api.router)
