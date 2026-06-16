"""
ai_engine.py
-------------
PDF analysis pipeline using OpenAI (if configured) + deterministic page references.

Key upgrades:
- 3 analysis levels: low / normal / big (normal is default)
- Adds "sources" (page numbers) to extracted ideas without hallucinating
- Caches extracted pages as {pdf_path}.pages.json (best-effort)
"""

from __future__ import annotations

import json
import os
import re 
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import PyPDF2
import hashlib
# New OpenAI SDK (recommended)
try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini-2024-07-18")

# Safety limits (can be overridden via env)
DEFAULT_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "40"))
_GPT_CACHE = {}

@dataclass(frozen=True)
class AnalysisConfig:
    name: str
    max_pages: int
    pages_per_chunk: int
    chunk_max_tokens: int
    final_max_tokens: int
    # prompt detail knobs
    key_points_cap: int
    highlights_cap: int
    definitions_cap: int
    formulas_cap: int


LEVELS: Dict[str, AnalysisConfig] = {
    "low": AnalysisConfig(
        name="low",
        max_pages=25,
        pages_per_chunk=2,
        chunk_max_tokens=1200,
        final_max_tokens=1500,
        key_points_cap=10,
        highlights_cap=10,
        definitions_cap=8,
        formulas_cap=8,
    ),

    "normal": AnalysisConfig(
        name="normal",
        max_pages=60,
        pages_per_chunk=3,
        chunk_max_tokens=2500,
        final_max_tokens=3000,
        key_points_cap=20,
        highlights_cap=20,
        definitions_cap=15,
        formulas_cap=20,
    ),

    "big": AnalysisConfig(
        name="big",
        max_pages=70,
        pages_per_chunk=5,
        chunk_max_tokens=4500,
        final_max_tokens=5000,
        key_points_cap=30,
        highlights_cap=30,
        definitions_cap=25,
        formulas_cap=30,
    ),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
def _cache_key(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

def _get_client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai package is missing. Install it with: pip install openai")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment.")
    return OpenAI(api_key=api_key)


# -----------------------------
# Extract text from PDF
# -----------------------------
def extract_pages_text(file_path: str, max_pages: int = DEFAULT_MAX_PAGES) -> List[str]:
    """Extract per-page text.

    - First try native PDF text extraction (fast).
    - If the PDF looks scanned (most pages empty), fall back to OCR using pdf2image + Tesseract.
    """
    pages: List[str] = []
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        total = min(len(reader.pages), max_pages)
        for i in range(total):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                t = ""
            t = t.replace("", "").strip()
            pages.append(t)

    # Heuristic: scanned PDFs often have near-empty extracted text
    empty = sum(1 for p in pages if len(p.strip()) < 20)
    total_chars = sum(len(p) for p in pages)
    looks_scanned = (len(pages) > 0) and (empty / max(1, len(pages)) >= 0.6) and (total_chars < 1500)

    if looks_scanned:
        try:
            from pdf2image import convert_from_path
            import pytesseract
            from PIL import Image  # noqa: F401

            ocr_pages: List[str] = []
            # Convert only the range we need (1-indexed for pdf2image)
            images = convert_from_path(file_path, dpi=220, first_page=1, last_page=total)
            for img in images:
                try:
                    text = pytesseract.image_to_string(img, lang="eng") or ""
                except Exception:
                    text = ""
                text = text.replace("", "").strip()
                ocr_pages.append(text)
            if len(ocr_pages) == len(pages):
                pages = ocr_pages
        except Exception:
            # OCR is best-effort; keep the original extracted pages
            pass

    return pages



def _load_or_extract_pages(file_path: str, *, max_pages: int) -> List[str]:
    cache_path = file_path + ".pages.json"
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pages = data.get("pages")
            if isinstance(pages, list) and all(isinstance(x, str) for x in pages):
                return pages[:max_pages]
        except Exception:
            pass

    pages = extract_pages_text(file_path, max_pages=max_pages)

    # best-effort cache
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"pages": pages}, f, ensure_ascii=False)
    except Exception:
        pass

    return pages


def _chunk_pages(pages: List[str], pages_per_chunk: int) -> List[Tuple[List[int], str]]:
    """
    Returns a list of (page_numbers, chunk_text).
    Page numbers are 1-based.
    """
    chunks: List[Tuple[List[int], str]] = []
    buf_txt: List[str] = []
    buf_pages: List[int] = []

    for i, p in enumerate(pages, start=1):
        if not (p and p.strip()):
            continue
        buf_txt.append(f"[Page {i}]\n{p}")
        buf_pages.append(i)

        if len(buf_pages) >= pages_per_chunk:
            chunks.append((buf_pages[:], "\n\n".join(buf_txt)))
            buf_txt, buf_pages = [], []

    if buf_pages:
        chunks.append((buf_pages[:], "\n\n".join(buf_txt)))
    return chunks

def _chunk_selected_pages(
    selected: List[Tuple[int, str]],
    pages_per_chunk: int,
    overlap: int = 1,
) -> List[Tuple[List[int], str]]:

    chunks = []

    if not selected:
        return chunks

    step = max(1, pages_per_chunk - overlap)

    for start in range(0, len(selected), step):
        block = selected[start:start + pages_per_chunk]

        if not block:
            continue

        pages = [p for p, _ in block]
        text = "\n\n".join(
            f"[Page {p}]\n{txt}"
            for p, txt in block
            if txt.strip()
        )

        if text.strip():
            chunks.append((pages, text))

    return chunks


# -----------------------------
# AI prompts
# -----------------------------
SYSTEM_PROMPT = (
    "You are a careful PDF analyst for pdf documents. "
    "You MUST be precise and avoid hallucinations. If something is not present, omit it. "
    "Return ONLY valid JSON that matches the requested schema."
)

CHUNK_PROMPT = """
Analyze the following PDF excerpt and extract ONLY the core informational content.

Rules:
- Focus on concepts, definitions, theorems/results, assumptions, and step-by-step reasoning that is part of the main explanation.
- EXCLUDE or heavily downplay: worked examples, exercises, problem sets, "Try it yourself", "Questions", and solution walkthroughs.
- If a section is mostly exercises/examples, you may mention the topic briefly but do not copy the problems.
Extract every mathematical formula exactly.

Do not simplify.

Preserve fractions, powers,
integrals, summations,
subscripts and superscripts.

Return valid LaTeX.
Return JSON exactly with keys:
summary: string
key_points: string[]
highlights: string[]
definitions: string[]   (terms + meaning if explicitly stated)
formulas: [
{
    "formula": string,
    "latex": string,
    "meaning": string,
    "variables": string[]
    "derivation_steps": string[] (if the formula is derived step-by-step in the text, include those steps here as strings; otherwise, return an empty array)
}
]  (ONLY if explicitly present in the excerpt)

PDF excerpt:
{TEXT}
""".strip()


FINAL_PROMPT = """
You will receive merged outputs from multiple chunks of the same PDF.
Your job is to produce a clean, comprehensive final analysis with deduplicated items.
Provide a detailed structured summary.

The length should scale with
the complexity and size of the document.
Rules:
- Keep the summary faithful to the document and as complete as possible (still readable).
- Do NOT include exercises/examples/problem statements; focus on what the learner must understand.
- If important context is missing, do not invent it.

Return JSON exactly with keys:
summary: string (separated by newlines)
key_points: string[]
highlights: string[]
definitions: string[]
important_notes: string[]
formulas: [ {{ "formula": string, "latex": string, "meaning": string, "variables": string[] }} ]

Merged content (may be repetitive):
{TEXT}
""".strip()



def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()

    # Remove code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = text.replace("```", "").strip()

    # Try direct
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to extract the first {...} block
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _call_gpt_json(client: "OpenAI", prompt: str, *, model: str, max_tokens: int) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    data = _try_parse_json(content)
    if not data:
        # fallback: keep API contract stable
        return {
            "summary": content[:2000],
            "key_points": [],
            "highlights": [],
            "definitions": [],
            "formulas": [],
        }
    return data


# -----------------------------
# Normalization + merging (adds sources deterministically)
# -----------------------------
def _wrap_items_with_sources(items: Any, pages: List[int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for x in items:
        if isinstance(x, dict):
            text = str(x.get("text") or x.get("value") or x.get("item") or "").strip()
            if not text:
                # allow already-string dicts like {"formula": ...}
                continue
            src = x.get("sources")
            if isinstance(src, list) and src:
                sources = [int(p) for p in src if str(p).isdigit()]
            else:
                sources = pages[:]
            out.append({"text": text, "sources": sources})
        else:
            text = str(x).strip()
            if text:
                out.append({"text": text, "sources": pages[:]})
    return out


def _wrap_formulas_with_sources(items, pages):
    out = []

    if not isinstance(items, list):
        return out

    for f in items:
        if not isinstance(f, dict):
            continue

        expr = str(f.get("formula", "")).strip()

        if not expr:
            continue

        out.append({
            "formula": expr,
            "latex": str(f.get("latex", "")).strip(),
            "meaning": str(f.get("meaning", "")).strip(),
            "variables": f.get("variables", []),
            "sources": pages[:],
        })

    return out

def _dedupe_items(objs: List[Dict[str, Any]], *, key_field: str = "text") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for o in objs:
        val = str(o.get(key_field, "")).strip()
        if not val:
            continue
        k = re.sub(r"\s+", " ", val).lower()
        if k not in seen:
            seen[k] = o
        else:
            # merge sources
            s1 = set(seen[k].get("sources") or [])
            s2 = set(o.get("sources") or [])
            seen[k]["sources"] = sorted({*s1, *s2})
    out = list(seen.values())
    return out



# -----------------------------
# Formula helpers (best-effort)
# -----------------------------
_MATH_LINE_RE = re.compile(
    r"(=|→|⇒|⇔|÷|±|∑|∫|√|≤|≥|≈|≠|"
    r"\b(?:sin|cos|tan|log|ln|exp|lim|max|min)\b|"
    r"[\^_]|"
    r"[πμσλΔΩ∂]|"
    r"[A-Za-z]+\([A-Za-z0-9_, ]+\))"
)
def _plain_to_latex(expr: str) -> str:
    """Very lightweight conversion from plain OCR/extracted text to LaTeX.

    This is intentionally conservative; it's only meant to make rendering nicer with MathJax.
    """
    s = (expr or "").strip()
    if not s:
        return ""
    # Basic replacements
    s = s.replace("≤", "\\leq ").replace("≥", "\\geq ").replace("≈", "\\approx ").replace("≠", "\\neq ")
    s = s.replace("∑", "\\sum ").replace("∫", "\\int ").replace("√", "\\sqrt{}")
    # Common greek letters (OCR sometimes returns unicode)
    greek = {
        "α":"\\alpha", "β":"\\beta", "γ":"\\gamma", "δ":"\\delta", "ε":"\\epsilon",
        "μ":"\\mu", "σ":"\\sigma", "λ":"\\lambda", "π":"\\pi", "Δ":"\\Delta", "Ω":"\\Omega", "θ":"\\theta"
    }
    for k,v in greek.items():
        s = s.replace(k, v+" ")
    # Trim repeated spaces
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(
    r'([A-Za-z0-9])_([A-Za-z0-9]+)',
    r'\1_{\2}',
    s
)
    return s

def _extract_formula_candidates(pages: List[str], pages_idx: List[int]) -> List[Dict[str, Any]]:
    """Extract formula-like lines from selected pages.

    Returns objects with {formula, latex, sources}.
    """
    out: List[Dict[str, Any]] = []
    for pno, text in zip(pages_idx, pages):
        if not text:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if len(line) < 5 or len(line) > 140:
                continue
            if _MATH_LINE_RE.search(line):
                out.append({
                    "formula": line,
                    "latex": _plain_to_latex(line),
                    "meaning": "",
                    "sources": [pno],
                })
    # Deduplicate by formula
    return _dedupe_items(out, key_field="formula")
def _merge_chunk_results(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "summaries": [],
        "key_points": [],
        "highlights": [],
        "definitions": [],
        "formulas": [],
    }

    for it in items:
        s = it.get("summary")
        if isinstance(s, str) and s.strip():
            merged["summaries"].append(s.strip())

        merged["key_points"].extend(it.get("key_points") or [])
        merged["highlights"].extend(it.get("highlights") or [])
        merged["definitions"].extend(it.get("definitions") or [])
        merged["formulas"].extend(it.get("formulas") or [])

    merged["key_points"] = _dedupe_items(merged["key_points"], key_field="text")
    merged["highlights"] = _dedupe_items(merged["highlights"], key_field="text")
    merged["definitions"] = _dedupe_items(merged["definitions"], key_field="text")

    # formulas dedupe by expression
    merged["formulas"] = _dedupe_items(merged["formulas"], key_field="formula")

    return merged


# -----------------------------
# Full PDF analysis
# -----------------------------
def _clean_text(s: str) -> str:
    """Make model/extraction text safer to display (no weird control chars, no huge repeats)."""
    if not s:
        return ""
    # remove control chars
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # collapse long repeats like '-----' or '====='
    s = re.sub(r"([=\-_*])\1{6,}", r"\1\1\1\1", s)
    # keep to a sane length per field
    return s


def _clean_items(items):
    if not isinstance(items, list):
        return items
    out=[]
    for it in items:
        if isinstance(it, str):
            t=_clean_text(it)
            if t:
                out.append(t)
        elif isinstance(it, dict):
            d=dict(it)
            if "text" in d:
                d["text"]=_clean_text(str(d.get("text","")))
            if "term" in d:
                d["term"]=_clean_text(str(d.get("term","")))
            if "definition" in d:
                d["definition"]=_clean_text(str(d.get("definition","")))
            if "formula" in d:
                d["formula"]=_clean_text(str(d.get("formula","")))
            if "meaning" in d:
                d["meaning"]=_clean_text(str(d.get("meaning","")))
            if "explanation" in d:
                d["explanation"]=_clean_text(str(d.get("explanation","")))
            out.append(d)
        else:
            t=_clean_text(str(it))
            if t:
                out.append(t)
    return out
def _process_chunk(
    client,
    chunk_pages,
    chunk_text,
    cfg,
):
    cache_key = _cache_key(chunk_text)

    if cache_key in _GPT_CACHE:
        return _GPT_CACHE[cache_key]

    prompt = CHUNK_PROMPT.replace(
        "{TEXT}",
        chunk_text[:22000]
    )

    raw = _call_gpt_json(
        client,
        prompt,
        model=DEFAULT_MODEL,
        max_tokens=cfg.chunk_max_tokens,
    )

    result = {
        "summary": str(raw.get("summary", "")).strip(),
        "key_points": _wrap_items_with_sources(
            raw.get("key_points"),
            chunk_pages,
        ),
        "highlights": _wrap_items_with_sources(
            raw.get("highlights"),
            chunk_pages,
        ),
        "definitions": _wrap_items_with_sources(
            raw.get("definitions"),
            chunk_pages,
        ),
        "formulas": _wrap_formulas_with_sources(
            raw.get("formulas"),
            chunk_pages,
        ),
    }

    _GPT_CACHE[cache_key] = result

    return result
def analyze_pdf(file_path: str, level: str = "normal", *, page: int | None = None, page_from: int | None = None, page_to: int | None = None) -> Dict[str, Any]:
    cfg = LEVELS.get((level or "").lower(), LEVELS["normal"])

    pages = _load_or_extract_pages(file_path, max_pages=cfg.max_pages)

    # -----------------------------
    # Optional page filter (single page or range)
    # -----------------------------
    total_pages = len(pages)
    selected: List[Tuple[int, str]] = []
    filter_mode = "all"
    if page is not None:
        filter_mode = "single"
        pno = int(page)
        if 1 <= pno <= total_pages:
            selected = [(pno, pages[pno - 1])]
        else:
            selected = []
    elif page_from is not None or page_to is not None:
        filter_mode = "range"
        start = int(page_from or 1)
        end = int(page_to or total_pages)
        if start < 1:
            start = 1
        if end > total_pages:
            end = total_pages
        if end < start:
            start, end = end, start
        selected = [(i, pages[i - 1]) for i in range(start, end + 1)]
    else:
        selected = [(i, t) for i, t in enumerate(pages, start=1)]

    selected_pages = [p for p, _ in selected]

    # If filtering removed everything or the PDF is empty
    if not selected:
        return {
            "meta": {"level": cfg.name, "created_at": _utc_iso(), "pages_analyzed": 0},
            "summary": "No pages selected for analysis (page filter out of range) or PDF has no extractable text.",
            "key_points": [],
            "highlights": [],
            "definitions": [],
            "important_notes": [],
            "formulas": [],
        }

    # Keep original page numbers for stable citations/sources
    selected = [(pno, (txt or "").strip()) for (pno, txt) in selected if (txt or "").strip()]

    if not selected:
        return {
            "meta": {
            "level": cfg.name,
            "created_at": _utc_iso(),
            "pages_analyzed": 0,
        },
        "summary": "PDF has no extractable text in the selected page range. If it's scanned, OCR may be needed.",
        "key_points": [],
        "highlights": [],
        "definitions": [],
        "important_notes": [],
        "formulas": [],
    }
    # Create OpenAI client
    client = _get_client()

    # Build chunks from selected pages
    chunks = _chunk_selected_pages(
        selected,
        cfg.pages_per_chunk,
        overlap=1,
    )   

    max_workers = min(
        8,
        max(
            2,
            (os.cpu_count() or 4)
        )
    )
    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        futures = {}

        for idx, (chunk_pages, chunk_text) in enumerate(chunks):

            future = executor.submit(
                _process_chunk,
                client,
                chunk_pages,
                chunk_text,
                cfg,
            )

            futures[future] = idx

        ordered_results = [None] * len(chunks)

        for future in as_completed(futures):

            idx = futures[future]

            try:
                ordered_results[idx] = future.result()
            except Exception:
                ordered_results[idx] = {
                    "summary": "",
                    "key_points": [],
                    "highlights": [],
                    "definitions": [],
                    "formulas": [],
                }

    chunk_results = ordered_results
    merged = _merge_chunk_results(chunk_results)

    merged_text = json.dumps(merged, ensure_ascii=False)
    final_raw = _call_gpt_json(
        client,
        FINAL_PROMPT.format(TEXT=merged_text[:30000]),
        model=DEFAULT_MODEL,
        max_tokens=cfg.final_max_tokens,
    )

    # Final output: keep sources from deterministic merge, but let the model refine summary/notes/formulas meanings
    summary = str(final_raw.get("summary", "")).strip()

    # important_notes: model-generated strings, attach broad sources (all analyzed pages)
    all_pages = selected_pages[:]
    important_notes = []
    if isinstance(final_raw.get("important_notes"), list):
        for x in final_raw.get("important_notes"):
            t = str(x).strip()
            if t:
                important_notes.append({"text": t, "sources": all_pages[:]} )

    # formulas: prefer merged ones (with sources) but allow model to add meaning improvements
    merged_formulas = merged.get("formulas") or []
    # if model returned formulas, we can try to update meaning by matching expressions
    if isinstance(final_raw.get("formulas"), list):
        by_expr = {re.sub(r"\s+", " ", str(f.get("formula", "")).strip()).lower(): f for f in merged_formulas if isinstance(f, dict)}
        for f in final_raw.get("formulas"):
            if not isinstance(f, dict):
                continue
            expr = str(f.get("formula","")).strip()
            if not expr:
                continue
            k = re.sub(r"\s+", " ", expr).lower()
            if k in by_expr:
                meaning = str(f.get("meaning","")).strip()
                if meaning:
                    by_expr[k]["meaning"] = meaning
            else:
                merged_formulas.append({
                "formula": expr,
                "latex": str(f.get("latex", "")).strip(),
                "meaning": str(f.get("meaning", "")).strip(),
                "variables": f.get("variables", []),
                "sources": all_pages[:],
                })
    merged_formulas = _dedupe_items(merged_formulas, key_field="formula")

    # Ensure LaTeX field for nicer rendering on the frontend
    for f in merged_formulas:
        if isinstance(f, dict):
            if not f.get("latex"):
                f["latex"] = _plain_to_latex(str(f.get("formula") or ""))

    # If no formulas were found (common in scanned PDFs), try heuristic extraction
    if not merged_formulas:
        try:
            merged_formulas = _extract_formula_candidates([t for _, t in selected], selected_pages)[: cfg.formulas_cap]
        except Exception:
            merged_formulas = []

    # Caps based on config
    key_points = (merged.get("key_points") or [])[: cfg.key_points_cap]
    highlights = (merged.get("highlights") or [])[: cfg.highlights_cap]
    definitions = (merged.get("definitions") or [])[: cfg.definitions_cap]
    merged_formulas = merged_formulas[: cfg.formulas_cap]

    result = {
        "meta": {
            "level": cfg.name,
            "created_at": _utc_iso(),
            "pages_analyzed": len(selected),
            "page_filter": {"mode": filter_mode, "pages": selected_pages},
        },
        "summary": summary,
        "key_points": key_points,
        "highlights": highlights,
        "definitions": definitions,
        "important_notes": important_notes,
        "formulas": merged_formulas,
    }


    # Clean for display (avoid unreadable artifacts)
    result["summary"] = _clean_text(str(result.get("summary","")))
    for k in ["key_points", "highlights", "definitions", "important_notes", "formulas"]:
        result[k] = _clean_items(result.get(k))
    return result