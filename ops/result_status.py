"""Stable business outcome vocabulary for task wrappers and pipelines."""
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional


class ResultStatus(str, Enum):
    SUCCESS_CORE = "SUCCESS_CORE"
    SUCCESS_ENRICHED = "SUCCESS_ENRICHED"
    DEGRADED = "DEGRADED"
    FAILED_CORE = "FAILED_CORE"


@dataclass(frozen=True)
class TaskResult:
    status: ResultStatus
    task: str
    run_id: str
    message: str = ""
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        return value
