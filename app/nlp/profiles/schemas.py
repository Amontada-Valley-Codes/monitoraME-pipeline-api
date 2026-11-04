"""Contratos versionados do Matching NLP, compatíveis com Company/CompanyCnae."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "matching.v1"


class CompanySize(StrEnum):
    MEI = "MEI"
    ME = "ME"
    EPP = "EPP"
    DEMAIS = "DEMAIS"


class CnaeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=7, max_length=7, pattern=r"^\d{7}$")
    description: str | None = Field(default=None, max_length=500)
    is_primary: bool


class CompanyLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    municipality_code: str = Field(min_length=7, max_length=7, pattern=r"^\d{7}$")
    state: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")


class CompanyProfile(BaseModel):
    """Snapshot mínimo; não aceita dados pessoais/identificadores cadastrais."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_id: UUID
    profile_version: str = Field(min_length=1, max_length=100)
    company_size: CompanySize
    primary_cnae: str = Field(min_length=7, max_length=7, pattern=r"^\d{7}$")
    cnaes: list[CnaeProfile] = Field(min_length=1, max_length=100)
    location: CompanyLocation
    categories: list[str] = Field(default_factory=list, max_length=100)
    operational_preferences: dict[str, str | bool | int] = Field(default_factory=dict, max_length=50)
    participation_forms: list[str] | None = Field(default=None, max_length=30)
    # Interesses explícitos; location representa sede e não implica preferência.
    interested_states: list[str] | None = Field(default=None, max_length=27)
    interested_municipalities: list[str] | None = Field(default=None, max_length=5_600)
    preferred_modality_codes: list[str] | None = Field(default=None, max_length=30)

    @field_validator("interested_states")
    @classmethod
    def validar_ufs_interesse(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(len(value) != 2 or not value.isalpha() or value != value.upper() for value in values):
            raise ValueError("interested_states deve conter UFs de duas letras maiusculas")
        return values

    @field_validator("interested_municipalities")
    @classmethod
    def validar_municipios_interesse(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(len(value) != 7 or not value.isdigit() for value in values):
            raise ValueError("interested_municipalities deve conter codigos IBGE de sete digitos")
        return values

    @model_validator(mode="after")
    def validar_cnaes_primario(self) -> CompanyProfile:
        primarios = [cnae.code for cnae in self.cnaes if cnae.is_primary]
        if primarios != [self.primary_cnae]:
            raise ValueError("cnaes deve conter exatamente um is_primary=true igual a primary_cnae")
        if len({cnae.code for cnae in self.cnaes}) != len(self.cnaes):
            raise ValueError("cnaes não pode conter códigos duplicados")
        return self


class MatchingBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    schema_version: Literal["matching.v1"]
    companies: list[CompanyProfile] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validar_empresas_unicas(self) -> MatchingBatchRequest:
        ids = [company.company_id for company in self.companies]
        if len(set(ids)) != len(ids):
            raise ValueError("companies não pode conter company_id duplicado")
        return self
