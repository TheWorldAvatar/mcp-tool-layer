from evaluation.scoring_steps import _find_best_synthesis_match


def _synth(name: str, ccdc: str = "") -> dict:
    return {"productNames": [name], "productCCDCNumber": ccdc, "steps": []}


def test_shared_ccdc_prefers_name_over_stamped_number() -> None:
    gt = [
        _synth("cu24(tbu-amide-bdc)24 via solvothermal method", "183513 1"),
        _synth("cu24(h-bdc)24", "183513 1"),
    ]
    preds = [
        _synth("synthesis of cu24(h-bdc)24 cage by mechanochemical method", "1835131"),
        _synth("synthesis of cu24(tbu-amide-bdc)24 by solvothermal method", ""),
    ]
    idx, matched = _find_best_synthesis_match(gt[0], preds, set(), gt_synths=gt)
    assert idx == 1
    assert "solvothermal" in matched["productNames"][0]


def test_unique_compound_index_pairs_when_ccdc_empty() -> None:
    gt = [
        _synth("[zr3o] formula one", ""),
        _synth("ZrT-2", ""),
    ]
    preds = [
        _synth("synthesis of 1", ""),
        _synth("synthesis of 2", ""),
    ]
    idx, matched = _find_best_synthesis_match(gt[1], preds, set(), gt_synths=gt)
    assert idx == 1
    assert matched["productNames"] == ["synthesis of 2"]


def test_unique_ccdc_still_pairs_when_names_differ() -> None:
    gt = [
        _synth("[zr3o] formula one", "95033 0"),
        _synth("[zr3o] formula two", "95033 1"),
    ]
    preds = [
        _synth("synthesis of 1", "95033 0"),
        _synth("synthesis of 2", "95033 1"),
    ]
    idx, matched = _find_best_synthesis_match(gt[1], preds, set(), gt_synths=gt)
    assert idx == 1
    assert matched["productNames"] == ["synthesis of 2"]
