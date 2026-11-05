"""Limpadores de dados separados por fonte externa."""

from app.pipeline.cleaners import ibge, opencnpj, tce_despesas

__all__ = ["ibge", "opencnpj", "tce_despesas"]
