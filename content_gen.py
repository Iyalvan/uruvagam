"""
uruvagam.content_gen
~~~~~~~~~~~~~~~~~~~~~
Generates structured training content from a topic using Ollama or Claude API.
Returns a validated JSON payload used by slide_gen and tts_gen downstream.
"""

import json
import os
import re
# changed: requests is not in deps; httpx is already installed via anthropic
import httpx
from typing import Optional

SYSTEM_PROMPT = """You are an expert corporate trainer and instructional designer with 15+ years of experience.
Create high-quality, structured training content that is clear, engaging, and immediately actionable.

You MUST output ONLY valid JSON — no markdown fences, no preamble, no explanation. 
Match this schema exactly:

{
  "title": "string — concise presentation title",
  "duration_minutes": number,
  "objectives": ["string (start with an action verb: Understand, Apply, Configure...)", ...],
  "slides": [
    {
      "slide_type": "title | objectives | content | demo | summary",
      "title": "string — slide heading",
      "subtitle": "string — optional one-liner below title",
      "bullets": ["string — concise, actionable point", ...],
      "speaker_notes": "string — natural conversational narration for this slide, 3-5 sentences. Write exactly what the presenter would say."
    }
  ],
  "full_script": "string — complete narration script stitched together, reading naturally as spoken word. Include transitions between slides."
}

Slide structure rules:
- Slide 1: type=title — just title + subtitle + speaker_notes (no bullets)  
- Slide 2: type=objectives — bullets are the learning objectives
- Slides 3 to N-1: type=content — each covers one focused concept, 3-5 bullets
- Last slide: type=summary — recap + clear call-to-action bullets
- Total slides: aim for (duration_minutes / 1.5) rounded to nearest integer, min 6

Quality rules:
- Bullets: start with action verbs where possible, max 12 words each
- Speaker notes: conversational, use "you" and "we", include examples, avoid jargon
- Full script: natural spoken English, include slide transition cues like "[Next slide]"
"""

# prepended to SYSTEM_PROMPT when restructuring user-provided source notes
SOURCE_PREAMBLE = """RESTRUCTURING MODE: You are organising a speaker's raw training notes into a polished presentation.
Do NOT invent content. Your job is to structure, group, and polish what the speaker provided.
Maintain the speaker's terminology, examples, and emphasis. Where the notes are sparse, expand naturally in the speaker_notes field — but do not introduce new facts or topics that are not in the source.

"""


def generate_content(
    topic: str,
    duration_minutes: int = 10,
    audience: str = "engineering team",
    # changed: default provider/model/url for local oMLX HTTP server
    provider: str = "omlx",
    model: str = "Qwen3.6-35B-A3B-4bit",
    omlx_base_url: str = "http://127.0.0.1:8000",
    omlx_api_key: Optional[str] = None,
    ollama_base_url: str = "http://localhost:11434",
    api_key: Optional[str] = None,
    # added: when provided, the LLM restructures these notes instead of inventing from scratch
    source_content: Optional[str] = None,
) -> dict:
    """Generate structured training content for a given topic (or restructure raw notes if source_content is given)."""

    slides_count = max(6, round(duration_minutes / 1.5))

    if source_content:
        system = SOURCE_PREAMBLE + SYSTEM_PROMPT
        user_prompt = f"""Restructure these raw training notes into a {duration_minutes}-minute presentation.

TITLE: {topic}
AUDIENCE: {audience}
TARGET SLIDE COUNT: {slides_count}

=== SOURCE NOTES (use these directly, do not invent new content) ===
{source_content}
=== END SOURCE NOTES ===

Group these notes into focused slides. Use the speaker's vocabulary and examples."""
    else:
        system = SYSTEM_PROMPT
        user_prompt = f"""Create a {duration_minutes}-minute training presentation on this topic:

TOPIC: {topic}
AUDIENCE: {audience}
TARGET SLIDE COUNT: {slides_count}

Make it practical, technically accurate, and immediately usable for the audience.
Speaker notes should sound natural when read aloud — not robotic."""

    if provider == "omlx":
        raw = _call_omlx(user_prompt, system, model, omlx_base_url, omlx_api_key)
    elif provider == "ollama":
        raw = _call_ollama(user_prompt, system, model, ollama_base_url)
    elif provider == "claude":
        raw = _call_claude(user_prompt, system, model, api_key)
    else:
        raise ValueError(f"Unknown provider: {provider}. Use 'omlx', 'ollama', or 'claude'.")

    return _parse_and_validate(raw, topic, duration_minutes)


# ── Provider calls ─────────────────────────────────────────────────────────────

# replaces _call_ollama: targets local oMLX HTTP server (OpenAI-compatible)
def _call_omlx(prompt: str, system: str, model: str, base_url: str, api_key: Optional[str]) -> str:
    key = api_key or os.environ.get("OMLX_API_KEY")
    if not key:
        raise ValueError("Set OMLX_API_KEY env var or pass omlx_api_key")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        # disable Qwen3.6 thinking mode — it leaks chain-of-thought into the output
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = httpx.post(f"{base_url}/v1/chat/completions", json=payload, headers=headers, timeout=300.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_ollama(prompt: str, system: str, model: str, base_url: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        # no chat_template_kwargs — ollama ignores it and some models reject it
    }
    # ollama has no auth by default
    r = httpx.post(f"{base_url}/v1/chat/completions", json=payload, timeout=300.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_claude(prompt: str, system: str, model: str, api_key: Optional[str]) -> str:
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("Set ANTHROPIC_API_KEY env var or pass --api-key")

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model or "claude-haiku-4-5",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── Parsing & validation ───────────────────────────────────────────────────────

def _parse_and_validate(raw: str, topic: str, duration: int) -> dict:
    """Clean, parse, and apply fallbacks to raw LLM JSON output."""
    # Strip markdown fences if present
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to extract a JSON object from the string
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"LLM did not return valid JSON: {e}\n\nRaw output:\n{text[:500]}")

    # Apply fallbacks for missing top-level fields
    data.setdefault("title", topic)
    data.setdefault("duration_minutes", duration)
    data.setdefault("objectives", [])
    data.setdefault("slides", [])
    data.setdefault("full_script", _stitch_script(data))

    # Ensure every slide has required keys
    for slide in data["slides"]:
        slide.setdefault("slide_type", "content")
        slide.setdefault("title", "")
        slide.setdefault("subtitle", "")
        slide.setdefault("bullets", [])
        slide.setdefault("speaker_notes", "")

    return data


def _stitch_script(data: dict) -> str:
    """Fallback: stitch speaker notes from slides into a full script."""
    parts = [f"Welcome everyone. Today we're covering: {data.get('title', '')}.\n"]
    for slide in data.get("slides", []):
        notes = slide.get("speaker_notes", "").strip()
        if notes:
            parts.append(notes)
    return "\n\n".join(parts)
