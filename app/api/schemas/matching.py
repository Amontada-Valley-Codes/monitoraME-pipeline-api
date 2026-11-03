"""Contratos de consulta dos resultados de compatibilidade NLP."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.nlp.matching.schemas import CompatibilityLevel, MatchingResult


class MatchingResultEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    company_id: UUID
    compatibility_level: CompatibilityLevel
    score: float = Field(ge=0, le=100)
    processed_at: datetime
    result: MatchingResult


class MatchingResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    results: list[MatchingResultEntry]
