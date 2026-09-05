"""Durable per-artifact generation state.

The state file is intentionally independent from the validation report. Reports
describe the latest validation result; this journal records where generation
stopped so an interrupted process can resume without guessing from file size.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
IN_PROGRESS_STATES = frozenset({"generating", "validating", "repairing"})
TERMINAL_STATES = frozenset({"passed", "failed", "interrupted"})
VALID_STATES = frozenset(
    {"pending", *IN_PROGRESS_STATES, *TERMINAL_STATES}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArtifactStateStore:
    """Atomically persist lifecycle state for generated artifacts."""

    def __init__(self, output_root: str | Path, ontology: str) -> None:
        self.output_root = Path(output_root).resolve()
        self.ontology = ontology
        self.path = (
            self.output_root
            / "reports"
            / ontology
            / "artifact_states.json"
        )
        self._payload = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == SCHEMA_VERSION
                and isinstance(payload.get("artifacts"), dict)
            ):
                return payload
        return {
            "schema_version": SCHEMA_VERSION,
            "ontology": self.ontology,
            "sequence": 0,
            "updated_at": _utc_now(),
            "artifacts": {},
        }

    def _key(self, artifact: str | Path) -> str:
        path = Path(artifact).resolve()
        try:
            return path.relative_to(self.output_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _write(self) -> None:
        self._payload["sequence"] = int(self._payload.get("sequence") or 0) + 1
        self._payload["updated_at"] = _utc_now()
        _atomic_json(self.path, self._payload)

    def initialize(self, artifacts: list[str | Path]) -> None:
        changed = False
        records = self._payload["artifacts"]
        for artifact in artifacts:
            key = self._key(artifact)
            if key not in records:
                records[key] = {
                    "status": "pending",
                    "attempt": 0,
                    "updated_at": _utc_now(),
                    "content_sha256": _sha256(Path(artifact)),
                }
                changed = True
        if changed:
            self._write()

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        for key, record in self._payload["artifacts"].items():
            if record.get("status") in IN_PROGRESS_STATES:
                record["status"] = "interrupted"
                record["updated_at"] = _utc_now()
                record["reason"] = "previous_process_ended_during_artifact"
                history = list(record.get("history") or [])
                history.append(
                    {
                        "status": "interrupted",
                        "updated_at": record["updated_at"],
                        "reason": record["reason"],
                    }
                )
                record["history"] = history[-100:]
                recovered.append(key)
        if recovered:
            self._write()
        return recovered

    def record_for(self, artifact: str | Path) -> dict[str, Any]:
        """Return a detached lifecycle record for one artifact."""
        return dict(self._payload["artifacts"].get(self._key(artifact)) or {})

    def is_matching_passed(self, artifact: str | Path) -> bool:
        """Only reuse a passed artifact when its current bytes match the journal."""
        path = Path(artifact)
        record = self.record_for(path)
        return (
            record.get("status") == "passed"
            and record.get("content_sha256") is not None
            and record.get("content_sha256") == _sha256(path)
        )

    def should_preserve_existing(self, artifact: str | Path) -> bool:
        """Preserve trustworthy passed bytes and non-empty interrupted work."""
        path = Path(artifact)
        if not path.is_file() or not path.read_bytes():
            return False
        record = self.record_for(path)
        if record.get("status") == "passed":
            return self.is_matching_passed(path)
        return record.get("status") in {
            "failed",
            "interrupted",
            *IN_PROGRESS_STATES,
        }

    def transition(
        self,
        artifact: str | Path,
        status: str,
        *,
        reason: str | None = None,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_STATES:
            raise ValueError(f"Unsupported artifact state: {status}")
        path = Path(artifact)
        key = self._key(path)
        record = dict(self._payload["artifacts"].get(key) or {})
        if status == "generating":
            record["attempt"] = int(record.get("attempt") or 0) + 1
        record.update(
            {
                "status": status,
                "updated_at": _utc_now(),
                "content_sha256": _sha256(path),
            }
        )
        if reason:
            record["reason"] = reason
        else:
            record.pop("reason", None)
        if validation is not None:
            record["validation"] = {
                "ok": bool(validation.get("ok")),
                "stage_ok": bool(validation.get("stage_ok")),
                "failure_count": len(validation.get("failures") or []),
            }
        history = list(record.get("history") or [])
        history.append(
            {
                "status": status,
                "updated_at": record["updated_at"],
                **({"reason": reason} if reason else {}),
            }
        )
        record["history"] = history[-100:]
        self._payload["artifacts"][key] = record
        self._write()
        return dict(record)

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._payload))
