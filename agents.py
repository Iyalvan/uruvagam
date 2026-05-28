"""
uruvagam.agents
~~~~~~~~~~~~~~~~
Quality agents that run after initial content generation.
Each agent is a pure function: (content: dict, ...) -> dict or (dict, dict).
All agents call the same local oMLX server as content_gen.

Agents run in order:
  1. ContentCriticAgent  — evaluate quality, save report, warn if score < 7
  2. SpeakerStyleAgent   — rewrite speaker_notes to match presenter's natural style
  3. DurationBudgetAgent — condense over-budget slides to fit F5-TTS single-chunk window
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import httpx


# ── constants ────────────────────────────────────────────────────────────────

WORDS_PER_SECOND = 0.4          # speech rate — mirrors tts.py estimate
BUDGET_THRESHOLD_WORDS = 90     # flag slides with notes > this (≈36s, forces 2+ TTS chunks)
BUDGET_TARGET_WORDS = 72        # condense to this (≈29s, fits in one chunk comfortably)


# ── shared LLM call ──────────────────────────────────────────────────────────

def _call_omlx(prompt: str, system: str, omlx_config: dict) -> str:
    key = omlx_config.get("api_key") or os.environ.get("OMLX_API_KEY")
    if not key:
        raise ValueError("Set OMLX_API_KEY env var")
    base_url = omlx_config.get("base_url", "http://127.0.0.1:8000")
    model = omlx_config.get("model", "Qwen3.6-35B-A3B-4bit")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        # lower temperature: we want precise edits, not creative generation
        "temperature": 0.3,
        "max_tokens": 8192,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = httpx.post(
        f"{base_url}/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=300.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_ollama(prompt: str, system: str, llm_config: dict) -> str:
    base_url = llm_config.get("base_url", "http://localhost:11434")
    model = llm_config.get("model", "llama3.2")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    r = httpx.post(f"{base_url}/v1/chat/completions", json=payload, timeout=300.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _dispatch_llm(prompt: str, system: str, llm_config: dict) -> str:
    provider = llm_config.get("provider", "omlx")
    if provider == "ollama":
        return _call_ollama(prompt, system, llm_config)
    return _call_omlx(prompt, system, llm_config)


def _parse_json(raw: str):
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    # LLMs often produce trailing commas — strip them before parsing
    text = re.sub(r",\s*([\]\}])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[\{].*[\]\}]", text, re.DOTALL)
        if match:
            cleaned = re.sub(r",\s*([\]\}])", r"\1", match.group())
            return json.loads(cleaned)
        raise ValueError(f"could not parse JSON from LLM response:\n{text[:400]}")


def _parse_slide_text(raw: str) -> dict:
    """Parse SLIDE:N delimiter format into {slide_number: text} dict.
    More reliable than JSON for long spoken-text values."""
    result = {}
    # split on SLIDE:<num> markers; re.split with capturing group interleaves num and text
    parts = re.split(r"SLIDE:(\d+)\s*\n", raw.strip())
    # parts = [prefix, num1, text1, num2, text2, ...]
    i = 1
    while i + 1 < len(parts):
        try:
            num = int(parts[i])
            text = parts[i + 1].strip().strip("---").strip()
            if text:
                result[num] = text
        except (ValueError, IndexError):
            pass
        i += 2
    return result


def _stitch_script(content: dict) -> str:
    parts = [f"Welcome everyone. Today we're covering: {content.get('title', '')}.\n"]
    for slide in content.get("slides", []):
        notes = slide.get("speaker_notes", "").strip()
        if notes:
            parts.append(notes)
    return "\n\n".join(parts)


# ── ContentCriticAgent ────────────────────────────────────────────────────────

_CRITIC_SYSTEM = """You are a training curriculum quality reviewer.
Evaluate the presentation against the rubric and return ONLY valid JSON:

{
  "score": <integer 0-10>,
  "pass": <true if score >= 7, else false>,
  "issues": [
    {
      "slide": <slide number as integer, or 0 for overall>,
      "type": "<coverage|depth|clarity|length|style>",
      "detail": "<specific, actionable description>"
    }
  ]
}

Rubric (each criterion is worth 2 points):
1. Coverage    — do the content slides collectively address every stated learning objective?
2. Depth       — is the technical depth calibrated to the stated audience (not too basic, not too dense)?
3. Clarity     — are bullets concise (< 12 words), start with action verbs where possible?
4. Length      — do speaker_notes feel natural for 1.5-3 minutes per slide when read aloud?
5. Style       — are speaker_notes conversational? (use "you"/"we", include examples, avoid jargon walls)

Be specific. Vague issues ("could be better") are not useful. Cite slide numbers and quote problematic text."""


def run_content_critic(
    content: dict,
    audience: str,
    llm_config: dict,
) -> tuple[dict, dict]:
    """Evaluate content quality. Returns (content unchanged, quality_report).
    Does not regenerate — validation only. Logs a warning if score < 7."""

    objectives = content.get("objectives", [])
    slides = content.get("slides", [])

    slides_data = json.dumps(
        [
            {
                "slide": i + 1,
                "title": s.get("title", ""),
                "bullets": s.get("bullets", []),
                "speaker_notes": s.get("speaker_notes", ""),
            }
            for i, s in enumerate(slides)
        ],
        indent=2,
    )

    prompt = (
        f"AUDIENCE: {audience}\n\n"
        f"LEARNING OBJECTIVES:\n"
        + "\n".join(f"  - {o}" for o in objectives)
        + f"\n\nSLIDES:\n{slides_data}"
    )

    try:
        raw = _dispatch_llm(prompt, _CRITIC_SYSTEM, llm_config)
        report = _parse_json(raw)
        report.setdefault("score", 0)
        report.setdefault("pass", False)
        report.setdefault("issues", [])
        score = report["score"]
        n_issues = len(report["issues"])
        flag = "  [WARNING] score below threshold — review quality_report.json" if not report["pass"] else ""
        print(f"  ContentCriticAgent: score={score}/10  issues={n_issues}{flag}")
    except Exception as e:
        print(f"  ContentCriticAgent: failed ({e}) — skipping")
        report = {"score": -1, "pass": True, "issues": [], "error": str(e)}

    return content, report


# ── SpeakerStyleAgent ─────────────────────────────────────────────────────────

_STYLE_SYSTEM = """You are a voice and style adapter for training presentation scripts.
Rewrite speaker_notes to match the presenter's natural speaking style.

Strict rules:
- Do NOT change facts, technical terms, or the concepts being explained
- Do NOT touch bullet points — only speaker_notes
- Match vocabulary, sentence rhythm, and directness from the style examples
- Keep the same approximate length (within ±20% of original word count)
- Do NOT add or remove transitions like "[Next slide]"

Output format — use EXACTLY this, no JSON, no markdown:
SLIDE:1
<rewritten notes for slide 1>
SLIDE:2
<rewritten notes for slide 2>
...one block per slide, in order."""


def run_speaker_style(
    content: dict,
    style_path: str,
    llm_config: dict,
) -> dict:
    """Rewrite all speaker_notes to match the presenter's natural speaking style.
    Skipped gracefully if style_path doesn't exist or is empty."""

    try:
        style_text = Path(style_path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        print(f"  SpeakerStyleAgent: {style_path} not found — skipping (create it to enable style matching)")
        return content

    if not style_text or style_text.startswith("# "):
        print("  SpeakerStyleAgent: style file is a template placeholder — skipping")
        return content

    slides = content.get("slides", [])
    slides_notes = [
        {"slide": i + 1, "speaker_notes": s.get("speaker_notes", "")}
        for i, s in enumerate(slides)
        if s.get("speaker_notes", "").strip()
    ]

    if not slides_notes:
        print("  SpeakerStyleAgent: no speaker_notes to rewrite — skipping")
        return content

    prompt = (
        f"PRESENTER'S NATURAL SPEAKING STYLE (examples — match this):\n\n{style_text}\n\n"
        f"SPEAKER NOTES TO REWRITE:\n{json.dumps(slides_notes, indent=2)}"
    )

    try:
        raw = _dispatch_llm(prompt, _STYLE_SYSTEM, llm_config)
        rewrite_map = _parse_slide_text(raw)

        if not rewrite_map:
            raise ValueError("no SLIDE:N blocks found in response")

        for i, slide in enumerate(slides):
            key = i + 1
            if key in rewrite_map:
                slide["speaker_notes"] = rewrite_map[key]

        content["full_script"] = _stitch_script(content)
        print(f"  SpeakerStyleAgent: rewrote speaker_notes for {len(rewrite_map)} slides")
    except Exception as e:
        print(f"  SpeakerStyleAgent: failed ({e}) — skipping, original notes preserved")

    return content


# ── DurationBudgetAgent ───────────────────────────────────────────────────────

_BUDGET_SYSTEM = """You are a TTS script optimiser.
Some speaker_notes are too long to generate in a single TTS pass (they require chunking, which can cause prosody gaps at split points).
Condense the given slides to fit within the target word count.

Rules:
- Keep ALL key technical points and concepts — do not drop anything important
- Remove repetition, filler phrases ("let's take a look at", "as you can see"), and over-explanation
- Write naturally — it should still sound like a complete, spoken explanation
- Match the target_words count as closely as possible

Output format — use EXACTLY this, no JSON, no markdown:
SLIDE:<number>
<condensed notes>
...one block per over-budget slide."""


def run_duration_budget(content: dict, llm_config: dict) -> dict:
    """Condense speaker_notes on slides that exceed the single-chunk TTS threshold."""

    slides = content.get("slides", [])
    over_budget = []

    for i, slide in enumerate(slides, 1):
        notes = slide.get("speaker_notes", "")
        word_count = len(notes.split())
        if word_count > BUDGET_THRESHOLD_WORDS:
            over_budget.append(
                {
                    "slide": i,
                    "speaker_notes": notes,
                    "current_words": word_count,
                    "target_words": BUDGET_TARGET_WORDS,
                }
            )

    if not over_budget:
        print("  DurationBudgetAgent: all slides within budget — skipping")
        return content

    slide_nums = [s["slide"] for s in over_budget]
    prompt = (
        f"TARGET: ~{BUDGET_TARGET_WORDS} words per slide (~{BUDGET_TARGET_WORDS * WORDS_PER_SECOND:.0f}s of speech).\n\n"
        f"OVER-BUDGET SLIDES:\n{json.dumps(over_budget, indent=2)}"
    )

    try:
        raw = _dispatch_llm(prompt, _BUDGET_SYSTEM, llm_config)
        condense_map = _parse_slide_text(raw)

        if not condense_map:
            raise ValueError("no SLIDE:N blocks found in response")

        for i, slide in enumerate(slides):
            key = i + 1
            if key in condense_map:
                slide["speaker_notes"] = condense_map[key]

        content["full_script"] = _stitch_script(content)
        print(
            f"  DurationBudgetAgent: condensed slides {slide_nums} "
            f"(was >{BUDGET_THRESHOLD_WORDS}w, target {BUDGET_TARGET_WORDS}w)"
        )
    except Exception as e:
        print(f"  DurationBudgetAgent: failed ({e}) — skipping, original notes preserved")

    return content
