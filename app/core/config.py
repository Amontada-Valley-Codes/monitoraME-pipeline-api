import os
from dotenv import load_dotenv

load_dotenv()

class ConfigError(RuntimeError):
    pass


def _env(key: str, *, strip_slash: bool = False) -> str:
    value = os.getenv(key)
    if value is None or not value.strip():
        raise ConfigError(f"Variavel de ambiente obrigatoria ausente: {key}")

    value = value.strip()
    return value.rstrip("/") if strip_slash else value


def _env_int(key: str, default: int, *, minimo: int) -> int:
    value = os.getenv(key, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigError(f"Variavel de ambiente invalida: {key}") from error
    if parsed < minimo:
        raise ConfigError(f"Variavel de ambiente invalida: {key}")
    return parsed


def _env_float(key: str, default: float, *, minimo: float) -> float:
    value = os.getenv(key, str(default)).strip()
    try:
        parsed = float(value)
    except ValueError as error:
        raise ConfigError(f"Variavel de ambiente invalida: {key}") from error
    if parsed < minimo:
        raise ConfigError(f"Variavel de ambiente invalida: {key}")
    return parsed


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Variavel de ambiente invalida: {key}")


def _env_status_codes(key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(key, ",".join(str(item) for item in default)).strip()
    try:
        status_codes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ConfigError(f"Variavel de ambiente invalida: {key}") from error
    if not status_codes or any(item < 100 or item > 599 for item in status_codes):
        raise ConfigError(f"Variavel de ambiente invalida: {key}")
    return status_codes


DATABASE_URL = _env("DATABASE_URL")
LOG_DATABASE_URL = _env("LOG_DATABASE_URL")
TCE_CE_BASE_URL = _env("TCE_CE_BASE_URL", strip_slash=True)
IBGE_LOCALIDADES_BASE_URL = _env("IBGE_LOCALIDADES_BASE_URL", strip_slash=True)
OPENCNPJ_BASE_URL = _env("OPENCNPJ_BASE_URL", strip_slash=True)

UF_PADRAO = _env("UF_PADRAO")
CODIGO_MUNICIPIO_TCE_PADRAO = _env("CODIGO_MUNICIPIO_TCE_PADRAO")

TCE_RETRY_MAX_ATTEMPTS = _env_int("TCE_RETRY_MAX_ATTEMPTS", 5, minimo=1)
TCE_RETRY_INITIAL_DELAY_SECONDS = _env_float(
    "TCE_RETRY_INITIAL_DELAY_SECONDS",
    2,
    minimo=0,
)
TCE_RETRY_MAX_DELAY_SECONDS = _env_float(
    "TCE_RETRY_MAX_DELAY_SECONDS",
    60,
    minimo=0,
)
TCE_RETRY_BACKOFF_MULTIPLIER = _env_float(
    "TCE_RETRY_BACKOFF_MULTIPLIER",
    2,
    minimo=1,
)
TCE_RETRY_JITTER = os.getenv("TCE_RETRY_JITTER", "full").strip().lower()
if not TCE_RETRY_JITTER:
    raise ConfigError("Variavel de ambiente invalida: TCE_RETRY_JITTER")
TCE_RETRY_RESPECT_RETRY_AFTER = _env_bool("TCE_RETRY_RESPECT_RETRY_AFTER", True)
TCE_RETRYABLE_STATUS_CODES = _env_status_codes(
    "TCE_RETRYABLE_STATUS_CODES",
    (408, 429, 500, 502, 503, 504),
)

# Escopo de despesas acompanhado pelo monitoraME. O codigo e a unica chave de
# filtro; a descricao fica apenas como metadado de apresentacao.
NATUREZAS_DESPESA_MONITORADAS = {
    "39": "Outros serviços de terceiros-pessoa jurídica",
    "51": "Obras e instalações",
    "30": "Material de consumo",
    "52": "Equipamentos e material permanente",
    "32": "Material, bem ou serviço para distribuição gratuita",
    "35": "Serviços de consultoria",
    "40": "Serviços de tecnologia da informação e comunicação pessoa jurídica",
}
CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS = tuple(NATUREZAS_DESPESA_MONITORADAS)
NATUREZAS_DESPESA_CONSIDERADAS = tuple(NATUREZAS_DESPESA_MONITORADAS.values())

# Contrato funcional do indicador de pagamentos do TCE-CE. Municipio,
# exercicio orcamentario (AAAA00) e competencia de corte (YYYY-MM) sao
# informados por execucao; estas constantes fixam a semantica financeira e
# cadastral compartilhada pelas etapas futuras do pipeline.
MEDIDA_FINANCEIRA_PAGAMENTOS_TCE = "valor_nota_pagamento"
ESTADO_NAO_ESTORNADO_PAGAMENTOS_TCE = 0
PORTE_ME_PUBLICO_CODIGO_TCE = "01"
PORTE_ME_PUBLICO_INCLUI_MEI = True
CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE = (
    "ME",
    "EPP",
    "DEMAIS",
    "NAO_IDENTIFICADO",
    "CPF",
    "DOCUMENTO_INVALIDO",
)
VERSAO_METODOLOGICA_EMPENHOS_TCE = "tce-empenhos-v1"
VERSAO_METODOLOGICA_PAGAMENTOS_TCE = "tce-pagamentos-v1"
