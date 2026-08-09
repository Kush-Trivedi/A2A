import json
from typing import Any
from collections.abc import Sequence
from sqlalchemy.types import UserDefinedType

DEFAULT_EMBEDDING_DIMENSIONS = 3072

class PgVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIMENSIONS) -> None:
        self.dimension = dimension

    def get_col_spec(self, **_kw) -> str:
        return f"vector({self.dimension})"

    def bind_processor(self, _dialect) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, Sequence):
                return "[" + ",".join(str(float(x)) for x in value) + "]"
            raise TypeError(f"Cannot convert {type(value)} to vector")
        return process

    def result_processor(self, _dialect, _coltype) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, list):
                return [float(x) for x in value]
            if isinstance(value, str):
                return [float(x) for x in json.loads(value)]
        return process
