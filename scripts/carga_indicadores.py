#!/usr/bin/env python3
"""Executa carga concorrente nas consultas locais de indicadores TCE."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import threading
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import requests

try:
    import psutil
except ImportError:  # pragma: no cover - a imagem instala requirements.txt
    psutil = None


ELEMENTOS_DESPESA = ("30", "32", "35", "39", "40", "51", "52")
SERVER_TIMING_DB_PATTERN = re.compile(r"(?:^|,)\s*db;dur=([0-9]+(?:\.[0-9]+)?)")
_thread_local = threading.local()


@dataclass(frozen=True)
class Cenario:
    nome: str
    municipio: str
    parametros: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ResultadoRequisicao:
    cenario: str
    municipio: str
    status_code: int | None
    tempo_resposta_ms: float
    tempo_banco_ms: float | None
    consultas_banco: int | None
    cache: str
    erro: str | None

    @property
    def sucesso(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class CacheAquecidoIndisponivelError(RuntimeError):
    """O modo warm nao conseguiu reutilizar as entradas preparadas."""


def percentil(valores: list[float], fracao: float) -> float:
    """Calcula o percentil pelo metodo nearest-rank."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, math.ceil(fracao * len(ordenados)) - 1)
    return ordenados[max(0, indice)]


def montar_cenarios(
    municipios: list[str],
    *,
    data_inicial: str,
    data_final: str,
) -> list[Cenario]:
    cenarios: list[Cenario] = []
    for municipio in municipios:
        base = (
            ("start_date", data_inicial),
            ("end_date", data_final),
            ("uf", "CE"),
            ("municipality_tce_code", municipio),
            ("ranking_limit", "10"),
        )
        cenarios.extend(
            (
                Cenario("distribuicoes_gerais", municipio, base),
                Cenario(
                    "distribuicao_mei",
                    municipio,
                    base + (("company_sizes", "MEI"),),
                ),
                Cenario(
                    "distribuicao_me",
                    municipio,
                    base + (("company_sizes", "ME"),),
                ),
                Cenario(
                    "compras_locais",
                    municipio,
                    base
                    + (("supplier_origins", "NO_MUNICIPIO_COMPRADOR"),),
                ),
                Cenario(
                    "distribuicao_elementos_despesa",
                    municipio,
                    base
                    + tuple(
                        ("expense_element_codes", codigo)
                        for codigo in ELEMENTOS_DESPESA
                    ),
                ),
            )
        )
    return cenarios


def _sessao_http() -> requests.Session:
    sessao = getattr(_thread_local, "sessao_http", None)
    if sessao is None:
        sessao = requests.Session()
        _thread_local.sessao_http = sessao
    return sessao


def _tempo_banco(headers: requests.structures.CaseInsensitiveDict[str]) -> float | None:
    correspondencia = SERVER_TIMING_DB_PATTERN.search(headers.get("Server-Timing", ""))
    return float(correspondencia.group(1)) if correspondencia else None


def _estado_cache(headers: requests.structures.CaseInsensitiveDict[str]) -> str:
    # Starlette pode preservar duas ocorrencias equivalentes do mesmo header.
    # Requests as combina com virgula; uma ocorrencia basta para classificar.
    return headers.get("X-Cache", "NAO_INFORMADO").split(",", maxsplit=1)[0].strip().upper()


def executar_requisicao(
    *,
    base_url: str,
    cenario: Cenario,
    timeout_segundos: float,
    modo_cache: str,
    headers_adicionais: dict[str, str],
) -> ResultadoRequisicao:
    url = (
        f"{base_url.rstrip('/')}/pipeline/tce/analise-territorial?"
        f"{urlencode(cenario.parametros)}"
    )
    headers = {
        "Accept": "application/json",
        "X-Load-Test": "1",
        **headers_adicionais,
    }
    if modo_cache == "bypass":
        headers["X-Force-Refresh"] = "1"

    inicio = perf_counter()
    try:
        resposta = _sessao_http().get(url, headers=headers, timeout=timeout_segundos)
        _ = resposta.content
        duracao_ms = (perf_counter() - inicio) * 1000
        consultas = resposta.headers.get("X-Database-Queries")
        erro = None
        if not 200 <= resposta.status_code < 300:
            erro = f"HTTP {resposta.status_code}"
        return ResultadoRequisicao(
            cenario=cenario.nome,
            municipio=cenario.municipio,
            status_code=resposta.status_code,
            tempo_resposta_ms=duracao_ms,
            tempo_banco_ms=_tempo_banco(resposta.headers),
            consultas_banco=int(consultas) if consultas and consultas.isdigit() else None,
            cache=_estado_cache(resposta.headers),
            erro=erro,
        )
    except requests.RequestException as error:
        return ResultadoRequisicao(
            cenario=cenario.nome,
            municipio=cenario.municipio,
            status_code=None,
            tempo_resposta_ms=(perf_counter() - inicio) * 1000,
            tempo_banco_ms=None,
            consultas_banco=None,
            cache="NAO_INFORMADO",
            erro=type(error).__name__,
        )


def _metricas_resultados(
    resultados: list[ResultadoRequisicao],
    *,
    duracao_lote_segundos: float | None = None,
) -> dict[str, Any]:
    latencias = [item.tempo_resposta_ms for item in resultados]
    tempos_banco = [
        item.tempo_banco_ms for item in resultados if item.tempo_banco_ms is not None
    ]
    sucessos = sum(item.sucesso for item in resultados)
    total = len(resultados)
    metricas: dict[str, Any] = {
        "requisicoes": total,
        "sucessos": sucessos,
        "erros": total - sucessos,
        "taxa_erro_percentual": round((total - sucessos) / total * 100, 3)
        if total
        else 0.0,
        "tempo_resposta_ms": {
            "media": round(statistics.fmean(latencias), 3) if latencias else 0.0,
            "p50": round(percentil(latencias, 0.50), 3),
            "p95": round(percentil(latencias, 0.95), 3),
            "p99": round(percentil(latencias, 0.99), 3),
            "maximo": round(max(latencias), 3) if latencias else 0.0,
        },
        "banco": {
            "amostras": len(tempos_banco),
            "cobertura_percentual": round(len(tempos_banco) / total * 100, 3)
            if total
            else 0.0,
            "tempo_ms_total": round(sum(tempos_banco), 3),
            "tempo_ms_media": round(statistics.fmean(tempos_banco), 3)
            if tempos_banco
            else 0.0,
            "tempo_ms_p95": round(percentil(tempos_banco, 0.95), 3),
            "consultas_total": sum(
                item.consultas_banco or 0 for item in resultados
            ),
        },
        "cache": dict(sorted(Counter(item.cache for item in resultados).items())),
        "status_http": dict(
            sorted(
                Counter(
                    str(item.status_code) if item.status_code is not None else "SEM_RESPOSTA"
                    for item in resultados
                ).items()
            )
        ),
        "erros_por_tipo": dict(
            sorted(Counter(item.erro for item in resultados if item.erro).items())
        ),
    }
    if duracao_lote_segundos is not None:
        metricas["duracao_lote_segundos"] = round(duracao_lote_segundos, 3)
        metricas["throughput_requisicoes_segundo"] = round(
            total / duracao_lote_segundos if duracao_lote_segundos else 0.0,
            3,
        )
    return metricas


class MonitorRecursos:
    def __init__(self, alvos: dict[str, int], intervalo_segundos: float = 0.1) -> None:
        self.alvos = alvos
        self.intervalo_segundos = intervalo_segundos
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._amostras: dict[str, list[dict[str, float]]] = defaultdict(list)
        self._erros: dict[str, str] = {}
        self._processos: dict[str, Any] = {}
        self._processos_conhecidos: dict[str, dict[int, Any]] = defaultdict(dict)

    def iniciar(self) -> None:
        if psutil is None:
            self._erros["monitor"] = "psutil nao instalado"
            return
        for nome, pid in self.alvos.items():
            try:
                processo = psutil.Process(pid)
                processo.cpu_percent(interval=None)
                self._processos[nome] = processo
                self._processos_conhecidos[nome][processo.pid] = processo
            except (psutil.Error, OSError) as error:
                self._erros[nome] = type(error).__name__
        self._thread = threading.Thread(target=self._executar, daemon=True)
        self._thread.start()

    def _arvore(self, raiz: Any) -> list[Any]:
        try:
            return [raiz, *raiz.children(recursive=True)]
        except (psutil.Error, OSError):
            return [raiz]

    def _amostrar(self) -> None:
        for nome, raiz in self._processos.items():
            cpu = 0.0
            rss = 0
            pids_ativos: set[int] = set()
            for processo_descoberto in self._arvore(raiz):
                pids_ativos.add(processo_descoberto.pid)
                processo = self._processos_conhecidos[nome].get(
                    processo_descoberto.pid
                )
                processo_novo = processo is None
                if processo_novo:
                    processo = processo_descoberto
                    self._processos_conhecidos[nome][processo.pid] = processo
                try:
                    cpu_atual = processo.cpu_percent(interval=None)
                    if not processo_novo:
                        cpu += cpu_atual
                    rss += processo.memory_info().rss
                except (psutil.Error, OSError):
                    continue
            self._processos_conhecidos[nome] = {
                pid: processo
                for pid, processo in self._processos_conhecidos[nome].items()
                if pid in pids_ativos
            }
            self._amostras[nome].append(
                {
                    "cpu_percent": cpu,
                    "rss_mb": rss / (1024 * 1024),
                }
            )

    def _executar(self) -> None:
        while not self._parar.wait(self.intervalo_segundos):
            self._amostrar()

    def finalizar(self) -> dict[str, Any]:
        if self._thread is not None:
            self._parar.set()
            self._thread.join(timeout=2)
            self._amostrar()
        resumo: dict[str, Any] = {}
        for nome in self.alvos:
            amostras = self._amostras.get(nome, [])
            if not amostras:
                resumo[nome] = {
                    "disponivel": False,
                    "motivo": self._erros.get(nome, self._erros.get("monitor", "sem amostras")),
                }
                continue
            cpus = [item["cpu_percent"] for item in amostras]
            memorias = [item["rss_mb"] for item in amostras]
            resumo[nome] = {
                "disponivel": True,
                "amostras": len(amostras),
                "cpu_percent_media": round(statistics.fmean(cpus), 3),
                "cpu_percent_maxima": round(max(cpus), 3),
                "memoria_rss_mb_media": round(statistics.fmean(memorias), 3),
                "memoria_rss_mb_maxima": round(max(memorias), 3),
            }
        return resumo


def executar_carga(
    *,
    base_url: str,
    cenarios: list[Cenario],
    requisicoes: int,
    concorrencia: int,
    timeout_segundos: float,
    modo_cache: str,
    aquecimento_por_cenario: int,
    headers_adicionais: dict[str, str],
    pids_monitorados: dict[str, int],
) -> tuple[list[ResultadoRequisicao], float, dict[str, Any]]:
    modo_aquecimento = "normal" if modo_cache == "warm" else modo_cache
    repeticoes_aquecimento = max(
        aquecimento_por_cenario,
        1 if modo_cache == "warm" else 0,
    )
    for _ in range(repeticoes_aquecimento):
        for cenario in cenarios:
            executar_requisicao(
                base_url=base_url,
                cenario=cenario,
                timeout_segundos=timeout_segundos,
                modo_cache=modo_aquecimento,
                headers_adicionais=headers_adicionais,
            )

    if modo_cache == "warm":
        verificacoes_cache = [
            executar_requisicao(
                base_url=base_url,
                cenario=cenario,
                timeout_segundos=timeout_segundos,
                modo_cache="normal",
                headers_adicionais=headers_adicionais,
            )
            for cenario in cenarios
        ]
        sem_hit = [
            f"{item.municipio}/{item.cenario}:{item.cache}"
            for item in verificacoes_cache
            if item.cache != "HIT" or not item.sucesso
        ]
        if sem_hit:
            amostra = ", ".join(sem_hit[:5])
            raise CacheAquecidoIndisponivelError(
                "cache aquecido nao ficou disponivel apos o aquecimento; "
                f"verifique a migration 0018_api_response_cache ({amostra})"
            )

    monitor = MonitorRecursos({"cliente": os.getpid(), **pids_monitorados})
    monitor.iniciar()
    inicio_lote = perf_counter()
    resultados: list[ResultadoRequisicao] = []
    with ThreadPoolExecutor(max_workers=concorrencia) as executor:
        futuros = [
            executor.submit(
                executar_requisicao,
                base_url=base_url,
                cenario=cenarios[indice % len(cenarios)],
                timeout_segundos=timeout_segundos,
                modo_cache=modo_cache if modo_cache != "warm" else "normal",
                headers_adicionais=headers_adicionais,
            )
            for indice in range(requisicoes)
        ]
        for futuro in as_completed(futuros):
            resultados.append(futuro.result())
    duracao = perf_counter() - inicio_lote
    recursos = monitor.finalizar()
    return resultados, duracao, recursos


def montar_relatorio(
    *,
    resultados: list[ResultadoRequisicao],
    duracao_lote_segundos: float,
    recursos: dict[str, Any],
    base_url: str,
    municipios: list[str],
    data_inicial: str,
    data_final: str,
    requisicoes: int,
    concorrencia: int,
    modo_cache: str,
    p95_alvo_ms: float,
    erro_maximo_percentual: float,
) -> dict[str, Any]:
    por_cenario: dict[str, list[ResultadoRequisicao]] = defaultdict(list)
    por_municipio: dict[str, list[ResultadoRequisicao]] = defaultdict(list)
    for resultado in resultados:
        por_cenario[resultado.cenario].append(resultado)
        por_municipio[resultado.municipio].append(resultado)

    resumo = _metricas_resultados(
        resultados,
        duracao_lote_segundos=duracao_lote_segundos,
    )
    p95_observado = resumo["tempo_resposta_ms"]["p95"]
    erro_observado = resumo["taxa_erro_percentual"]
    municipios_com_sucesso = {item.municipio for item in resultados if item.sucesso}
    aceite_municipios = len(municipios_com_sucesso) >= 2
    aceite_desempenho = (
        p95_observado < p95_alvo_ms
        and erro_observado <= erro_maximo_percentual
    )
    return {
        "schema_version": "1.0",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "configuracao": {
            "base_url": base_url,
            "municipios": municipios,
            "data_inicial": data_inicial,
            "data_final": data_final,
            "requisicoes": requisicoes,
            "concorrencia": concorrencia,
            "modo_cache": modo_cache,
            "endpoint": "/pipeline/tce/analise-territorial",
            "p95_alvo_ms": p95_alvo_ms,
            "erro_maximo_percentual": erro_maximo_percentual,
        },
        "resumo": resumo,
        "por_cenario": {
            nome: _metricas_resultados(itens)
            for nome, itens in sorted(por_cenario.items())
        },
        "por_municipio": {
            nome: _metricas_resultados(itens)
            for nome, itens in sorted(por_municipio.items())
        },
        "recursos": recursos,
        "criterios_aceite": {
            "multiplos_municipios": {
                "atendido": aceite_municipios,
                "quantidade": len(municipios_com_sucesso),
            },
            "tempo_resposta_sob_carga_documentado": {
                "atendido": any(item.sucesso for item in resultados),
                "p95_observado_ms": p95_observado,
            },
            "meta_operacional": {
                "atendido": aceite_desempenho,
                "regra": (
                    f"p95 < {p95_alvo_ms:g} ms e taxa de erro <= "
                    f"{erro_maximo_percentual:g}%"
                ),
            },
        },
    }


def renderizar_markdown(relatorio: dict[str, Any]) -> str:
    config = relatorio["configuracao"]
    resumo = relatorio["resumo"]
    latencia = resumo["tempo_resposta_ms"]
    banco = resumo["banco"]
    aceite = relatorio["criterios_aceite"]
    linhas = [
        "# Resultado do teste de carga dos indicadores",
        "",
        f"Executado em `{relatorio['gerado_em']}` contra `{config['base_url']}`.",
        "",
        "## Configuração",
        "",
        f"- Municípios TCE: {', '.join(config['municipios'])}",
        f"- Período: {config['data_inicial']} a {config['data_final']}",
        f"- Requisições: {config['requisicoes']}",
        f"- Concorrência: {config['concorrencia']}",
        f"- Cache: {config['modo_cache']}",
        "",
        "## Resultado consolidado",
        "",
        "| Métrica | Resultado |",
        "| --- | ---: |",
        f"| Tempo médio | {latencia['media']:.3f} ms |",
        f"| p50 | {latencia['p50']:.3f} ms |",
        f"| p95 | {latencia['p95']:.3f} ms |",
        f"| p99 | {latencia['p99']:.3f} ms |",
        f"| Throughput | {resumo['throughput_requisicoes_segundo']:.3f} req/s |",
        f"| Taxa de erro | {resumo['taxa_erro_percentual']:.3f}% |",
        f"| Tempo médio do banco | {banco['tempo_ms_media']:.3f} ms |",
        f"| p95 do banco | {banco['tempo_ms_p95']:.3f} ms |",
        f"| Cobertura da medição do banco | {banco['cobertura_percentual']:.3f}% |",
        "",
        "## Por município",
        "",
        "| Município TCE | Requisições | Erros | Média | p95 | p99 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for municipio, metricas in relatorio["por_municipio"].items():
        tempos = metricas["tempo_resposta_ms"]
        linhas.append(
            f"| {municipio} | {metricas['requisicoes']} | {metricas['erros']} | "
            f"{tempos['media']:.3f} ms | {tempos['p95']:.3f} ms | "
            f"{tempos['p99']:.3f} ms |"
        )
    linhas.extend(
        (
            "",
            "## Recursos",
            "",
            "| Processo | CPU média | CPU máxima | RSS média | RSS máxima |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for nome, recurso in relatorio["recursos"].items():
        if not recurso.get("disponivel"):
            linhas.append(f"| {nome} | indisponível | - | - | - |")
            continue
        linhas.append(
            f"| {nome} | {recurso['cpu_percent_media']:.3f}% | "
            f"{recurso['cpu_percent_maxima']:.3f}% | "
            f"{recurso['memoria_rss_mb_media']:.3f} MB | "
            f"{recurso['memoria_rss_mb_maxima']:.3f} MB |"
        )
    linhas.extend(
        (
            "",
            "## Critérios de aceite",
            "",
            f"- Múltiplos municípios: {'ATENDIDO' if aceite['multiplos_municipios']['atendido'] else 'NÃO ATENDIDO'}.",
            f"- Tempo sob carga documentado: {'ATENDIDO' if aceite['tempo_resposta_sob_carga_documentado']['atendido'] else 'NÃO ATENDIDO'}.",
            f"- Meta operacional: {'ATENDIDA' if aceite['meta_operacional']['atendido'] else 'NÃO ATENDIDA'} ({aceite['meta_operacional']['regra']}).",
            "",
            "O tempo de banco é a soma das execuções SQL no banco principal por "
            "requisição. CPU pode ultrapassar 100% quando o processo usa mais de "
            "um núcleo. Resultados de cache aquecido e bypass devem ser comparados "
            "separadamente.",
            "",
        )
    )
    return "\n".join(linhas)


def _pares_validados(valores: list[str], *, nome_argumento: str) -> dict[str, str]:
    pares: dict[str, str] = {}
    for valor in valores:
        if "=" not in valor:
            raise ValueError(f"{nome_argumento} exige NOME=VALOR: {valor}")
        nome, conteudo = valor.split("=", maxsplit=1)
        if not nome.strip():
            raise ValueError(f"{nome_argumento} exige nome nao vazio")
        pares[nome.strip()] = conteudo
    return pares


def _pids(valores: list[str]) -> dict[str, int]:
    pares = _pares_validados(valores, nome_argumento="--monitorar-pid")
    try:
        return {nome: int(pid) for nome, pid in pares.items()}
    except ValueError as error:
        raise ValueError("--monitorar-pid exige PID inteiro") from error


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--municipios", default="010,057")
    parser.add_argument("--data-inicial", required=True)
    parser.add_argument("--data-final", required=True)
    parser.add_argument("--requisicoes", type=int, default=200)
    parser.add_argument("--concorrencia", type=int, default=20)
    parser.add_argument("--timeout-segundos", type=float, default=30)
    parser.add_argument(
        "--cache",
        choices=("bypass", "warm", "normal"),
        default="bypass",
        help="bypass mede o motor; warm preenche o cache antes; normal usa o estado atual.",
    )
    parser.add_argument("--aquecimento-por-cenario", type=int, default=1)
    parser.add_argument("--p95-alvo-ms", type=float, default=1500)
    parser.add_argument("--erro-maximo-percentual", type=float, default=0)
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NOME=VALOR",
        help="Cabeçalho adicional; valores não são gravados no relatório.",
    )
    parser.add_argument(
        "--monitorar-pid",
        action="append",
        default=[],
        metavar="NOME=PID",
        help="Monitora CPU/RSS do processo e de seus filhos.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(".local/carga-indicadores/resultado.json"),
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path(".local/carga-indicadores/resultado.md"),
    )
    return parser


def main() -> int:
    parser = criar_parser()
    args = parser.parse_args()
    municipios = list(
        dict.fromkeys(item.strip() for item in args.municipios.split(",") if item.strip())
    )
    if len(municipios) < 2:
        parser.error("--municipios deve informar ao menos dois codigos distintos")
    if any(not re.fullmatch(r"\d{3}", item) for item in municipios):
        parser.error("--municipios aceita codigos TCE de tres digitos separados por virgula")
    if args.requisicoes < 1 or args.concorrencia < 1:
        parser.error("--requisicoes e --concorrencia devem ser maiores que zero")
    if args.timeout_segundos <= 0 or args.aquecimento_por_cenario < 0:
        parser.error("timeout deve ser positivo e aquecimento nao pode ser negativo")
    try:
        headers = _pares_validados(args.header, nome_argumento="--header")
        pids = _pids(args.monitorar_pid)
    except ValueError as error:
        parser.error(str(error))

    cenarios = montar_cenarios(
        municipios,
        data_inicial=args.data_inicial,
        data_final=args.data_final,
    )
    try:
        resultados, duracao, recursos = executar_carga(
            base_url=args.base_url,
            cenarios=cenarios,
            requisicoes=args.requisicoes,
            concorrencia=min(args.concorrencia, args.requisicoes),
            timeout_segundos=args.timeout_segundos,
            modo_cache=args.cache,
            aquecimento_por_cenario=args.aquecimento_por_cenario,
            headers_adicionais=headers,
            pids_monitorados=pids,
        )
    except CacheAquecidoIndisponivelError as error:
        parser.exit(status=2, message=f"erro: {error}\n")
    relatorio = montar_relatorio(
        resultados=resultados,
        duracao_lote_segundos=duracao,
        recursos=recursos,
        base_url=args.base_url,
        municipios=municipios,
        data_inicial=args.data_inicial,
        data_final=args.data_final,
        requisicoes=args.requisicoes,
        concorrencia=min(args.concorrencia, args.requisicoes),
        modo_cache=args.cache,
        p95_alvo_ms=args.p95_alvo_ms,
        erro_maximo_percentual=args.erro_maximo_percentual,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(renderizar_markdown(relatorio), encoding="utf-8")
    print(json.dumps(relatorio["resumo"], ensure_ascii=False, indent=2))
    print(f"JSON: {args.output_json}")
    print(f"Markdown: {args.output_md}")
    return 0 if relatorio["criterios_aceite"]["meta_operacional"]["atendido"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
