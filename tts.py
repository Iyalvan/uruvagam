#!/usr/bin/env python3
"""
uruvagam — tts.py
------------------
Voice generation from narration script.
Supports: F5-TTS (voice cloning), Qwen3-TTS via oMLX, Kokoro TTS, ElevenLabs (cloud)

Usage:
  python tts.py outputs/my_topic_20240101/          # uses content.json in dir
  python tts.py outputs/my_topic_20240101/content.json
  python tts.py outputs/my_topic/ --engine qwen3tts-omlx --preview
  python tts.py outputs/my_topic/ --engine kokoro
  python tts.py outputs/my_topic/ --engine elevenlabs --api-key YOUR_KEY
"""

import argparse
import base64
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf


F5_SAMPLE_RATE = 24_000
F5_MAX_TOTAL_SECONDS = 43.0
F5_CHUNK_TARGET_SECONDS = 24.0
F5_CHUNK_SILENCE_SECONDS = 0.18
F5_SLIDE_SILENCE_SECONDS = 0.65
QWEN3TTS_MODEL = "Qwen3-TTS-12Hz-1.7B-Base-bf16"
QWEN3TTS_BASE_URL = "http://127.0.0.1:8000"
QWEN3TTS_TARGET_RMS_DBFS = -19.5
QWEN3TTS_PEAK_LIMIT_DBFS = -2.0
QWEN3TTS_INSTRUCTIONS = (
    "Clone this speaker's voice as closely as possible. "
    "Match the speaker's pace, pitch, and natural delivery from the reference audio. "
    "Maintain consistent voice throughout. "
    "Do not add dramatic emphasis or theatrical energy."
)


def main():
    parser = argparse.ArgumentParser(description="uruvagam TTS generator")
    parser.add_argument("input", help="Path to content.json or its parent directory")
    parser.add_argument("--engine", choices=["f5tts", "qwen3tts-omlx", "kokoro", "elevenlabs"],
                        default="f5tts", help="TTS engine (default: f5tts)")
    parser.add_argument("--voice-ref", default="assets/voice_reference.wav",
                        help="Reference audio for voice cloning (f5tts/qwen3tts-omlx/elevenlabs)")
    # added: paired transcript for F5-TTS (required for quality voice cloning)
    parser.add_argument("--voice-ref-text", default="assets/voice_reference.txt",
                        help="Path to a text file with the exact transcript of the reference audio")
    parser.add_argument("--output-dir", default=None,
                        help="Audio output directory (default: RUN/audio, or RUN/audio_qwen3tts for qwen3tts-omlx)")
    # added: quick iteration mode — only generates slides 1-2 audio
    parser.add_argument("--preview", action="store_true",
                        help="Only generate first 2 slides' audio (fast quality check)")
    parser.add_argument("--slide", type=int, default=None,
                        help="Generate audio for one slide only (1-indexed)")
    parser.add_argument("--api-key", default=None,
                        help="ElevenLabs API key (or set ELEVENLABS_API_KEY)")
    parser.add_argument("--voice-id", default=None,
                        help="ElevenLabs voice ID (after first clone)")
    parser.add_argument("--omlx-base-url", default=os.environ.get("OMLX_BASE_URL", QWEN3TTS_BASE_URL),
                        help="oMLX base URL for qwen3tts-omlx")
    parser.add_argument("--qwen-model", default=os.environ.get("QWEN3TTS_OMLX_MODEL", QWEN3TTS_MODEL),
                        help="oMLX model id for qwen3tts-omlx")
    parser.add_argument("--qwen-voice", default=os.environ.get("QWEN3TTS_VOICE", ""),
                        help="Optional speaker name for Qwen CustomVoice models. Leave empty for Base clone models.")
    parser.add_argument("--qwen-instructions", default=os.environ.get("QWEN3TTS_INSTRUCTIONS", QWEN3TTS_INSTRUCTIONS),
                        help="Style instructions for qwen3tts-omlx")
    parser.add_argument("--qwen-speed", type=float, default=1.0,
                        help="Speech speed hint for qwen3tts-omlx. Some oMLX models may ignore it.")
    parser.add_argument("--no-normalize", action="store_true",
                        help="Do not RMS-normalize qwen3tts-omlx output")
    args = parser.parse_args()

    # Resolve content.json path
    p = Path(args.input)
    content_path = p / "content.json" if p.is_dir() else p
    if not content_path.exists():
        print(f"ERROR: {content_path} not found")
        sys.exit(1)

    with open(content_path) as f:
        content = json.load(f)

    if args.output_dir:
        out_dir = Path(args.output_dir)
    elif args.engine == "qwen3tts-omlx":
        out_dir = content_path.parent / "audio_qwen3tts"
    else:
        out_dir = content_path.parent / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"TTS engine: {args.engine}")
    print(f"Output dir: {out_dir}")

    if args.engine == "f5tts":
        _run_f5tts(content, out_dir, args.voice_ref, args.voice_ref_text, args.preview, args.slide)
    elif args.engine == "qwen3tts-omlx":
        _run_qwen3tts_omlx(
            content,
            out_dir,
            voice_ref=args.voice_ref,
            voice_ref_text_path=args.voice_ref_text,
            preview=args.preview,
            slide=args.slide,
            base_url=args.omlx_base_url,
            model=args.qwen_model,
            voice=args.qwen_voice.strip() or None,
            instructions=args.qwen_instructions,
            speed=args.qwen_speed,
            normalize=not args.no_normalize,
        )
    elif args.engine == "kokoro":
        _run_kokoro(content, out_dir)
    elif args.engine == "elevenlabs":
        _run_elevenlabs(content, out_dir, args.api_key, args.voice_id)


# 150 wpm of natural speech ≈ 0.4s per word.
def _estimate_speech_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return max(2.5, words * 0.4)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;:])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _chunk_text_for_f5(text: str, max_chunk_seconds: float = F5_CHUNK_TARGET_SECONDS) -> list[str]:
    """Group sentences so each F5 call stays under the model's max duration window."""
    chunks: list[str] = []
    current: list[str] = []
    current_seconds = 0.0

    for sentence in _split_sentences(text):
        sentence_seconds = _estimate_speech_duration(sentence)

        if current and current_seconds + sentence_seconds > max_chunk_seconds:
            chunks.append(" ".join(current))
            current = [sentence]
            current_seconds = sentence_seconds
        else:
            current.append(sentence)
            current_seconds += sentence_seconds

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]


def _audio_duration(path: str | Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sample_rate


def _write_wav(path: str | Path, audio: np.ndarray, sample_rate: int):
    sf.write(str(path), audio, sample_rate)


def _silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(int(seconds * sample_rate), dtype=np.float32)


def _concat_wavs(paths: list[Path], output_path: Path, gap_seconds: float):
    if not paths:
        return

    chunks: list[np.ndarray] = []
    sample_rate: int | None = None
    for path in paths:
        audio, sr = _read_wav(path)
        if sample_rate is None:
            sample_rate = sr
        elif sr != sample_rate:
            raise ValueError(f"Sample rate mismatch while concatenating {path}: {sr} != {sample_rate}")
        chunks.append(audio)
        chunks.append(_silence(gap_seconds, sr))

    if chunks:
        chunks = chunks[:-1]
    _write_wav(output_path, np.concatenate(chunks), sample_rate or F5_SAMPLE_RATE)


def _slide_pairs(content: dict, preview: bool, slide: int | None) -> list[tuple[int, dict]]:
    all_slides = content.get("slides", [])
    if slide is not None:
        if slide < 1 or slide > len(all_slides):
            print(f"ERROR: --slide {slide} out of range (deck has {len(all_slides)} slides)")
            return []
        print(f"SINGLE SLIDE MODE: generating slide {slide} only\n")
        return [(slide, all_slides[slide - 1])]
    if preview:
        pairs = list(enumerate(all_slides, 1))[:2]
        print(f"PREVIEW MODE: generating only the first {len(pairs)} slides\n")
        return pairs
    return list(enumerate(all_slides, 1))


def _run_f5tts(content: dict, out_dir: Path, voice_ref: str, voice_ref_text_path: str, preview: bool, slide: int | None = None):
    """
    F5-TTS MLX voice cloning. Apple Silicon optimised.
    """
    # changed: the high-level API is f5_tts_mlx.generate.generate(), NOT F5TTS().generate()
    try:
        from f5_tts_mlx.generate import generate as f5_generate
    except ImportError:
        print("F5-TTS MLX not installed. Run: pip install f5-tts-mlx")
        _show_setup_hint("f5tts")
        return

    if not Path(voice_ref).exists():
        print(f"ERROR: voice reference not found: {voice_ref}")
        return

    # ref_audio_text is required for voice cloning quality — F5 aligns voice characteristics to it
    ref_text_path = Path(voice_ref_text_path)
    if not ref_text_path.exists():
        print(f"ERROR: voice reference transcript not found: {ref_text_path}")
        print("Create it with the EXACT words spoken in the reference audio.")
        return
    ref_audio_text = ref_text_path.read_text(encoding="utf-8").strip()
    ref_duration = _audio_duration(voice_ref)

    slide_pairs = _slide_pairs(content, preview, slide)

    generated_slide_paths: list[Path] = []

    for i, slide in slide_pairs:
        notes = slide.get("speaker_notes", "").strip()
        if not notes:
            continue
        out_file = out_dir / f"slide_{i:02d}.wav"
        target_duration = _estimate_speech_duration(notes)
        chunks = _chunk_text_for_f5(notes)
        print(f"  [{i:02d}] {slide.get('title', '')}  (~{target_duration:.0f}s target, {len(chunks)} chunk(s))")

        chunk_paths: list[Path] = []
        with tempfile.TemporaryDirectory(prefix=f"uruvagam_slide_{i:02d}_") as tmp:
            tmp_dir = Path(tmp)
            for chunk_index, chunk_text in enumerate(chunks, 1):
                chunk_target = _estimate_speech_duration(chunk_text)
                total_duration = min(F5_MAX_TOTAL_SECONDS, ref_duration + chunk_target)
                chunk_file = tmp_dir / f"chunk_{chunk_index:02d}.wav"
                print(f"       chunk {chunk_index}/{len(chunks)} (~{chunk_target:.0f}s target)")
                f5_generate(
                    generation_text=chunk_text,
                    ref_audio_path=voice_ref,
                    ref_audio_text=ref_audio_text,
                    duration=total_duration,
                    output_path=str(chunk_file),
                )
                chunk_paths.append(chunk_file)

            if len(chunk_paths) == 1:
                audio, sample_rate = _read_wav(chunk_paths[0])
                _write_wav(out_file, audio, sample_rate)
            else:
                _concat_wavs(chunk_paths, out_file, F5_CHUNK_SILENCE_SECONDS)

        generated_slide_paths.append(out_file)
        print(f"       saved → {out_file}")

    # Build the full narration only when all slides were processed.
    if not preview and slide is None and generated_slide_paths:
        full_out = out_dir / "full_narration.wav"
        print("\n  assembling full narration from slide audio...")
        _concat_wavs(generated_slide_paths, full_out, F5_SLIDE_SILENCE_SECONDS)
        print(f"       saved → {full_out}")


def _run_qwen3tts_omlx(
    content: dict,
    out_dir: Path,
    voice_ref: str,
    voice_ref_text_path: str,
    preview: bool,
    slide: int | None,
    base_url: str,
    model: str,
    voice: str | None,
    instructions: str,
    speed: float,
    normalize: bool,
):
    """
    Qwen3-TTS through the local oMLX OpenAI-compatible audio endpoint.

    This is opt-in and writes to audio_qwen3tts/ by default so existing F5
    audio stays untouched.
    """
    api_key = os.environ.get("OMLX_API_KEY")
    if not api_key:
        print("ERROR: OMLX_API_KEY is not set")
        return

    ref_audio_path = Path(voice_ref)
    ref_text_path = Path(voice_ref_text_path)
    if not ref_audio_path.exists():
        print(f"ERROR: voice reference not found: {ref_audio_path}")
        return
    if not ref_text_path.exists():
        print(f"ERROR: voice reference transcript not found: {ref_text_path}")
        return

    ref_audio_b64 = base64.b64encode(ref_audio_path.read_bytes()).decode("ascii")
    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    slide_pairs = _slide_pairs(content, preview, slide)
    generated_slide_paths: list[Path] = []
    report_rows: list[dict] = []

    print(f"Qwen model: {model}")
    print(f"Qwen voice: {voice or '(omitted; Base clone mode)'}")
    print(f"Normalize: {'yes' if normalize else 'no'}")

    for i, slide_data in slide_pairs:
        notes = slide_data.get("speaker_notes", "").strip()
        if not notes:
            continue

        out_file = out_dir / f"slide_{i:02d}.wav"
        # chunk long notes so oMLX doesn't hit its internal token/duration cap
        chunks = _chunk_text_for_f5(notes, max_chunk_seconds=60.0)
        print(f"  [{i:02d}] {slide_data.get('title', '')}  ({len(chunks)} chunk(s))")
        started = time.time()

        chunk_audios: list[np.ndarray] = []
        sample_rate = None
        for chunk_index, chunk_text in enumerate(chunks, 1):
            if len(chunks) > 1:
                print(f"       chunk {chunk_index}/{len(chunks)}")
            audio_bytes = _call_omlx_speech(
                base_url=base_url,
                api_key=api_key,
                model=model,
                text=chunk_text,
                voice=voice,
                instructions=instructions,
                ref_audio_b64=ref_audio_b64,
                ref_text=ref_text,
                speed=speed,
            )
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                tmp.write(audio_bytes)
                tmp.flush()
                chunk_audio, sample_rate = _read_wav(tmp.name)
            chunk_audios.append(chunk_audio)

        elapsed = time.time() - started

        # join chunks with a short silence gap between them
        if len(chunk_audios) == 1:
            audio = chunk_audios[0]
        else:
            silence = _silence(F5_CHUNK_SILENCE_SECONDS, sample_rate)
            parts = []
            for idx, ca in enumerate(chunk_audios):
                parts.append(ca)
                if idx < len(chunk_audios) - 1:
                    parts.append(silence)
            audio = np.concatenate(parts)

        if normalize:
            audio = _normalize_audio(audio, QWEN3TTS_TARGET_RMS_DBFS, QWEN3TTS_PEAK_LIMIT_DBFS)

        _write_wav(out_file, audio, sample_rate)
        generated_slide_paths.append(out_file)
        metrics = _audio_metrics(out_file)
        report_rows.append(
            {
                "slide": i,
                "title": slide_data.get("title", ""),
                "words": len(notes.split()),
                "path": str(out_file),
                "generation_seconds": round(elapsed, 3),
                **metrics,
            }
        )
        print(
            f"       saved → {out_file} "
            f"({metrics['duration_seconds']:.3f}s, rms {metrics['rms_dbfs']:.2f} dBFS, elapsed {elapsed:.1f}s)"
        )

    if not preview and slide is None and generated_slide_paths:
        full_out = out_dir / "full_narration.wav"
        print("\n  assembling full narration from slide audio...")
        _concat_wavs(generated_slide_paths, full_out, F5_SLIDE_SILENCE_SECONDS)
        print(f"       saved → {full_out}")

    report = {
        "engine": "qwen3tts-omlx",
        "model": model,
        "base_url": base_url,
        "voice": voice,
        "instructions": instructions,
        "speed": speed,
        "normalized": normalize,
        "target_rms_dbfs": QWEN3TTS_TARGET_RMS_DBFS if normalize else None,
        "peak_limit_dbfs": QWEN3TTS_PEAK_LIMIT_DBFS if normalize else None,
        "slides": report_rows,
    }
    (out_dir / "qwen3tts_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def _call_omlx_speech(
    *,
    base_url: str,
    api_key: str,
    model: str,
    text: str,
    voice: str | None,
    instructions: str,
    ref_audio_b64: str,
    ref_text: str,
    speed: float,
) -> bytes:
    payload: dict[str, object] = {
        "model": model,
        "input": text,
        "instructions": instructions,
        "response_format": "wav",
        "ref_audio": ref_audio_b64,
        "ref_text": ref_text,
        "speed": speed,
    }
    if voice:
        payload["voice"] = voice

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"ERROR: oMLX speech request failed: HTTP {error.code} {error.reason}\n{body}"
        ) from error


def _normalize_audio(audio: np.ndarray, target_rms_dbfs: float, peak_limit_dbfs: float) -> np.ndarray:
    if len(audio) == 0:
        return audio

    rms = float(np.sqrt(np.mean(audio * audio)))
    if rms <= 0:
        return audio

    target_rms = 10 ** (target_rms_dbfs / 20.0)
    peak_limit = 10 ** (peak_limit_dbfs / 20.0)
    gain = target_rms / rms
    peak = float(np.max(np.abs(audio)))
    if peak * gain > peak_limit:
        gain = peak_limit / peak
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _audio_metrics(path: Path) -> dict:
    info = sf.info(str(path))
    audio, sample_rate = _read_wav(path)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
    return {
        "sample_rate": sample_rate,
        "duration_seconds": round(info.frames / info.samplerate, 3),
        "rms_dbfs": round(_dbfs(rms), 2),
        "peak_dbfs": round(_dbfs(peak), 2),
    }


def _dbfs(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value)


def _run_kokoro(content: dict, out_dir: Path):
    """
    Kokoro TTS — fast 82M param model, no voice cloning but very natural.
    Install: pip install kokoro-onnx soundfile

    Docs: https://github.com/hexgrad/kokoro
    """
    try:
        from kokoro_onnx import Kokoro  # type: ignore
        import soundfile as sf
        import numpy as np
    except ImportError:
        print("\n=== Kokoro TTS not installed ===")
        print("Install with:  pip install kokoro-onnx soundfile")
        _show_setup_hint("kokoro")
        return

    kokoro = Kokoro("kokoro-v0_19.onnx", "voices.json")

    slides = content.get("slides", [])
    for i, slide in enumerate(slides):
        notes = slide.get("speaker_notes", "").strip()
        if not notes:
            continue
        out_file = out_dir / f"slide_{i+1:02d}.wav"
        print(f"  Slide {i+1}: {slide.get('title', '')}")
        samples, sample_rate = kokoro.create(notes, voice="af_sarah", speed=1.0, lang="en-us")
        sf.write(str(out_file), samples, sample_rate)
        print(f"  ✓ {out_file}")


def _run_elevenlabs(content: dict, out_dir: Path, api_key: str, voice_id: str):
    """
    ElevenLabs cloud TTS — highest quality voice cloning.
    Install: pip install elevenlabs

    First use: create a voice clone at https://elevenlabs.io
    then pass --voice-id YOUR_VOICE_ID
    """
    try:
        from elevenlabs.client import ElevenLabs  # type: ignore
        from elevenlabs import save  # type: ignore
    except ImportError:
        print("\n=== ElevenLabs SDK not installed ===")
        print("Install with:  pip install elevenlabs")
        _show_setup_hint("elevenlabs")
        return

    key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ERROR: Set ELEVENLABS_API_KEY or pass --api-key")
        return

    client = ElevenLabs(api_key=key)
    vid = voice_id or "21m00Tcm4TlvDq8ikWAM"  # Rachel (default)

    slides = content.get("slides", [])
    for i, slide in enumerate(slides):
        notes = slide.get("speaker_notes", "").strip()
        if not notes:
            continue
        out_file = out_dir / f"slide_{i+1:02d}.mp3"
        print(f"  Slide {i+1}: {slide.get('title', '')}")
        audio = client.generate(text=notes, voice=vid, model="eleven_multilingual_v2")
        save(audio, str(out_file))
        print(f"  ✓ {out_file}")


def _show_setup_hint(engine: str):
    hints = {
        "f5tts": (
            "F5-TTS Setup (Apple Silicon / M4 Pro Max):\n"
            "  1. pip install f5-tts-mlx\n"
            "  2. Record 60s of your voice → save as assets/voice_reference.wav\n"
            "  3. python tts.py outputs/YOUR_RUN/ --engine f5tts\n"
        ),
        "kokoro": (
            "Kokoro TTS Setup:\n"
            "  1. pip install kokoro-onnx soundfile\n"
            "  2. Download model: python -c \"from kokoro_onnx import Kokoro; Kokoro.download()\"\n"
            "  3. python tts.py outputs/YOUR_RUN/ --engine kokoro\n"
        ),
        "elevenlabs": (
            "ElevenLabs Setup:\n"
            "  1. pip install elevenlabs\n"
            "  2. Create account + voice clone at https://elevenlabs.io\n"
            "  3. export ELEVENLABS_API_KEY=your_key\n"
            "  4. python tts.py outputs/YOUR_RUN/ --engine elevenlabs --voice-id YOUR_ID\n"
        ),
    }
    print(f"\n{hints.get(engine, '')}")


if __name__ == "__main__":
    main()
