"""Extract recording paths and workflow ids from ReAct tool outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

RECORDING_PATH_LINE = re.compile(r"^recording_path\t(.+)$", re.MULTILINE)
WORKFLOW_ID_LINE = re.compile(r"^workflow_id\t(.+)$", re.MULTILINE)
ONLINE_JSON = re.compile(r"([^\s\"']+_online_\d+\.json)", re.IGNORECASE)


def parse_tsv_field(text: str, field: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith(f"{field}\t"):
            return line.split("\t", 1)[1].strip()
    return None


def extract_from_text(text: str) -> Dict[str, Optional[str]]:
    recording = parse_tsv_field(text, "recording_path")
    workflow_id = parse_tsv_field(text, "workflow_id")
    if not recording:
        match = ONLINE_JSON.search(text or "")
        if match:
            recording = match.group(1)
    if recording:
        recording = str(Path(recording.strip().strip('"')).resolve())
    return {"recording_path": recording, "workflow_id": workflow_id}


def extract_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Optional[str]]:
    tool_activity = metadata.get("tool_activity") or {}
    recording: Optional[str] = None
    workflow_id: Optional[str] = None

    for item in tool_activity.get("tool_outputs") or []:
        content = item.get("content") or ""
        parsed = extract_from_text(content)
        if parsed.get("recording_path"):
            recording = parsed["recording_path"]
        if parsed.get("workflow_id"):
            workflow_id = parsed["workflow_id"]

    if not recording:
        for name in reversed(tool_activity.get("executed_tool_names") or []):
            if name in (
                "run_workflow_online",
                "run_competency_online",
                "replay_workflow_offline",
                "replay_competency_offline",
            ):
                break

    return {"recording_path": recording, "workflow_id": workflow_id}


def extract_recording_info(
    *,
    online_answer: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Best-effort extraction from tool outputs then agent answer text."""
    info = extract_from_metadata(metadata or {})
    if not info.get("recording_path"):
        from_answer = extract_from_text(online_answer or "")
        if from_answer.get("recording_path"):
            info["recording_path"] = from_answer["recording_path"]
        if from_answer.get("workflow_id") and not info.get("workflow_id"):
            info["workflow_id"] = from_answer["workflow_id"]
    return info


def load_recording_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
