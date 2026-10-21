from fastapi import APIRouter
from app.pncp.observability import metricas


router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    summary="Verifica saude da API",
    response_description="Objeto com `status`, igual a `healthy` quando a API está respondendo.",
)
async def health():
    return {"status": "healthy"}


@router.get(
    "/health/pncp",
    summary="Expõe saúde local do pipeline PNCP",
    response_description="Objeto com `status` da API e `pncp` com métricas locais do pipeline.",
)
async def health_pncp():
    """Consulta somente PostgreSQL; indisponibilidade do PNCP não afeta esta rota."""
    return {"status": "healthy", "pncp": metricas()}
