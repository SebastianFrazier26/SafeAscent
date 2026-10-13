"""Column types SQLAlchemy does not ship."""

from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType[str]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"
