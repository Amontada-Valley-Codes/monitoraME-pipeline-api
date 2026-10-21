from fastapi import APIRouter
from app.pncp.observability import metricas


router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    summary="Verifica saude da API",
    response_description="Status operacional da API.",
)
async def health():
    return {"status": "healthy"}


@router.get("/health/pncp", summary="Expõe saúde local do pipeline PNCP")
async def health_pncp():
    """Consulta somente PostgreSQL; indisponibilidade do PNCP não afeta esta rota."""
    return {"status": "healthy", "pncp": metricas()}
