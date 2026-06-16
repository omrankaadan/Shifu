"""
chat_engine.py
--------------
Zero-cost "chatbot" over a PDF using extractive retrieval (no external APIs).

Given a question, it:
- Extracts / loads per-page text
- Scores pages by simple TF-IDF-like overlap with the query
- Returns the best matching page snippets + lightweight synthesized answer

This is NOT a generative LLM; it's an extractive helper to chat "about the PDF".
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional

from ai_engine import extract_pages_text


_WORD_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)


def _tokenize(s: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(s or "") if len(t) >= 2]


def _load_pages_cached(pdf_path: str) -> List[str]:
    cache_path = pdf_path + ".pages.json"
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pages = data.get("pages")
            if isinstance(pages, list) and all(isinstance(x, str) for x in pages):
                return pages
        except Exception:
            pass
    pages = extract_pages_text(pdf_path)
    # best-effort cache
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"pages": pages}, f, ensure_ascii=False)
    except Exception:
        pass
    return pages


def _score_pages(pages: List[str], query: str) -> List[Tuple[int, float]]:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    # document frequency for query tokens
    N = max(1, len(pages))
    df: Dict[str, int] = {t: 0 for t in set(q_tokens)}
    page_tokens: List[List[str]] = []
    for p in pages:
        toks = _tokenize(p)
        page_tokens.append(toks)
        uniq = set(toks)
        for t in df.keys():
            if t in uniq:
                df[t] += 1

    # IDF
    idf = {t: (math.log((N + 1) / (df[t] + 1)) + 1.0) for t in df.keys()}

    scored: List[Tuple[int, float]] = []
    for idx, toks in enumerate(page_tokens, start=1):
        if not toks:
            continue
        tf: Dict[str, int] = {}
        for t in toks:
            if t in idf:
                tf[t] = tf.get(t, 0) + 1

        score = 0.0
        for t in q_tokens:
            if t in tf:
                score += idf[t] * (1.0 + math.log(tf[t]))
        if score > 0:
            scored.append((idx, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _snippet_for_page(page_text: str, query: str, max_len: int = 420) -> str:
    if not page_text:
        return ""
    text = re.sub(r"\s+", " ", page_text).strip()
    if not text:
        return ""

    q_tokens = _tokenize(query)
    # find first token occurrence
    pos = None
    low = text.lower()
    for t in q_tokens[:6]:
        p = low.find(t)
        if p != -1:
            pos = p
            break

    if pos is None:
        return text[:max_len] + ("…" if len(text) > max_len else "")

    start = max(0, pos - 140)
    end = min(len(text), start + max_len)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _load_analysis_cached(pdf_path: str) -> Dict[str, Any]:
    """Best-effort load of saved analysis JSON alongside the uploaded pdf."""
    try:
        ap = pdf_path + ".analysis.json"
        if os.path.exists(ap):
            with open(ap, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _match_analysis_items(analysis: Dict[str, Any], query: str, *, cap: int = 6) -> List[Dict[str, Any]]:
    """Find analysis items (key points / definitions / formulas) that overlap the query tokens."""
    q = set(_tokenize(query))
    if not q:
        return []
    matches: List[Dict[str, Any]] = []

    def add(kind: str, item: Any):
        if len(matches) >= cap:
            return
        if isinstance(item, str):
            text = item.strip()
            sources = []
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("term") or item.get("formula") or "").strip()
            sources = item.get("sources") or item.get("pages") or []
        else:
            text = str(item).strip()
            sources = []
        if not text:
            return
        toks = set(_tokenize(text))
        if not toks:
            return
        score = len(toks & q) / max(1, len(q))
        if score >= 0.25:
            matches.append({"type": kind, "text": text, "sources": sources})

    for it in analysis.get("key_points") or []:
        add("key_point", it)
    for it in analysis.get("definitions") or []:
        # definitions might be dicts with term/definition
        if isinstance(it, dict) and ("term" in it or "definition" in it):
            term = str(it.get("term") or "").strip()
            definition = str(it.get("definition") or "").strip()
            add("definition", {"text": f"{term}: {definition}" if term else definition, "sources": it.get("sources")})
        else:
            add("definition", it)
    for it in analysis.get("formulas") or []:
        if isinstance(it, dict):
            expr = str(it.get("formula") or it.get("latex") or "").strip()
            meaning = str(it.get("meaning") or it.get("explanation") or "").strip()
            add("formula", {"text": f"{expr} — {meaning}" if meaning else expr, "sources": it.get("sources")})
        else:
            add("formula", it)

    return matches[:cap]


def _llm_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_KEY"))


def _llm_answer(message: str, sources: List[Dict[str, Any]], analysis: Dict[str, Any]) -> Optional[str]:
    """Generate a helpful answer grounded in retrieved snippets + analysis, if OpenAI is configured."""
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_CHAT_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
    client = OpenAI(api_key=api_key)

    context_blocks = []
    for s in sources[:4]:
        p = s.get("page")
        sn = (s.get("snippet") or "").strip()
        if sn:
            context_blocks.append(f"[Page {p}] {sn}")

    # Use the saved analysis summary as optional context (kept short)
    summary = ""
    try:
        raw = analysis.get("result") or analysis
        summary = str(raw.get("summary") or "")[:900]
    except Exception:
        summary = ""

    system = (
        "You are an assistant helping a user understand a PDF. "
        "Answer ONLY using the provided context snippets and (if present) the analysis summary. "
        "Be clear, concise, and practical. "
        "If the answer is not in the context, say so and suggest what to search for in the PDF. "
        "Always include page citations in parentheses like (p.3) when you state a factual claim from a snippet."
    )

    user = (
        f"User question: {message}\n\n"
        + (f"Document summary (may be incomplete):\n{summary}\n\n" if summary else "")
        + "Relevant excerpts:\n"
        + ("\n".join(context_blocks) if context_blocks else "(no excerpts found)")
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=550,
    )
    out = resp.choices[0].message.content if resp.choices else ""
    return (out or "").strip() or None


def chat_about_pdf(pdf_path: str, message: str, *, top_k: int = 3) -> Dict[str, Any]:
    pages = _load_pages_cached(pdf_path)
    scored = _score_pages(pages, message)[: max(1, top_k)]

    sources: List[Dict[str, Any]] = []
    for page_no, _ in scored:
        ptext = pages[page_no - 1] if 0 <= page_no - 1 < len(pages) else ""
        sources.append(
            {
                "page": page_no,
                "snippet": _snippet_for_page(ptext, message),
            }
        )

    analysis = _load_analysis_cached(pdf_path)
    matches = _match_analysis_items((analysis.get("result") or analysis) if isinstance(analysis, dict) else {}, message)

    if not sources:
        return {
            "answer": "I couldn't find a clear match in the PDF for that question. Try using keywords that appear in the document (section titles, variable names, or exact terms).",
            "sources": [],
            "matches": matches,
            "mode": "extractive",
        }

    # Prefer an LLM answer if configured; otherwise fall back to extractive synthesis
    llm = _llm_answer(message, sources, analysis)
    if llm:
        return {"answer": llm, "sources": sources, "matches": matches, "mode": "llm"}

    excerpts = []
    for s in sources:
        sn = (s.get("snippet") or "").strip()
        if sn:
            excerpts.append(sn)

    if len(excerpts) == 1:
        answer = "From the document, the most relevant part says:\n\n" + excerpts[0]
    else:
        answer = "Here are the most relevant excerpts from the document:\n\n" + "• " + "\n• ".join(excerpts[:3])

    return {"answer": answer, "sources": sources, "matches": matches, "mode": "extractive"}
