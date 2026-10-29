from __future__ import annotations

from enum import StrEnum


class SupplierDocumentType(StrEnum):
    CNPJ = "CNPJ"
    CPF = "CPF"


class SupplierSortField(StrEnum):
    SUPPLIER = "supplier"
    FINAL_VALUE = "final_value"
    EXPENSES_COUNT = "expenses_count"
    FIRST_EXPENSE = "first_expense"
    LAST_EXPENSE = "last_expense"


__all__ = ["SupplierDocumentType", "SupplierSortField"]
