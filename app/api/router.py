from fastapi import APIRouter
from app.api.endpoints import health, matching, pipeline, pncp, public, portes

router = APIRouter()

router.include_router(health.router)
router.include_router(pipeline.router)
router.include_router(pncp.router)
router.include_router(matching.router)
router.include_router(matching.internal_router)
router.include_router(public.router)
router.include_router(portes.router)
