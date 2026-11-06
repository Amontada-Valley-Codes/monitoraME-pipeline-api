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


def _env_int(key: str, default: int, *, minimo: int = 1) -> int:
    value = os.getenv(key, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigError(f"Variavel {key} deve ser um inteiro.") from error
    if parsed < minimo:
        raise ConfigError(f"Variavel {key} deve ser maior ou igual a {minimo}.")
    return parsed


def _env_anos(key: str, default: str) -> tuple[int, ...]:
    value = os.getenv(key, default)
    try:
        anos = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    except ValueError as error:
        raise ConfigError(f"Variavel {key} deve conter anos separados por virgula.") from error
    if not anos or any(ano < 1900 or ano > 9999 for ano in anos):
        raise ConfigError(f"Variavel {key} contem exercicio invalido.")
    return anos


DATABASE_URL = _env("DATABASE_URL")
LOG_DATABASE_URL = _env("LOG_DATABASE_URL")
TCE_CE_BASE_URL = _env("TCE_CE_BASE_URL", strip_slash=True)
IBGE_LOCALIDADES_BASE_URL = _env("IBGE_LOCALIDADES_BASE_URL", strip_slash=True)
OPENCNPJ_BASE_URL = _env("OPENCNPJ_BASE_URL", strip_slash=True)

UF_PADRAO = _env("UF_PADRAO")
CODIGO_MUNICIPIO_TCE_PADRAO = _env("CODIGO_MUNICIPIO_TCE_PADRAO")

# Scheduler TCE: domingo (weekday 6), 02:00, America/Fortaleza.
TCE_SCHEDULE_YEARS = _env_anos("TCE_SCHEDULE_YEARS", "2024,2025,2026")
TCE_SCHEDULE_WEEKDAY = _env_int("TCE_SCHEDULE_WEEKDAY", 6, minimo=0)
if TCE_SCHEDULE_WEEKDAY > 6:
    raise ConfigError("TCE_SCHEDULE_WEEKDAY deve estar entre 0 (segunda) e 6 (domingo).")
TCE_SCHEDULE_HOUR = _env_int("TCE_SCHEDULE_HOUR", 2, minimo=0)
if TCE_SCHEDULE_HOUR > 23:
    raise ConfigError("TCE_SCHEDULE_HOUR deve estar entre 0 e 23.")
TCE_SCHEDULE_TIMEZONE = os.getenv("TCE_SCHEDULE_TIMEZONE", "America/Fortaleza").strip()
TCE_SCHEDULER_INTERVAL_SECONDS = _env_int("TCE_SCHEDULER_INTERVAL_SECONDS", 60)
TCE_WORKER_IDLE_SECONDS = _env_int("TCE_WORKER_IDLE_SECONDS", 2)
TCE_WORKER_CONCURRENCY = _env_int("TCE_WORKER_CONCURRENCY", 2)
TCE_JOB_MAX_ATTEMPTS = _env_int("TCE_JOB_MAX_ATTEMPTS", 5)
TCE_JOB_LEASE_SECONDS = _env_int("TCE_JOB_LEASE_SECONDS", 1800)
TCE_INCREMENTAL_OVERLAP_MONTHS = _env_int("TCE_INCREMENTAL_OVERLAP_MONTHS", 2)

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
