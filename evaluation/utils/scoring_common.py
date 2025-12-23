import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def to_fingerprint(value: Any) -> str:
    """Normalize a value for order-insensitive, robust comparison.
    - Trim and collapse whitespace
    - Lowercase for case-insensitive matching
    - Convert Unicode subscript/superscript to regular characters
    - Map common placeholders ("", "n/a", "na", "-1", "-1.0", "-1e+00") to empty string
    - Normalize Unicode primes/quotes to ASCII to avoid spurious FP/FN
    """
    s = "" if value is None else str(value)
    s = s.strip()
    low = s.lower()
    if low in ("", "n/a", "na", "-1", "-1.0", "-1e+00", "-1e+0", "-1.00"):
        return ""

    # Convert Unicode subscript/superscript characters to regular characters
    # Subscripts: ₀₁₂₃₄₅₆₇₈₉ → 0123456789
    # Superscripts: ⁰¹²³⁴⁵⁶⁷⁸⁹ → 0123456789
    # Other Unicode chars: · → ., etc.
    unicode_map = {
        # Subscripts
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        # Superscripts
        '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        # Other Unicode characters
        '·': '.', '•': '.', '⋅': '.', '×': 'x', '÷': '/',
        'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta',
        'ε': 'epsilon', 'μ': 'mu', 'ν': 'nu', 'π': 'pi',
    }

    for unicode_char, ascii_char in unicode_map.items():
        s = s.replace(unicode_char, ascii_char)

    # normalize unicode primes/quotes:
    # ‴ (U+2034) → ″ (U+2033), then map primes to ASCII ' and double primes to ASCII "
    try:
        s = s.replace("\u2034", "\u2033")  # ‴ -> ″
        s = s.replace("\u2032", "'")       # ′ -> '
        s = s.replace("\u2033", '"')        # ″ -> "
        # also handle curly quotes often used instead of primes
        s = s.replace("\u2019", "'")       # ’ -> '
        s = s.replace("\u201D", '"')        # " -> "
    except Exception:
        pass
    # normalize whitespace and case
    s = " ".join(s.split())
    return s.lower()


def precision_recall_f1(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
    return (prec, rec, f1)


def score_lists(gt_list: List[str], res_list: List[str]) -> Tuple[int, int, int]:
    """Return (tp, fp, fn) comparing two lists as multisets using fingerprints."""
    from collections import Counter

    gt_fp = [to_fingerprint(x) for x in gt_list if to_fingerprint(x) != '""']
    res_fp = [to_fingerprint(x) for x in res_list if to_fingerprint(x) != '""']

    gt_c = Counter(gt_fp)
    res_c = Counter(res_fp)
    all_keys = set(gt_c) | set(res_c)
    tp = sum(min(gt_c[k], res_c[k]) for k in all_keys)
    fp = sum(max(0, res_c[k] - gt_c.get(k, 0)) for k in all_keys)
    fn = sum(max(0, gt_c[k] - res_c.get(k, 0)) for k in all_keys)
    return (tp, fp, fn)


def hash_map_reverse(path: Path) -> Dict[str, str]:
    """Return mapping hash->doi from data/doi_to_hash.json (keys are DOIs with underscores)."""
    h2d: Dict[str, str] = {}
    if not path.exists():
        return h2d
    try:
        d2h = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d2h, dict):
            for doi, hv in d2h.items():
                sdoi = str(doi).strip()
                shv = str(hv).strip()
                if len(shv) == 8:
                    h2d[shv] = sdoi
    except Exception:
        pass
    return h2d


def render_report(title: str, rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]]) -> str:
    out: List[str] = []
    out.append(f"# {title}")
    out.append("")
    out.append("| Hash | TP | FP | FN | Precision | Recall | F1 |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    sum_tp = sum_fp = sum_fn = 0
    for hv, (tp, fp, fn, prec, rec, f1) in rows:
        out.append(f"| {hv} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |")
        sum_tp += tp
        sum_fp += fp
        sum_fn += fn
    op, orc, of1 = precision_recall_f1(sum_tp, sum_fp, sum_fn)
    out.append("")
    out.append(f"Overall: TP={sum_tp}, FP={sum_fp}, FN={sum_fn}, Precision={op:.3f}, Recall={orc:.3f}, F1={of1:.3f}")
    out.append("")
    return "\n".join(out)


