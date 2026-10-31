from __future__ import annotations

from enum import StrEnum


class EmpenhoSortField(StrEnum):
    DATE = "date"
    MUNICIPALITY = "municipality"
    SUPPLIER = "supplier"
    EXPENSE_ELEMENT = "expense_element"
    FINAL_VALUE = "final_value"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


__all__ = ["EmpenhoSortField", "SortOrder"]
