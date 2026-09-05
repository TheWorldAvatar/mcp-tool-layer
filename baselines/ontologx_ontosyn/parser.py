"""OntoLogX main parser adapted for OntoSynthesis papers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolCall, ToolMessage
from pydantic import ValidationError

from graph_merge import attach_subgraph, complete_delta, remove_prior_relationships
from ontology_graph import load_ontology_graph
from shacl_validate import validate_graph
from synthesis_schema import BaseSynthesisGraph, build_dynamic_model

logger = logging.getLogger("ontologx_ontosyn")

REFINE_WHILE_BUDGET = (
    "Token budget remains. Re-emit this ChemicalSynthesis with higher fidelity "
    "to the hint ledger: amounts, OM-2 unit IRIs, labels, atmosphere, "
    "temperatures, durations, and sealedVessel. Fix only what the ledger "
    "supports. Do not invent steps, chemicals, or numbers."
)


def complete_snapshot_instruction(context: dict) -> str:
    """Tell correction rounds to replace, rather than patch, their output scope."""
    if context.get("source") == "final_merge_repair":
        return (
            "Emit a minimal correction delta for the existing merged graph, not a "
            "replacement graph. Include nodes only when adding or changing them. "
            "Relationships may reference existing node ids without re-emitting those "
            "nodes. Preserve all unaffected graph content. To delete an invalid edge, "
            "put its exact source_id/type/target_id triple in remove_relationships."
        )
    if context.get("extension"):
        domain = str(context.get("extension") or "extension")
        typing = (
            "To type an existing ChemicalOutput as Species, re-emit that exact "
            "id with type ontospecies:Species."
            if domain == "ontospecies"
            else "Reuse the seeded MetalOrganicPolyhedron id; do not mint a second MOP."
        )
        return (
            f"Re-emit a COMPLETE replacement snapshot for the {domain} "
            "extension layer, not a patch. Always include both top-level "
            "fields: nodes and relationships. Repeat every current-extension "
            "node and relationship that should remain. Prior main-graph nodes "
            "may stay omitted when referenced by id. "
            f"{typing}"
        )
    layer = context.get("layer")
    if layer is not None:
        return (
            f"Re-emit a COMPLETE replacement snapshot for the current iter{layer} layer, "
            "not a patch. Always include both top-level fields: nodes and relationships. "
            "Repeat every current-layer node and relationship that should remain, including "
            "unaffected content. Prior-layer nodes may stay omitted when referenced by id; "
            "if a reported violation requires deleting a prior-layer edge, put that exact "
            "source_id/type/target_id triple in remove_relationships."
        )
    return (
        "Re-emit the COMPLETE SynthesisGraph, not a patch. Always include both "
        "top-level fields: nodes and relationships, preserving unaffected content."
    )


@dataclass
class ParseUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    token_budget: int | None = None
    stop_reason: str = ""
    call_details: list[dict[str, int]] = field(default_factory=list)


def extract_call_usage(raw_schema: dict[str, Any]) -> dict[str, int]:
    raw = raw_schema.get("raw")
    usage = getattr(raw, "usage_metadata", None) or {}
    if not usage:
        meta = getattr(raw, "response_metadata", None) or {}
        usage = meta.get("token_usage") or meta.get("usage") or {}
    prompt = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    completion = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


class OntoSynParser:
    def __init__(
        self,
        llm: BaseChatModel,
        ontology_path: str,
        shacl_path: str,
        prompt: str,
        correction_steps: int = 3,
        spend_full_budget: bool = False,
        max_rounds: int | None = None,
        input_label: str = "Paper",
    ) -> None:
        self.llm = llm
        self.ontology_path = ontology_path
        self.shacl_path = shacl_path
        self.prompt = prompt
        self.correction_steps = correction_steps
        self.spend_full_budget = spend_full_budget
        self.max_rounds = max_rounds
        self.input_label = input_label
        self.ontology = load_ontology_graph(ontology_path)

        try:
            llm.with_structured_output(BaseSynthesisGraph)
        except NotImplementedError as exc:
            raise ValueError("The parser model must support structured output.") from exc

        # Do not wrap the T-Box in ChatPromptTemplate: our comments contain `{...}`.
        self.structured_model = llm.with_structured_output(
            build_dynamic_model(self.ontology),
            include_raw=True,
            method="function_calling",
        )
        self._validate_graph = validate_graph

    def parse(
        self,
        event: str,
        context: dict,
        paper_hash: str,
        extra_human: str = "",
        token_budget: int | None = None,
        existing_graph: Any = None,
        require_shacl: bool = True,
        shacl_path: str | None = None,
    ) -> tuple[Any, bool, list[str], ParseUsage]:
        corrections: list[Any] = []
        last_graph = None
        last_messages: list[str] = []
        last_conforms = False
        usage = ParseUsage(token_budget=token_budget)
        scope = paper_hash
        if context.get("entity_key"):
            scope = f"{paper_hash}/{context['entity_key']}"

        if token_budget and token_budget > 0:
            limit = self.max_rounds if self.max_rounds is not None else max(self.correction_steps, 64)
        else:
            limit = (self.correction_steps + 1) if self.max_rounds is None else self.max_rounds

        for current_step in range(limit):
            if token_budget and usage.total_tokens >= token_budget and current_step > 0:
                usage.stop_reason = "budget_exhausted"
                break
            if current_step > 0:
                logger.info(
                    "Round %s for %s spent=%s budget=%s",
                    current_step,
                    scope,
                    usage.total_tokens,
                    token_budget,
                )
            messages = [
                SystemMessage(self.prompt),
                HumanMessage(
                    f"{getattr(self, 'input_label', 'Paper')}:\n"
                    f"{event}\n\nContext: {context}{extra_human}"
                ),
                *corrections[-12:],
            ]
            raw_schema = cast("dict", self.structured_model.invoke(messages))
            call_usage = extract_call_usage(raw_schema)
            usage.calls += 1
            usage.prompt_tokens += call_usage["prompt_tokens"]
            usage.completion_tokens += call_usage["completion_tokens"]
            usage.total_tokens += call_usage["total_tokens"]
            usage.call_details.append(call_usage)

            if not raw_schema.get("parsed"):
                parse_err = raw_schema.get("parsing_error")
                if parse_err is not None:
                    logger.warning(
                        "Structured output parse failed for %s at step %s: %s",
                        scope,
                        current_step,
                        parse_err,
                    )
                else:
                    logger.warning(
                        "Structured output parse failed for %s at step %s (no parsing_error; raw=%s)",
                        scope,
                        current_step,
                        type(raw_schema.get("raw")).__name__,
                    )
                try:
                    llm_answer = cast("AIMessage", raw_schema["raw"])
                    corrections.extend(
                        [
                            AIMessage(llm_answer.content, id=llm_answer.id, tool_calls=llm_answer.tool_calls),
                            *[
                                ToolMessage("", tool_call_id=tool_call["id"])
                                for tool_call in (llm_answer.tool_calls or [])
                            ],
                            AIMessage("Done"),
                        ]
                    )
                except Exception:
                    logger.exception("No usable raw LLM output for %s", scope)
                    continue
                msg = (
                    "The SynthesisGraph tool call could not be decoded "
                    "(node/property/relationship types or field types only). "
                    "Graph correctness is checked later by SHACL, not here. "
                    + complete_snapshot_instruction(context)
                )
                if raw_schema.get("parsing_error") and getattr(raw_schema["parsing_error"], "errors", None):
                    parsing_error = cast("ValidationError", raw_schema["parsing_error"])
                    errors = [
                        {
                            "location": ".".join(map(str, err.get("loc"))),
                            "invalid_input": err.get("input"),
                        }
                        for err in parsing_error.errors()
                    ]
                    msg += (
                        " Fix these format errors while preserving and re-emitting "
                        f"all unaffected current-scope content: {errors}"
                    )
                corrections.append(HumanMessage(msg))
                if token_budget and usage.total_tokens >= token_budget:
                    usage.stop_reason = "budget_exhausted"
                    break
                continue

            parsed = raw_schema["parsed"]
            editable_base = remove_prior_relationships(
                existing_graph,
                getattr(parsed, "remove_relationships", None),
            )
            output_graph = complete_delta(parsed, editable_base, event, context)
            if editable_base is not None:
                output_graph = attach_subgraph(editable_base, output_graph)
            last_graph = output_graph
            # Graph oracle is SHACL on the attached graph, never Pydantic.
            conforms, messages_out, _ratio = self._validate_graph(
                output_graph,
                self.ontology_path,
                shacl_path or self.shacl_path,
                paper_hash,
            )
            last_conforms = bool(conforms)
            last_messages = messages_out
            if not require_shacl:
                usage.stop_reason = "layer_complete"
                return output_graph, last_conforms, last_messages, usage
            if token_budget and usage.total_tokens >= token_budget:
                usage.stop_reason = "budget_exhausted"
                return output_graph, last_conforms, last_messages, usage
            if conforms and not self.spend_full_budget:
                usage.stop_reason = "conforms" if not token_budget else "conforms_within_budget"
                return output_graph, True, messages_out, usage
            if token_budget is None and current_step >= self.correction_steps:
                usage.stop_reason = "max_rounds"
                return output_graph, last_conforms, last_messages, usage

            logger.info("SHACL violations for %s: %s", scope, messages_out[:8])
            raw_ai = raw_schema.get("raw")
            if isinstance(raw_ai, AIMessage):
                tool_calls = raw_ai.tool_calls or [
                    ToolCall(name="SynthesisGraph", args={}, id=str(uuid.uuid4()))
                ]
                corrections.extend(
                    [
                        AIMessage(raw_ai.content or "", id=raw_ai.id, tool_calls=tool_calls),
                        *[
                            ToolMessage(
                                "",
                                tool_call_id=call["id"] if isinstance(call, dict) else call.get("id", ""),
                            )
                            for call in tool_calls
                        ],
                        AIMessage("Done"),
                    ]
                )
            if conforms and self.spend_full_budget:
                corrections.append(
                    HumanMessage(
                        complete_snapshot_instruction(context) + " " + REFINE_WHILE_BUDGET
                    )
                )
            else:
                corrections.append(
                    HumanMessage(
                        f"The graph violates {('OntoSpecies' if context.get('extension') else 'OntoSynthesis')} SHACL constraints. "
                        + complete_snapshot_instruction(context)
                        + " Fix the reported violations while preserving all other "
                        "current-scope content: "
                        + " | ".join(messages_out[:20])
                    )
                )

        if last_graph is None and existing_graph is not None:
            last_graph = existing_graph
            if not usage.stop_reason:
                usage.stop_reason = "layer_kept_prior"
        if not usage.stop_reason:
            usage.stop_reason = "max_rounds"
        return last_graph, last_conforms, last_messages, usage
