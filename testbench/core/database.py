"""SQLite persistence for completed and in-progress test runs."""

from __future__ import annotations

import builtins
from pathlib import Path

from sqlalchemy import (
    JSON,
    URL,
    Boolean,
    Float,
    ForeignKey,
    String,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from testbench.core.runner import RunResult


class Base(DeclarativeBase):
    """Base class for TestBench database models."""


class RunRecord(Base):
    """Persisted summary for a single test execution."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    test_name: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    artifact_dir: Mapped[str] = mapped_column(String, nullable=False)
    report_path: Mapped[str | None] = mapped_column(String)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    steps: Mapped[list[StepRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StepRecord.index",
    )


class StepRecord(Base):
    """Persisted timing and outcome for one step."""

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False)
    index: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(String)
    screenshot: Mapped[str | None] = mapped_column(String)
    run: Mapped[RunRecord] = relationship(back_populates="steps")


class HealingRecord(Base):
    """Audit record for a suggested or applied locator repair."""

    __tablename__ = "healing_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(nullable=False)
    old_locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    new_locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    accepted: Mapped[bool | None] = mapped_column(Boolean)


class RunStore:
    """Small SQLite store with an explicit, configurable database location."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = URL.create("sqlite", database=str(database_path.resolve()))
        self._engine = create_engine(url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)
        run_columns = {column["name"] for column in inspect(self._engine).get_columns("runs")}
        if "metadata_json" not in run_columns:
            with self._engine.begin() as connection:
                connection.execute(text("ALTER TABLE runs ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
        self._sessions = sessionmaker(bind=self._engine)

    def save(self, result: RunResult) -> None:
        """Insert or update a run and its current step snapshots."""
        with self._sessions.begin() as session:
            row = session.get(RunRecord, result.run_id)
            if row is None:
                row = RunRecord(
                    run_id=result.run_id,
                    test_name=result.test_name,
                    target=result.target,
                    status=result.status,
                    started_at=result.started_at,
                    duration_seconds=result.duration_seconds,
                    artifact_dir=result.artifact_dir,
                    report_path=result.report_path,
                    metadata_json=result.metadata,
                )
                session.add(row)
            else:
                row.test_name = result.test_name
                row.target = result.target
                row.status = result.status
                row.started_at = result.started_at
                row.duration_seconds = result.duration_seconds
                row.artifact_dir = result.artifact_dir
                row.report_path = result.report_path
                row.metadata_json = result.metadata
                row.steps.clear()

            row.steps.extend(
                StepRecord(
                    index=step.index,
                    action=step.action,
                    status=step.status,
                    duration_seconds=step.duration_seconds,
                    error=step.error,
                    screenshot=step.screenshot,
                )
                for step in result.steps
            )

    def get(self, run_id: str) -> dict | None:
        """Return one run as a JSON-compatible dictionary."""
        with self._sessions() as session:
            row = session.get(RunRecord, run_id)
            if row is None:
                return None
            return _serialize_run(row)

    def list(self, limit: int = 50) -> list[dict]:
        """Return the most recent run summaries."""
        with self._sessions() as session:
            rows = session.scalars(select(RunRecord).order_by(RunRecord.started_at.desc()).limit(limit)).all()
            return [_serialize_run(row) for row in rows]

    def record_healing(
        self,
        run_id: str,
        step_index: int,
        suggestion: dict,
        *,
        accepted: bool | None = None,
    ) -> int:
        """Store a locator suggestion and return its history row ID."""
        with self._sessions.begin() as session:
            row = HealingRecord(
                run_id=run_id,
                step_index=step_index,
                old_locator=suggestion["old_locator"],
                new_locator=suggestion["new_locator"],
                confidence=suggestion["confidence"],
                method=suggestion["method"],
                accepted=accepted,
            )
            session.add(row)
            session.flush()
            return row.id

    def list_healing(self, run_id: str | None = None, limit: int = 100) -> builtins.list[dict]:
        """List recent healing history, optionally for one run."""
        with self._sessions() as session:
            query = select(HealingRecord).order_by(HealingRecord.id.desc()).limit(limit)
            if run_id is not None:
                query = query.where(HealingRecord.run_id == run_id)
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "step_index": row.step_index,
                    "old_locator": row.old_locator,
                    "new_locator": row.new_locator,
                    "confidence": row.confidence,
                    "method": row.method,
                    "accepted": row.accepted,
                }
                for row in session.scalars(query).all()
            ]

    def update_healing(self, record_id: int, *, accepted: bool) -> bool:
        """Mark one healing suggestion accepted or rejected."""
        with self._sessions.begin() as session:
            row = session.get(HealingRecord, record_id)
            if row is None:
                return False
            row.accepted = accepted
            return True

    def update_metadata(self, run_id: str, values: dict) -> bool:
        """Merge metadata into one persisted run."""
        with self._sessions.begin() as session:
            row = session.get(RunRecord, run_id)
            if row is None:
                return False
            row.metadata_json = {**(row.metadata_json or {}), **values}
            return True


def _serialize_run(row: RunRecord) -> dict:
    return {
        "run_id": row.run_id,
        "test_name": row.test_name,
        "target": row.target,
        "status": row.status,
        "started_at": row.started_at,
        "duration_seconds": row.duration_seconds,
        "artifact_dir": row.artifact_dir,
        "report_path": row.report_path,
        "metadata": row.metadata_json or {},
        "steps": [
            {
                "index": step.index,
                "action": step.action,
                "status": step.status,
                "duration_seconds": step.duration_seconds,
                "error": step.error,
                "screenshot": step.screenshot,
            }
            for step in row.steps
        ],
    }
