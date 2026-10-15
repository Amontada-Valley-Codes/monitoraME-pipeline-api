"""Publicacao atomica e consulta de retratos mensais com lacunas explicitas."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from sqlalchemy import func, insert, select, text, update
from app.core import orm
from app.core.porte_models import PortePublicacao, PorteObservacao
from app.utils import normalizar_cnpj

PORTES = {"00": "NAO_INFORMADO", "01": "ME", "03": "EPP", "05": "DEMAIS"}
FONTE = "RECEITA_FEDERAL_DADOS_ABERTOS"
POLITICA = "COMPETENCIA_EXATA_SEM_PREENCHIMENTO"


def competencia(valor: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", valor) or valor[:4] == "0000":
        raise ValueError("Competencia invalida; use YYYY-MM.")
    return valor


def mes_atual() -> str:
    return datetime.now(ZoneInfo("America/Fortaleza")).strftime("%Y-%m")


def meses(inicio: str, fim: str) -> list[str]:
    competencia(inicio)
    competencia(fim)
    a = int(inicio[:4])*12 + int(inicio[5:])-1
    b = int(fim[:4])*12 + int(fim[5:])-1
    if b < a or b-a >= 120:
        raise ValueError("Intervalo deve ser crescente e conter no maximo 120 meses.")
    return [f"{n//12:04d}-{n%12+1:02d}" for n in range(a, b+1)]


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


def _publicacao(p: PortePublicacao) -> dict:
    return {"competencia": p.competencia, "versao": p.versao, "ativa": p.ativa,
        "fonte": p.fonte, "procedencia": "RETRATO_MENSAL_PUBLICADO",
        "observado_em": p.observado_em.isoformat(), "importado_em": p.importado_em.isoformat(),
        "referencia_extracao_comprovada": False, "vigencia_diaria_comprovada": False,
        "cobertura": {"raizes_selecionadas": p.raizes_selecionadas, **p.manifesto["cobertura"]}, "universo": p.manifesto["universo"],
        "artefatos": [{k:a.get(k) for k in ("nome","url","sha256","bytes","last_modified","membros_zip")}
                     for a in p.manifesto["artefatos"]]}


def publicacoes(fim: str | None = None, *, todas_versoes: bool = False) -> dict:
    fim = competencia(fim or mes_atual())
    with orm.main_session() as session:
        stmt = select(PortePublicacao).where(PortePublicacao.competencia <= fim)
        if not todas_versoes:
            stmt = stmt.where(PortePublicacao.ativa.is_(True))
        items = [_publicacao(p) for p in session.scalars(stmt.order_by(PortePublicacao.competencia, PortePublicacao.importado_em))]
    return {"data_source": "LOCAL_DATABASE", "external_requests": False,
        "politica_temporal": POLITICA, "competencia_final": fim, "items": items}


def _observacao(session, p, raiz):
    o = session.get(PorteObservacao, (p.versao, raiz))
    if o is None:
        status = "FORA_DA_AMOSTRA"
    elif o.porte_codigo is None:
        status = "AUSENTE_NA_FOTOGRAFIA"
    elif o.porte_codigo == "00":
        status = "PORTE_NAO_INFORMADO"
    else:
        status = "IDENTIFICADO"
    return {"competencia": p.competencia, "versao": p.versao, "status": status,
        "porte_codigo": o.porte_codigo if o else None, "porte_observado": PORTES.get(o.porte_codigo) if o else None,
        "optante_mei": o.optante_mei if o else None,
        "simples_disponivel": o is not None and o.simples is not None,
        "publicacao": _publicacao(p)}


def historico(cnpj: str, inicio: str | None = None, fim: str | None = None, *, versao: str | None = None) -> dict:
    cnpj = normalizar_cnpj(cnpj)
    if cnpj is None:
        raise ValueError("CNPJ com formato invalido.")
    raiz = cnpj[:8]
    with orm.main_session() as session:
        if versao:
            p = session.get(PortePublicacao, versao)
            if p is None:
                raise LookupError("Versao nao encontrada.")
            items = [_observacao(session, p, raiz)]
            lacunas = []
        else:
            fim = competencia(fim or mes_atual())
            primeiro = session.scalar(select(func.min(PortePublicacao.competencia)).where(
                PortePublicacao.ativa.is_(True), PortePublicacao.competencia <= fim))
            inicio = competencia(inicio or primeiro or fim)
            intervalo = meses(inicio, fim)
            registros = {p.competencia:p for p in session.scalars(select(PortePublicacao).where(
                PortePublicacao.ativa.is_(True), PortePublicacao.competencia.in_(intervalo)))}
            items, lacunas = [], []
            for mes in intervalo:
                if mes not in registros:
                    lacunas.append(mes)
                    items.append({"competencia":mes, "versao":None, "status":"SEM_PUBLICACAO",
                                  "porte_observado":None, "porte_codigo":None, "optante_mei":None,
                                  "simples_disponivel":False, "publicacao":None})
                else:
                    items.append(_observacao(session, registros[mes], raiz))
    return {"cnpj_consultado": cnpj, "raiz": raiz, "unidade_cadastral": "CNPJ_BASICO",
        "data_source": "LOCAL_DATABASE", "external_requests": False, "politica_temporal": POLITICA,
        "vigencia_diaria_comprovada": False, "competencias_sem_publicacao": lacunas, "items": items}
