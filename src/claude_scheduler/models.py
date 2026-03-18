from dataclasses import dataclass
from datetime import datetime


@dataclass
class Job:
    id: int
    name: str
    prompt: str
    directory: str
    schedule: str
    model: str | None
    skip_perms: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        return cls(
            id=row["id"],
            name=row["name"],
            prompt=row["prompt"],
            directory=row["directory"],
            schedule=row["schedule"],
            model=row["model"],
            skip_perms=bool(row["skip_perms"]),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


@dataclass
class Run:
    id: int
    job_id: int
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    cost_usd: float | None
    duration_ms: int | None
    output_file: str
    session_id: str | None
    error: str | None

    @classmethod
    def from_row(cls, row: dict) -> "Run":
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            exit_code=row["exit_code"],
            cost_usd=row["cost_usd"],
            duration_ms=row["duration_ms"],
            output_file=row["output_file"],
            session_id=row["session_id"],
            error=row["error"],
        )
