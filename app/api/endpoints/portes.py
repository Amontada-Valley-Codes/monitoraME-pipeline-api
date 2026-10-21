"""Consulta somente leitura das fotografias cadastrais publicadas."""
from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from app.api.errors import obter_request_id, resposta_erro
from app.pipeline import portes_historicos as service

router = APIRouter(prefix="/pipeline/tce/portes", tags=["Porte empresarial histórico"])


def _executar(request, response, fn):
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        return fn()
    except ValueError as error:
        return resposta_erro(status_code=422, code="VALIDATION_ERROR", message=str(error),
                             details=[], request_id=request_id)
    except LookupError as error:
        return resposta_erro(status_code=404, code="NOT_FOUND", message=str(error),
                             details=[], request_id=request_id)
    except SQLAlchemyError:
        return resposta_erro(status_code=503, code="DATABASE_UNAVAILABLE", message="Banco indisponivel.",
                             details=[], request_id=request_id)


@router.get(
    "/competencias",
    summary="Lista publicações mensais e cobertura da amostra",
    description="Retorna as competências publicadas, sua cobertura e, opcionalmente, versões anteriores.",
    response_description=(
        "Objeto com `items` de publicações: competência, versão, fonte, cobertura, "
        "procedência e artefatos; inclui política temporal e competência final consultada."
    ),
)
def competencias(request: Request, response: Response,
                 competencia_final: str | None = Query(None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
                 incluir_versoes_anteriores: bool = False):
    return _executar(request, response, lambda:service.publicacoes(
        competencia_final, todas_versoes=incluir_versoes_anteriores))


@router.get(
    "/historico/{cnpj}",
    summary="Consulta histórico mensal de porte por CNPJ",
    description="Retorna as fotografias cadastrais da raiz informada no intervalo solicitado, incluindo lacunas explícitas.",
    response_description=(
        "Objeto com CNPJ e raiz consultados, `items` por competência com porte, indicador "
        "MEI, status e publicação de origem; `competencias_sem_publicacao` explicita lacunas."
    ),
)
def historico(cnpj: str, request: Request, response: Response,
              competencia_inicial: str | None = Query(None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
              competencia_final: str | None = Query(None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")):
    return _executar(request, response, lambda:service.historico(
        cnpj, competencia_inicial, competencia_final))


@router.get(
    "/versoes/{versao}/{cnpj}",
    summary="Consulta uma versão histórica de porte por CNPJ",
    description="Reproduz a observação do fornecedor na versão publicada informada.",
    response_description=(
        "Objeto com o CNPJ e a raiz consultados e um item contendo porte, indicador MEI, "
        "status e metadados da publicação correspondente à versão solicitada."
    ),
)
def versao(versao: str, cnpj: str, request: Request, response: Response):
    return _executar(request, response, lambda:service.historico(cnpj, versao=versao))
