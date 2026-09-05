#!/usr/bin/env python3
"""
Real-LLM smoke: regenerate OntoSynthesis creation_checks and verify fail-closed.

Confirms evidence-required check_existing_* empty-evidence calls return:
  status == "rejected"
  code  == "PROPOSED_ENTITY_EVIDENCE_REQUIRED"

Also verifies package-local error_json ignores a malicious status= overwrite
(the v10 regression). Uses live LLM via agentic generation (not mocks).

Example (repo root):

  python -m src.agents.scripts_and_prompts_generation.check_existing_failclosed_smoke \\
    --trials 3 --model gpt-5
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    error_json as canonical_error_json,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES,
    existing_entity_check_contracts,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKS_NAME = "ontosynthesis_creation_checks.py"
EXPECTED_CODE = "PROPOSED_ENTITY_EVIDENCE_REQUIRED"
STATUS_OVERWRITE_RE = re.compile(
    r"error_json\s*\([^)]*status\s*=\s*(?:STATUS_PROPOSED_ENTITY_EVIDENCE_REQUIRED|"
    r"[\"']PROPOSED_ENTITY_EVIDENCE_REQUIRED[\"'])",
    re.DOTALL,
)


def _evidence_required_tool_names(trial_root: Path) -> list[str]:
    contract_path = (
        trial_root
        / "ontology_structures"
        / "ontosynthesis"
        / "generation_contract.json"
    )
    parsed_path = (
        trial_root
        / "ontology_structures"
        / "ontosynthesis"
        / "parsed.json"
    )
    if not contract_path.is_file() or not parsed_path.is_file():
        return []
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    return [
        str(item["public_tool"])
        for item in existing_entity_check_contracts(
            parsed=parsed,
            contract=contract,
            legacy_all_classes_when_absent=False,
        )
        if str(item.get("lookup_scope") or "")
        in EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES
    ]


def _probe_fail_closed(scripts_dir: Path, trial_root: Path) -> dict[str, Any]:
    package_dir = scripts_dir.parent
    package_root = str(package_dir.resolve())
    if package_root not in sys.path:
        sys.path.insert(0, package_root)

    for mod_name in list(sys.modules):
        if mod_name == "ontosynthesis" or mod_name.startswith("ontosynthesis."):
            del sys.modules[mod_name]

    checks = importlib.import_module("ontosynthesis.ontosynthesis_creation_checks")
    runtime = importlib.import_module("ontosynthesis._fixed_rdf_runtime")

    runtime_probe = json.loads(
        runtime.error_json(
            code=EXPECTED_CODE,
            message="smoke status overwrite",
            status=EXPECTED_CODE,
        )
    )
    if runtime_probe.get("status") != "rejected" or runtime_probe.get("code") != EXPECTED_CODE:
        return {
            "ok": False,
            "error": "package_runtime_error_json_status_overwrite",
            "runtime_probe": runtime_probe,
        }

    evidence_required_tools = _evidence_required_tool_names(trial_root)
    all_tools = [
        name
        for name in getattr(checks, "__all__", [])
        if isinstance(name, str) and name.startswith("check_existing_")
    ]
    if not all_tools:
        return {"ok": False, "error": "no_check_existing_tools", "tools": []}

    tool_results: list[dict[str, Any]] = []
    failures: list[str] = []
    evidence_required_pass = 0

    for tool_name in all_tools:
        tool = getattr(checks, tool_name, None)
        if tool is None:
            failures.append(f"{tool_name}: missing")
            continue
        try:
            payload = json.loads(tool())
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{tool_name}: {type(exc).__name__}: {exc}")
            continue
        status = str(payload.get("status") or "")
        code = str(payload.get("code") or "")
        requires_evidence = tool_name in evidence_required_tools

        # Exact v10 regression: rejection code leaked into status.
        if status == EXPECTED_CODE or (
            code == EXPECTED_CODE and status.casefold() != "rejected"
        ):
            ok = False
            failures.append(
                f"{tool_name}: status-overwrite regression "
                f"(status={status!r}, code={code!r})"
            )
        elif requires_evidence or not evidence_required_tools:
            # Contract present: every evidence-required lookup_scope must fail
            # closed. Contract missing: any fail-closed code must still use
            # status=rejected; other tools may return ok.
            if code == EXPECTED_CODE or requires_evidence:
                ok = status.casefold() == "rejected" and code == EXPECTED_CODE
                if ok:
                    evidence_required_pass += 1
                elif requires_evidence:
                    failures.append(
                        f"{tool_name}: evidence-required fail-closed expected "
                        f"status=rejected code={EXPECTED_CODE}; "
                        f"got status={status!r} code={code!r}"
                    )
                else:
                    ok = True
            else:
                ok = True
        else:
            ok = True

        tool_results.append(
            {
                "tool": tool_name,
                "evidence_required": requires_evidence,
                "ok": ok,
                "status": status,
                "code": code,
            }
        )

    if evidence_required_tools:
        missing_required = [
            name for name in evidence_required_tools if name not in all_tools
        ]
        for name in missing_required:
            failures.append(f"{name}: missing evidence-required tool")
        if evidence_required_pass == 0:
            failures.append("no_evidence_required_tool_passed_fail_closed_probe")
    elif not any(
        r.get("status", "").casefold() == "rejected" and r.get("code") == EXPECTED_CODE
        for r in tool_results
    ):
        failures.append("no_tool_returned_fail_closed_envelope")

    source = (scripts_dir / CHECKS_NAME).read_text(encoding="utf-8")
    antipattern = bool(STATUS_OVERWRITE_RE.search(source))
    return {
        "ok": not failures,
        "failures": failures,
        "tools_probed": len(tool_results),
        "evidence_required_tools": evidence_required_tools,
        "evidence_required_pass": evidence_required_pass,
        "tool_results": tool_results,
        "source_status_overwrite_antipattern": antipattern,
        "runtime_probe": runtime_probe,
        "checks_chars": len(source),
    }


def run_trial(
    *,
    trial: int,
    output_root: Path,
    model: str,
    domain_config: Path,
) -> dict[str, Any]:
    trial_root = output_root / f"trial_{trial}"
    if trial_root.exists():
        shutil.rmtree(trial_root)
    # Important: do not pre-seed files before the experiment. With --target-artifact,
    # a non-empty output snapshot causes newly written ontology_structures to be deleted.

    summary = run_agentic_generation_experiment(
        ["ontosynthesis"],
        domain_config_path=domain_config,
        output_root=trial_root,
        generate_scripts=True,
        generate_prompts=False,
        llm_agent_generation=True,
        generation_model=model,
        generation_only=True,
        incremental_generation_repair=True,
        focused_repair=True,
        parallel_generation=False,
        max_generation_workers=1,
        max_agent_rounds=2,
        edit_backend="exact_edits",
        target_artifacts=[CHECKS_NAME],
        write_context_files=True,
    )

    scripts_dir = trial_root / "scripts" / "ontosynthesis"
    runtime_src = Path(__file__).with_name("fixed_rdf_runtime.py")
    if scripts_dir.is_dir():
        shutil.copy2(runtime_src, scripts_dir / "_fixed_rdf_runtime.py")

    probe = _probe_fail_closed(scripts_dir, trial_root)
    report = {
        "trial": trial,
        "ok": bool(probe.get("ok")),
        "output_root": str(trial_root),
        "generation_ok": bool(summary.get("ok")),
        "probe": probe,
    }
    (trial_root / "failclosed_smoke_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real-LLM smoke for check_existing fail-closed envelope."
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument(
        "--output-root",
        default="tmp/check_existing_failclosed_smoke",
    )
    parser.add_argument(
        "--domain-config",
        default="configs/domains/ontosynthesis.json",
    )
    args = parser.parse_args(argv)
    if args.trials < 1:
        raise SystemExit("--trials must be >= 1")

    offline = json.loads(
        canonical_error_json(
            code=EXPECTED_CODE,
            message="offline",
            status=EXPECTED_CODE,
        )
    )
    if offline.get("status") != "rejected" or offline.get("code") != EXPECTED_CODE:
        raise SystemExit(f"canonical error_json broken before smoke: {offline}")

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    domain_config = Path(args.domain_config)

    results: list[dict[str, Any]] = []
    for trial in range(1, args.trials + 1):
        print(f"[smoke] trial {trial}/{args.trials} starting...", flush=True)
        result = run_trial(
            trial=trial,
            output_root=output_root,
            model=args.model,
            domain_config=domain_config,
        )
        results.append(result)
        status = "PASS" if result["ok"] else "FAIL"
        probe = result.get("probe") or {}
        print(
            f"[smoke] trial {trial} {status} "
            f"(generation_ok={result.get('generation_ok')}, "
            f"evidence_required_pass={probe.get('evidence_required_pass')}/"
            f"{len(probe.get('evidence_required_tools') or [])}, "
            f"source_antipattern={probe.get('source_status_overwrite_antipattern')})",
            flush=True,
        )
        if not result["ok"]:
            print(json.dumps(probe.get("failures"), indent=2, ensure_ascii=False))

    summary = {
        "schema_version": "check-existing-failclosed-smoke.v1",
        "trials": args.trials,
        "model": args.model,
        "all_ok": all(bool(item.get("ok")) for item in results),
        "passed": sum(1 for item in results if item.get("ok")),
        "results": [
            {
                "trial": item.get("trial"),
                "ok": item.get("ok"),
                "generation_ok": item.get("generation_ok"),
                "evidence_required_pass": (item.get("probe") or {}).get(
                    "evidence_required_pass"
                ),
                "evidence_required_tools": len(
                    (item.get("probe") or {}).get("evidence_required_tools") or []
                ),
                "source_antipattern": (item.get("probe") or {}).get(
                    "source_status_overwrite_antipattern"
                ),
                "failures": (item.get("probe") or {}).get("failures") or [],
            }
            for item in results
        ],
    }
    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[smoke] wrote {summary_path}", flush=True)
    print(
        f"[smoke] {summary['passed']}/{summary['trials']} passed; "
        f"all_ok={summary['all_ok']}",
        flush=True,
    )
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
