"""Contratos de saída do matching e dos endpoints futuros de batches."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.nlp.profiles.schemas import SCHEMA_VERSION


class CompatibilityLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MatchingReason(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    evidence: str | None = Field(default=None, max_length=1000)


class MatchingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    criterion: str = Field(min_length=1, max_length=40)
    score: float | None = Field(default=None, ge=0, le=1)
    details: str = Field(min_length=1, max_length=1000)


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NOT_CONFIRMED = "NOT_CONFIRMED"


class ProcurementProfile(BaseModel):
    """Atributos normalizados opcionais necessários ao matching determinístico."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    procurement_id: str = Field(min_length=1, max_length=300)
    source: str = Field(default="PNCP", min_length=1, max_length=30)
    external_id: str = Field(min_length=1, max_length=300)
    object: str | None = Field(default=None, max_length=10_000)
    items: list[str] = Field(default_factory=list, max_length=500)
    categories: list[str] = Field(default_factory=list, max_length=100)
    accepted_company_sizes: list[str] | None = Field(default=None, max_length=4)
    territory_states: list[str] | None = Field(default=None, max_length=27)
    territory_municipalities: list[str] | None = Field(default=None, max_length=5_600)
    participation_forms: list[str] | None = Field(default=None, max_length=30)
    participation_form: str | None = Field(default=None, max_length=80)
    operational_requirements: dict[str, str | bool | int] = Field(default_factory=dict, max_length=50)
    status: str | None = Field(default=None, max_length=40)
    deadline: datetime | None = None
    revoked: bool | None = None
    annulled: bool | None = None
    suspended: bool | None = None
    url: str | None = Field(default=None, max_length=2000)
    version: str = Field(min_length=1, max_length=100)


class ProcurementReference(BaseModel):
    """Referência a edital já normalizado pelo Pipeline; sem coleta PNCP aqui."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    procurement_id: str = Field(min_length=1, max_length=300)
    source: str = Field(default="PNCP", min_length=1, max_length=30)
    external_id: str = Field(min_length=1, max_length=300)
    object: str | None = Field(default=None, max_length=10_000)
    url: str | None = Field(default=None, max_length=2000)
    version: str = Field(min_length=1, max_length=100)


class MatchingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    edital: ProcurementReference
    score: float = Field(ge=0, le=100)
    data_confidence: float = Field(ge=0, le=100)
    eligibility: EligibilityStatus
    compatibility_level: CompatibilityLevel
    reason_codes: list[str] = Field(default_factory=list, max_length=50)
    warning_codes: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[MatchingEvidence] = Field(default_factory=list, max_length=20)
    justifications: list[MatchingReason] = Field(default_factory=list, max_length=50)
    profile_version: str = Field(min_length=1, max_length=100)
    schema_version: Literal["matching.v1"] = SCHEMA_VERSION
    matching_version: str = Field(min_length=1, max_length=100)
    taxonomy_version: str = Field(min_length=1, max_length=100)
    processing_state: BatchStatus
    processed_at: datetime | None = None


class BatchAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    status: BatchStatus = BatchStatus.PENDING
    accepted_companies: int = Field(ge=0)


class BatchStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    schema_version: Literal["matching.v1"] = SCHEMA_VERSION
    status: BatchStatus
    total_companies: int = Field(ge=0)
    processed_companies: int = Field(ge=0)
    failed_companies: int = Field(ge=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = Field(default=None, max_length=500)


class MatchingResultsPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    schema_version: Literal["matching.v1"] = SCHEMA_VERSION
    status: BatchStatus
    results: list[MatchingResult]
    next_cursor: str | None = None
