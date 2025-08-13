"""
This is for agents to extract more focused information given a query, e.g., 

the chemical formula of the chemical in the paper, the paper, or anything. 

The function searches for all the occurrences of the query in the paper,

extract 3 sentences each before and after the query occurence.

try with example article 10.1021_acs.inorgchem.4c02394, with query H2NDBDC


"""


from __future__ import annotations

import re
from typing import List, Dict, Any, Tuple, Optional


_SPACY_NLP = None  # lazy-initialized spaCy pipeline


def _get_spacy_pipeline(preferred_model: Optional[str] = None):
    global _SPACY_NLP
    if _SPACY_NLP is not None:
        return _SPACY_NLP
    try:
        import spacy  # type: ignore
    except Exception:
        return None

    models: List[str] = []
    if preferred_model:
        models.append(preferred_model)
    models.extend([
        "en_core_sci_sm",  # scispaCy (recommended for scientific text)
        "en_core_web_trf",
        "en_core_web_md",
        "en_core_web_sm",
    ])

    for model in models:
        try:
            _SPACY_NLP = spacy.load(model, exclude=["ner"])  # NER not needed
            return _SPACY_NLP
        except Exception:
            continue

    # Fallback: blank English with sentencizer
    try:
        from spacy.lang.en import English  # type: ignore
        nlp = English()
        if not nlp.has_factory("sentencizer"):
            nlp.add_pipe("sentencizer")
        else:
            nlp.add_pipe("sentencizer")
        _SPACY_NLP = nlp
        return _SPACY_NLP
    except Exception:
        return None


def _split_into_sentences(text: str) -> List[Tuple[str, int, int]]:
    """Split text into sentences (sentence, start_char, end_char) using spaCy if available.

    Falls back to a simple punctuation-based splitter if spaCy/scispaCy isn't available.
    """
    if not text:
        return []

    nlp = _get_spacy_pipeline()
    if nlp is not None:
        try:
            doc = nlp(text)
            out: List[Tuple[str, int, int]] = []
            for s in doc.sents:
                sent = s.text.strip()
                if sent:
                    out.append((sent, s.start_char, s.end_char))
            if out:
                return out
        except Exception:
            pass

    # Fallback naive splitter
    working = text
    sentence_end_re = re.compile(r"([\.\?\!])")
    parts: List[Tuple[str, int, int]] = []
    start = 0
    i = 0
    while i < len(working):
        m = sentence_end_re.search(working, i)
        if not m:
            chunk = working[start:]
            if chunk.strip():
                parts.append((chunk.strip(), start, len(working)))
            break
        end_idx = m.end()
        chunk = working[start:end_idx]
        if chunk.strip():
            parts.append((chunk.strip(), start, end_idx))
        j = end_idx
        while j < len(working) and working[j].isspace():
            j += 1
        start = j
        i = j
    return parts


def in_context_search(
    text: str,
    query: str,
    sentences_before: int = 3,
    sentences_after: int = 3,
    case_sensitive: bool = False,
    max_results: int | None = None,
) -> List[Dict[str, Any]]:
    """Find occurrences of `query` and return surrounding sentence context windows.

    Args:
        text: Full document text to search.
        query: Substring to locate (no regex; treated literally).
        sentences_before: Number of sentences to include before the hit.
        sentences_after: Number of sentences to include after the hit.
        case_sensitive: If False, performs case-insensitive search.
        max_results: Optional cap on number of returned contexts.

    Returns:
        List of dicts with keys:
        - match_index: index of this match among all matches
        - match_span: (start, end) character indices in the original text
        - sentence_index: index of the sentence containing the match
        - window_range: (start_sentence_index, end_sentence_index_exclusive)
        - center_sentence: the sentence containing the match
        - snippet: concatenated window text (sentences_before .. after)
        - sentences: list of sentences in the window
    """
    if not text or not query:
        return []

    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    # Build an array of (start, end) for sentences to map spans to sentence index
    sent_spans = [(s[1], s[2]) for s in sentences]

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    results: List[Dict[str, Any]] = []
    for match_index, m in enumerate(pattern.finditer(text)):
        start_char, end_char = m.span()

        # Find sentence containing this match
        sent_idx = None
        for idx, (s_start, s_end) in enumerate(sent_spans):
            if s_start <= start_char < s_end:
                sent_idx = idx
                break
        if sent_idx is None:
            # Fallback: skip if not mapped
            continue

        w_start = max(0, sent_idx - max(0, sentences_before))
        w_end = min(len(sentences), sent_idx + max(0, sentences_after) + 1)
        window_sents = [s for (s, _, _) in sentences[w_start:w_end]]
        center_sentence = sentences[sent_idx][0]
        snippet = " ".join(window_sents)

        results.append({
            "match_index": match_index,
            "match_span": (start_char, end_char),
            "sentence_index": sent_idx,
            "window_range": (w_start, w_end),
            "center_sentence": center_sentence,
            "snippet": snippet,
            "sentences": window_sents,
        })

        if max_results is not None and len(results) >= max_results:
            break

    return results


if __name__ == "__main__":
    from models.locations import SANDBOX_TASK_DIR
    import os
    doi = "10.1021_acs.inorgchem.4c02394"
    md_path = os.path.join(SANDBOX_TASK_DIR, doi, f"{doi}_complete.md")
    with open(md_path, "r", encoding="utf-8") as f:
        paper_text = f.read()
    hits = in_context_search(paper_text, "H2NDBDC", sentences_before=3, sentences_after=3)
    for h in hits:
        print(f"- Match #{h['match_index']} @ sentences {h['window_range']}:\n  {h['snippet']}\n")

