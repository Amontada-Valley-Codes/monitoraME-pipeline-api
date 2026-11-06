"""Publicacao atomica e consulta de retratos mensais com lacunas explicitas."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, or_, select, text, update
from app.core import orm
from app.core.porte_models import PortePublicacao, PorteObservacao

PORTES = {"00": "NAO_INFORMADO", "01": "ME", "03": "EPP", "05": "DEMAIS"}
FONTE = "RECEITA_FEDERAL_DADOS_ABERTOS"
POLITICA = "COMPETENCIA_EXATA_SEM_PREENCHIMENTO"


def competencia(valor: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", valor) or valor[:4] == "0000":
        raise ValueError("Competencia invalida; use YYYY-MM.")
    return valor


def mes_atual() -> str:
    return datetime.now(ZoneInfo("America/Fortaleza")).strftime("%Y-%m")


def _hash(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def importar(resultado: str | Path, *, workspace: str | Path = ".") -> list[dict]:
    """Importa somente piloto concluido; verifica arquivos e publica em uma transacao."""
    raiz_workspace = Path(workspace).resolve()
    path = Path(resultado).resolve()
    bruto = path.read_bytes()
    report = json.loads(bruto)
    if report.get("status") != "VALIDACAO_MENSAL_CONCLUIDA":
        raise ValueError("Somente resultados integralmente validados podem ser publicados.")
    dbpath = path.with_name("observacoes.sqlite3")
    sqlite_hash = _hash(dbpath)
    with sqlite3.connect(dbpath.as_uri()+"?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite sem integridade.")
        dados = [dict(r) for r in conn.execute("SELECT * FROM observacoes ORDER BY competencia,raiz")]
        inscricoes = [r[0] for r in conn.execute("SELECT cnpj FROM inscricoes ORDER BY cnpj")]
    if len(inscricoes) != report["cnpjs_selecionados"]:
        raise ValueError("Selecao de inscricoes diverge do manifesto.")
    roots = {c[:8] for c in inscricoes}
    if len(roots) != report["raizes_selecionadas"]:
        raise ValueError("Selecao de raizes diverge do manifesto.")
    competencias = sorted(report["competencias"])
    if len(set(competencias)) != len(competencias) or not competencias:
        raise ValueError("Competencias duplicadas ou ausentes.")
    if {r["competencia"] for r in dados} != set(competencias):
        raise ValueError("Competencias do SQLite divergem do manifesto.")
    arquivos = report["artefatos"]
    preparados = []
    for mes in competencias:
        competencia(mes)
        if mes > mes_atual():
            raise ValueError("Nao e permitido publicar competencia futura.")
        fontes = sorted((a for a in arquivos if a["competencia"] == mes), key=lambda a:a["nome"])
        esperados = {f"Empresas{i}.zip" for i in range(10)} | {"Simples.zip"}
        if len(fontes) != 11 or {a["nome"] for a in fontes} != esperados:
            raise ValueError("Conjunto de arquivos incompleto.")
        for a in fontes:
            file = (raiz_workspace / a["caminho"]).resolve()
            if not file.is_relative_to(raiz_workspace):
                raise ValueError("Arquivo fora do workspace autorizado.")
            if a["url"] != f"https://arquivos.receitafederal.gov.br/public.php/webdav/{mes}/{a['nome']}":
                raise ValueError("Origem do artefato invalida.")
            if file.stat().st_size != a["bytes"] or _hash(file) != a["sha256"]:
                raise ValueError("Artefato ausente, alterado ou sem integridade.")
            if a.get("duplicadas", 0):
                raise ValueError("Duplicidades na selecao.")
        observacoes = []
        selecionadas = [r for r in dados if r["competencia"] == mes]
        if len(selecionadas) != len(roots) or {r["raiz"] for r in selecionadas} != roots:
            raise ValueError("Amostra de raizes incompleta ou duplicada.")
        for r in selecionadas:
            if not re.fullmatch(r"[A-Z0-9]{8}", r["raiz"]):
                raise ValueError("Raiz invalida.")
            codigo = r["porte_codigo"]
            if codigo is not None and codigo not in PORTES:
                raise ValueError("Porte nao documentado.")
            if r["porte"] != PORTES.get(codigo):
                raise ValueError("Rotulo do porte inconsistente.")
            simples = json.loads(r["simples_json"]) if r["simples_json"] else None
            mei = simples.get("opcao_mei") if simples is not None else None
            if mei not in (None, "", "S", "N"):
                raise ValueError("Indicador MEI invalido.")
            observacoes.append(dict(raiz=r["raiz"], porte_codigo=codigo,
                optante_mei={"S": True, "N": False}.get(mei), simples=simples))
        encontradas = sum(o["porte_codigo"] is not None for o in observacoes)
        c = report["cobertura"][mes]
        if (encontradas != c["raizes_encontradas"] or len(roots)-encontradas != c["raizes_ausentes"]
                or sum(o["porte_codigo"] == "00" for o in observacoes) != c["porte_nao_informado"]):
            raise ValueError("Cobertura diverge das observacoes.")
        tributarios = {
            "simples_encontradas": sum(o["simples"] is not None for o in observacoes),
            "simples_ausentes": sum(o["simples"] is None for o in observacoes),
            "mei_sim": sum(o["optante_mei"] is True for o in observacoes),
            "mei_nao": sum(o["optante_mei"] is False for o in observacoes),
            "mei_nao_informado": sum(o["simples"] is not None and o["optante_mei"] is None for o in observacoes),
        }
        if any(c.get(k) != v for k,v in tributarios.items()):
            raise ValueError("Cobertura tributaria diverge das observacoes.")
        identidade = {"competencia": mes, "fonte": FONTE,
            "arquivos": [{k:a[k] for k in ("nome","url","bytes","sha256")} for a in fontes],
            "observacoes": observacoes}
        versao = hashlib.sha256(json.dumps(identidade, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifesto = {"artefatos": fontes, "cobertura": c,
            "relatorio_sha256": hashlib.sha256(bruto).hexdigest(),
            "sqlite_sha256": sqlite_hash, "modo_fonte": report.get("modo_fonte"),
            "referencia_extracao_comprovada": False, "vigencia_diaria_comprovada": False,
            "procedencia": "RETRATO_MENSAL_PUBLICADO", "politica_temporal": POLITICA,
            "universo": "RAIZES_SELECIONADAS_NO_PIPELINE", "cnpjs_selecionados": len(inscricoes)}
        observado = max(datetime.fromisoformat(a["coletado_em"]) for a in fontes)
        preparados.append((mes, versao, observacoes, encontradas, manifesto, observado))
    saida = []
    with orm.main_session() as session:
        for mes, versao, observacoes, encontradas, manifesto, observado in preparados:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"), {"escopo":"porte-mensal:"+mes})
            existente = session.get(PortePublicacao, versao)
            if existente:
                saida.append({"competencia": mes, "versao": versao, "reutilizado": True, "ativa": existente.ativa})
                continue  # Reimportar uma versao antiga nao reativa nem reescreve seu conteudo.
            session.execute(update(PortePublicacao).where(
                PortePublicacao.competencia == mes, PortePublicacao.ativa.is_(True)).values(ativa=False))
            session.add(PortePublicacao(versao=versao, competencia=mes, ativa=True, fonte=FONTE,
                observado_em=observado, raizes_selecionadas=len(roots), raizes_encontradas=encontradas,
                manifesto=manifesto))
            session.flush()
            for i in range(0, len(observacoes), 1000):
                session.execute(insert(PorteObservacao), [dict(o, versao=versao) for o in observacoes[i:i+1000]])
            saida.append({"competencia": mes, "versao": versao, "reutilizado": False, "ativa": True})
    return saida


def raizes_classificadas_por_competencia(
    raizes_por_competencia: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Lista raízes com porte histórico conhecido nas publicações mensais ativas."""
    filtros = [
        and_(
            PortePublicacao.competencia == mes,
            PorteObservacao.raiz.in_(sorted(raizes)),
        )
        for mes, raizes in raizes_por_competencia.items()
        if raizes
    ]
    if not filtros:
        return {}
    with orm.main_session() as session:
        registros = session.execute(
            select(PortePublicacao.competencia, PorteObservacao.raiz)
            .join(PorteObservacao, PorteObservacao.versao == PortePublicacao.versao)
            .where(
                PortePublicacao.ativa.is_(True),
                PorteObservacao.porte_codigo.is_not(None),
                PorteObservacao.porte_codigo != "00",
                or_(*filtros),
            )
        ).all()
    resultado: dict[str, set[str]] = {}
    for mes, raiz in registros:
        resultado.setdefault(str(mes), set()).add(str(raiz))
    return resultado
