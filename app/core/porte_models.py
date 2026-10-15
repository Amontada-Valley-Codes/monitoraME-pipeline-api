"""Observacoes cadastrais mensais, independentes do cache atual."""
from datetime import datetime
from typing import Any
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.orm import MainBase


class PortePublicacao(MainBase):
    __tablename__ = "porte_publicacoes"
    __table_args__ = (
        CheckConstraint("competencia ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_porte_competencia"),
        CheckConstraint("raizes_selecionadas >= 0 AND raizes_encontradas >= 0 AND raizes_encontradas <= raizes_selecionadas", name="ck_porte_cobertura"),
    )
    versao: Mapped[str] = mapped_column(Text, primary_key=True)
    competencia: Mapped[str] = mapped_column(Text)
    ativa: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    fonte: Mapped[str] = mapped_column(Text)
    observado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    importado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    raizes_selecionadas: Mapped[int] = mapped_column(Integer)
    raizes_encontradas: Mapped[int] = mapped_column(Integer)
    manifesto: Mapped[dict[str, Any]] = mapped_column(JSONB)


class PorteObservacao(MainBase):
    __tablename__ = "porte_observacoes"
    __table_args__ = (
        CheckConstraint("raiz ~ '^[A-Z0-9]{8}$'", name="ck_porte_raiz"),
        CheckConstraint("porte_codigo IS NULL OR porte_codigo IN ('00','01','03','05')", name="ck_porte_codigo"),
    )
    versao: Mapped[str] = mapped_column(Text, ForeignKey("porte_publicacoes.versao", ondelete="RESTRICT"), primary_key=True)
    raiz: Mapped[str] = mapped_column(Text, primary_key=True)
    porte_codigo: Mapped[str | None] = mapped_column(Text)
    optante_mei: Mapped[bool | None] = mapped_column(Boolean)
    simples: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


Index("uq_porte_competencia_ativa", PortePublicacao.competencia, unique=True, postgresql_where=text("ativa"))
Index("idx_porte_observacoes_raiz", PorteObservacao.raiz)
