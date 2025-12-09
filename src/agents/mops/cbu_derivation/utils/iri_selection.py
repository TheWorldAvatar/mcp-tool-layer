import os
import json as _json
import re as _re
import hashlib
from typing import Dict, List, Optional, Tuple
from models.ModelConfig import ModelConfig
from models.locations import DATA_DIR


def _build_prompt(entity: str, mop_labels: List[str], mop_ccdc: str, candidates: List[Dict[str, object]], metal_formula: str, organic_formula: str) -> str:
    header = (
        f"Entity: {entity}\n"
        f"MOP labels: {', '.join(mop_labels) if mop_labels else ''}\n"
        f"CCDC: {mop_ccdc}\n\n"
        f"Derived CBUs:\n- Metal formula: {metal_formula}\n- Organic formula: {organic_formula}\n\n"
        "Candidates (choose IRIs):\n"
    )
    body_lines: List[str] = []
    for idx, c in enumerate(candidates, 1):
        iri = c.get("iri")
        is_ci = c.get("is_ci")
        labels = c.get("labels")
        alt_names = c.get("alt_names")
        formulas = c.get("formulas")
        amounts = c.get("amounts")
        body_lines.append(
            f"[{idx}] IRI: {iri}\n   isChemicalInput: {is_ci}\n   labels: {labels}\n   alternativeNames: {alt_names}\n   chemicalFormulas: {formulas}\n   amounts: {amounts}"
        )

    instructions = (
        "You are selecting which candidate ChemicalBuildingUnit IRIs the derived CBUs should attach to.\n"
        "Rules:\n"
        "- metal_cbu_iri should preferably be a metal precursor or metal-bearing reagent (e.g., VOSO4, metal salts).\n"
        "- organic_cbu_iri should preferably be a multicarboxylic organic linker (e.g., dicarboxylic acids, tricarboxylic acids).\n"
        "- Match by formula fragments, names/aliases, and chemical roles.\n"
        "- IMPORTANT: You MUST select IRIs from the candidate list when possible.\n"
        "  * If no ideal metal-bearing candidate exists, select the best alternative (e.g., solvents, other chemicals).\n"
        "  * If no ideal organic linker exists, select the best alternative from available candidates.\n"
        "- If you cannot find any suitable IRI for a CBU, use empty string \"\" for that field - a new IRI will be generated automatically.\n"
        "- The two selected IRIs must be different from each other (if both are selected).\n"
        "- Empty strings are acceptable and will result in new IRIs being created.\n"
        "Return ONLY valid JSON with double quotes and no extra text."
    )

    joined = "\n".join(body_lines)
    prompt_text = (
        f"{header}{joined}\n\n{instructions}\n\n"
        'Example JSON format:\n'
        '{\n'
        '  "metal_cbu_iri": "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/...",\n'
        '  "organic_cbu_iri": "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/..."\n'
        '}\n\n'
        'Your response (JSON only):\n'
    )
    return prompt_text


def _validate_selection(metal_iri: Optional[str], organic_iri: Optional[str]) -> Tuple[bool, str]:
    """
    Validate that the selection is acceptable.
    Empty IRIs are allowed - they will be replaced with generated ones.

    Returns:
        (is_valid, reason) - True if valid, False otherwise with reason
    """
    # Allow empty IRIs - they will be generated later
    metal_iri = (metal_iri or "").strip()
    organic_iri = (organic_iri or "").strip()

    # If both are empty, that's not valid (need at least one candidate)
    if not metal_iri and not organic_iri:
        return (False, "both metal_cbu_iri and organic_cbu_iri are empty")

    # Check IRIs are different (only if both are present)
    if metal_iri and organic_iri and metal_iri == organic_iri:
        return (False, "metal_cbu_iri and organic_cbu_iri are the same")

    # Check IRIs are valid URLs (only if present)
    if metal_iri and not metal_iri.startswith("http"):
        return (False, "metal_cbu_iri is not a valid URL")
    if organic_iri and not organic_iri.startswith("http"):
        return (False, "organic_cbu_iri is not a valid URL")

    return (True, "valid")


def _generate_cbu_iri(entity: str, formula: str, cbu_type: str, hash_value: str) -> str:
    """
    Generate a new IRI for a CBU when no suitable existing IRI is found.
    Uses a hash of entity, formula, and type to create a deterministic IRI.
    """
    # Create a unique string for this CBU
    unique_str = f"{entity}_{formula}_{cbu_type}_{hash_value}"
    # Generate a hash
    hash_obj = hashlib.sha256(unique_str.encode('utf-8'))
    hash_str = hash_obj.hexdigest()
    # Take first 32 characters for a reasonable length
    short_hash = hash_str[:32]
    return f"https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/{short_hash}"


def llm_select_cbu_iris(*, entity: str, mop_labels: List[str], mop_ccdc: str, candidates: List[Dict[str, object]], metal_formula: str, organic_formula: str, hash_value: str) -> Tuple[Optional[str], Optional[str]]:
    """Build the selection prompt, persist it, call the LLM, and parse the JSON.
    Retries with validation until both IRIs are selected and different.
    Returns (metal_cbu_iri, organic_cbu_iri) or (None, None) when max retries exceeded.
    """
    print(f"[IRI-SELECTION] Starting IRI selection for {entity}")
    print(f"[IRI-SELECTION] Metal formula: {metal_formula}")
    print(f"[IRI-SELECTION] Organic formula: {organic_formula}")
    print(f"[IRI-SELECTION] Candidates: {len(candidates)}")
    max_retries = 10
    attempt = 0
    
    # Setup LLM once
    # Build and save prompt first, even if LLM fails
    prompt_text = _build_prompt(entity, mop_labels, mop_ccdc, candidates, metal_formula, organic_formula)
    try:
        sel_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "prompts")
        os.makedirs(sel_dir, exist_ok=True)
        with open(os.path.join(sel_dir, f"{entity}_iri_selection_prompt.md"), "w", encoding="utf-8") as pf:
            pf.write(prompt_text)
    except Exception:
        pass  # Don't let prompt saving prevent execution

    try:
        from models.LLMCreator import LLMCreator
        # Note: gpt-4.1-mini only supports temperature=1.0 (default), not 0.0
        mc = ModelConfig(temperature=1.0, top_p=0.02)
        llm = LLMCreator(model="gpt-4.1-mini", remote_model=True, model_config=mc, structured_output=False, structured_output_schema=None).setup_llm()
    except Exception as e:
        # Save debug information even when LLM init fails
        try:
            sel_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "debug")
            os.makedirs(sel_dir, exist_ok=True)
            debug_file = os.path.join(sel_dir, f"{entity}_iri_selection_DEBUG.md")
            with open(debug_file, "w", encoding="utf-8") as df:
                df.write(f"# IRI Selection Debug - {entity}\n\n")
                df.write(f"**Timestamp:** {__import__('datetime').datetime.now().isoformat()}\n\n")
                df.write(f"**Error:** Failed to initialize LLM\n")
                df.write(f"**Exception:** {str(e)}\n\n")
                df.write("## Full Prompt\n\n")
                df.write(prompt_text)
                df.write("\n\n## Input Summary\n\n")
                df.write(f"**Metal Formula:** {metal_formula}\n")
                df.write(f"**Organic Formula:** {organic_formula}\n")
                df.write(f"**MOP Labels:** {mop_labels}\n")
                df.write(f"**CCDC:** {mop_ccdc}\n")
                df.write(f"**Candidates:** {len(candidates)} candidates\n")
                for i, cand in enumerate(candidates[:5]):  # Show first 5 candidates
                    df.write(f"  - {i+1}: {cand.get('labels', [])} -> {cand.get('iri', '')}\n")
        except Exception:
            pass  # Don't let debug saving fail the error reporting

        error_msg = f"CRITICAL: Failed to initialize LLM for IRI selection: {e}. " \
                   f"This usually indicates missing dependencies (langchain_openai, openai) or invalid API configuration. " \
                   f"Debug information saved to cbu_derivation/selection/debug/{entity}_iri_selection_DEBUG.md"
        print(error_msg)
        # Re-raise the exception to fail the pipeline
        raise RuntimeError(error_msg) from e
    try:
        sel_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "prompts")
        os.makedirs(sel_dir, exist_ok=True)
        with open(os.path.join(sel_dir, f"{entity}_iri_selection_prompt.md"), "w", encoding="utf-8") as pf:
            pf.write(prompt_text)
    except Exception:
        pass
    
    # Retry loop with validation
    all_responses = []
    import time
    
    while attempt < max_retries:
        attempt += 1
        try:
            # Call LLM
            resp_obj = llm.invoke(prompt_text)
            resp = getattr(resp_obj, "content", None) if resp_obj is not None else None
            if not isinstance(resp, str):
                resp = str(resp_obj) if resp_obj is not None else ""
            
            all_responses.append({
                "attempt": attempt,
                "response": resp,
                "timestamp": __import__('datetime').datetime.now().isoformat()
            })
            
            # Attempt to extract JSON
            m = _re.search(r"\{[\s\S]*\}", resp)
            if not m:
                print(f"  ⚠️  IRI selection attempt {attempt}/{max_retries}: No JSON found in response")
                continue
            
            json_str = m.group(0)
            # Be lenient: accept single quotes if the model mirrored the example
            data = _json.loads(json_str.replace("'", '"'))
            
            # Extract IRIs
            metal_iri = data.get("metal_cbu_iri") or ""
            organic_iri = data.get("organic_cbu_iri") or ""
            
            # Validate selection
            is_valid, reason = _validate_selection(metal_iri, organic_iri)
            
            if is_valid:
                print(f"  ✅ IRI selection succeeded on attempt {attempt}/{max_retries}")

                # Generate new IRIs for any missing ones
                final_metal_iri = metal_iri
                final_organic_iri = organic_iri

                if not final_metal_iri and metal_formula:
                    final_metal_iri = _generate_cbu_iri(entity, metal_formula, "metal", hash_value)
                    print(f"  🆕 Generated new IRI for metal CBU: {final_metal_iri}")

                if not final_organic_iri and organic_formula:
                    final_organic_iri = _generate_cbu_iri(entity, organic_formula, "organic", hash_value)
                    print(f"  🆕 Generated new IRI for organic CBU: {final_organic_iri}")

                # Save final successful response
                try:
                    resp_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "responses")
                    os.makedirs(resp_dir, exist_ok=True)
                    with open(os.path.join(resp_dir, f"{entity}_iri_selection_response.md"), "w", encoding="utf-8") as rf:
                        rf.write(resp)
                except Exception:
                    pass

                # Save selection result as JSON
                try:
                    result_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "results")
                    os.makedirs(result_dir, exist_ok=True)
                    selection_result = {
                        "entity": entity,
                        "mop_ccdc": mop_ccdc,
                        "metal_cbu": {
                            "formula": metal_formula,
                            "selected_iri": final_metal_iri,
                            "generated": final_metal_iri != metal_iri
                        },
                        "organic_cbu": {
                            "formula": organic_formula,
                            "selected_iri": final_organic_iri,
                            "generated": final_organic_iri != organic_iri
                        },
                        "candidates": candidates,
                        "attempts": attempt,
                        "all_attempts": all_responses,
                        "timestamp": __import__('datetime').datetime.now().isoformat()
                    }
                    with open(os.path.join(result_dir, f"{entity}_selection.json"), "w", encoding="utf-8") as jf:
                        _json.dump(selection_result, jf, indent=2, ensure_ascii=False)
                except Exception:
                    pass

                return (final_metal_iri, final_organic_iri)
            else:
                print(f"  ⚠️  IRI selection attempt {attempt}/{max_retries} failed: {reason}")
                if attempt < max_retries:
                    # Add feedback to prompt for next attempt
                    prompt_text = _build_prompt(entity, mop_labels, mop_ccdc, candidates, metal_formula, organic_formula)
                    prompt_text += f"\n\n[RETRY FEEDBACK - Attempt {attempt}]: Previous attempt failed: {reason}. Please ensure you select TWO DIFFERENT IRIs, one for metal and one for organic CBU."
                    # Wait before retry
                    time.sleep(2)  # 2 second delay between retries
        
        except _json.JSONDecodeError as e:
            print(f"  ⚠️  IRI selection attempt {attempt}/{max_retries}: JSON parsing error - {e}")
            if attempt < max_retries:
                time.sleep(2)  # 2 second delay before retry
            continue
        except Exception as e:
            print(f"  ⚠️  IRI selection attempt {attempt}/{max_retries}: Error - {e}")
            if attempt < max_retries:
                time.sleep(2)  # 2 second delay before retry
            continue
    
    # Max retries exceeded - save all attempts for debugging
    print(f"  ❌ IRI selection failed after {max_retries} attempts")
    try:
        result_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "results")
        os.makedirs(result_dir, exist_ok=True)
        failed_result = {
            "entity": entity,
            "mop_ccdc": mop_ccdc,
            "metal_cbu": {
                "formula": metal_formula,
                "selected_iri": ""
            },
            "organic_cbu": {
                "formula": organic_formula,
                "selected_iri": ""
            },
            "candidates": candidates,
            "status": "failed",
            "attempts": max_retries,
            "all_attempts": all_responses,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
        with open(os.path.join(result_dir, f"{entity}_selection.json"), "w", encoding="utf-8") as jf:
            _json.dump(failed_result, jf, indent=2, ensure_ascii=False)
    except Exception:
        pass
    
    return (None, None)
