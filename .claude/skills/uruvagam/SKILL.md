---
name: uruvagam
description: Drive the uruvagam pipeline to turn raw notes or a topic into a final interactive HTML presentation with voice-cloned narration and a lip-synced talking-head presenter video. Use this skill whenever the user wants to generate a uruvagam deck, create a narrated or lip-synced presentation in this repo, build a talking-head training video from notes, regenerate any stage (content, voice, video, html), or resume an existing outputs/<run>/ directory. Also trigger on requests like "make a presentation with my voice", "create slides with a lip-synced presenter", "narrate this topic with my voice clone", or any mention of the local content → audio → video → html workflow in this project. Always invoke when working inside the uruvagam repo on slides, narration, voice cloning, lip-sync, or talking-head video tasks even if the user does not say "uruvagam" by name.
---

# uruvagam — narrated lip-synced presentation pipeline

Drives the local pipeline that turns notes or a topic into a final `preview.html` with per-slide narration + lip-synced presenter video. The Makefile is the canonical interface; this skill adds preflight, smart asset selection, and stage-gated review.

## Operating principles

- **Stage-gated.** Run one stage, pause for the user to review the artifact, then continue. Do **not** chain stages silently — a bad voice ref or face ref wastes 30+ min of video generation.
- **Stop on first hard error.** If preflight fails, an asset is missing, or a gate is not met, halt and surface the exact fix command. Never paper over a missing prerequisite.
- **Be explicit about impact.** Every input choice (voice ref, face ref, duration, theme, provider) changes a specific downstream artifact. State the impact when asking.
- **Discover from the filesystem.** Do not ask the user for things you can read: existing `outputs/<run>/` dirs, voice refs under `assets/` and `assets/voice_refs/`, face refs under `assets/`, themes under `themes/`.
- **Provider:** three choices — `omlx` (local Qwen3.6 via oMLX, Mac Silicon), `ollama` (local, cross-platform), `claude` (cloud Haiku). `omlx` and `ollama` run the quality agents; `claude` skips them. Default is `omlx`.

## Phase 0 — Preflight (always, no exceptions)

Run `bash .claude/skills/uruvagam/scripts/preflight.sh` from the repo root. It checks: `.venv`, `OMLX_API_KEY`, oMLX server reachability, `f5_tts_mlx`, `lipsync`, Wav2Lip checkpoint, and required assets.

If any **required** check fails, **stop and print the exact fix command** the script suggests. Do not continue.

The script emits a final line `PREFLIGHT_RESULT: <pass|fail>`. Only proceed on `pass`.

## Phase 1 — Capture inputs (interactively, with impact stated)

Decide **resume vs new** first:

- If the user references an existing run (e.g. `outputs/openbao_migration_..._20260527_152919`), use it as `RUN`. Detect which stages are already done by checking for `content.json`, `audio_qwen3tts/`, `video/` files. Skip ahead.
- Otherwise, capture the new-run inputs below.

Use `AskUserQuestion` for inputs that are choices; ask plain questions for free-form inputs. Always include the **impact** in the question, not in a separate help text the user has to dig for.

**Required for a new run:**

| Input | Type | Impact |
|---|---|---|
| `SOURCE` (file) **or** `TOPIC` (string) | choice | Source mode restructures your notes; topic mode invents structure from scratch. Pick source mode when you have real notes — it is more grounded. |
| `TITLE` | string | Used in slide 1, PPTX filename, and run directory name. |
| `DURATION` (minutes) | int | Drives slide count (≈ duration / 1.5, min 6) and narration density. Longer duration → more slides, not longer slides. |
| `AUDIENCE` | string | Changes the technical depth and tone of generated content. Be specific: "platform engineers familiar with Kubernetes" is much better than "engineers". |
| `THEME` | choice from `themes/` | See **Theme selection** below — enumerate from `themes/*.yaml` before asking. |
| `PROVIDER` | choice | `omlx` (default, local Mac Silicon, quality agents run), `ollama` (local cross-platform, quality agents run), or `claude` (cloud Haiku 4.5, **agents do not run**). Pick the one whose key/server you have ready. |

**Theme selection:**
Before asking the user to pick, enumerate candidates:
1. Run `ls themes/*.yaml` to discover available themes
2. For each theme YAML, read `name`, `colors.deck_background`, `colors.slide_background`, and `colors.text`
3. From those values derive the visual description:
   - dark deck_background → "dark background, light text — best for screen projection and video"
   - light deck_background → "white/light background, dark text — best for print or org decks"
4. Show the user the name + description for each theme
5. Ask them to pick one
6. Use the chosen name as `THEME=` in all subsequent make commands

Current themes and their correct descriptions (pre-derived so Claude does not need to re-read on every run):
- `dark_corporate` — dark navy background (#1A1A2E), white text, blue accent. Best for screen, conference, video.
- `light_org` — white background (#FFFFFF), near-black text, corporate blue accent. Best for org presentations, print.

**Asset selection (always interactive, even on resume if asset not yet locked):**

For voice ref and face ref, the skill must:
1. Enumerate candidates from the filesystem
2. Show metadata for each (duration, size, sample rate where relevant)
3. State the **impact** of the choice
4. Show the **expected format** before the user picks

See `references/assets.md` for the format spec to display to the user, and read it before running asset selection so you can quote the expected formats accurately.

## Phase 2 — Stage 1: Content

```bash
make content SOURCE=<file> TITLE=<title> DURATION=<n> AUDIENCE=<text> PROVIDER=omlx THEME=<theme> [TEMPLATE=...] [LOGO=...]
```

Or for topic mode:

```bash
make content-topic TOPIC=<topic> DURATION=<n> AUDIENCE=<text> PROVIDER=omlx THEME=<theme>
```

After this, the newest `outputs/<run>/` is the active `RUN`. Capture it:

```bash
RUN=$(ls -td outputs/*/ | head -1 | sed 's|/$||')
```

### ▶ Review gate 1

Tell the user to review and ask them to confirm before proceeding:

```bash
make report RUN="$RUN"     # critic score (must be ≥ 7 to proceed)
make open RUN="$RUN"       # preview the deck
```

**Stop if:**
- critic score < 7 → user should improve source notes or edit `content.json` manually and run `make slides RUN="$RUN"` then loop back
- bullets/structure wrong → same
- speaker_notes don't sound like the presenter → edit `assets/speaker_style.txt` then re-run content

Do not generate any audio until the user confirms the deck is good.

## Phase 3 — Stage 2a: Voice preview (Qwen3-TTS, slides 1–2 only)

Before running, **show the user the instruction that will be used** (print the value of `QWEN_INSTRUCTIONS` from the Makefile default or their override). Tell them:
> "This is the voice instruction for the clone. If the preview sounds off, you can override it with `QWEN_INSTRUCTIONS="..."` on any make command."

```bash
make voice-qwen-preview RUN="$RUN" QWEN_VOICE_REF=<chosen_wav> QWEN_VOICE_REF_TEXT=<chosen_txt>
```

### ▶ Review gate 2

```bash
open "$RUN/audio_qwen3tts/slide_01.wav"
open "$RUN/audio_qwen3tts/slide_02.wav"
```

**Stop if:** voice does not match the intended presenter. When the voice is off, surface two remedies in order:
1. Try a different voice ref from `assets/voice_refs/`
2. Tune the instruction: `make voice-qwen-preview RUN="$RUN" QWEN_VOICE_REF=<wav> QWEN_VOICE_REF_TEXT=<txt> QWEN_INSTRUCTIONS="<your custom instruction>"`

Do not proceed to full voice generation until the user confirms the preview sounds right.

## Phase 4 — Stage 2b: Voice full

```bash
make voice-qwen RUN="$RUN" QWEN_VOICE_REF=<chosen_wav> QWEN_VOICE_REF_TEXT=<chosen_txt>
```

Verify with `make verify RUN="$RUN"` quickly before moving on. Long `speaker_notes` are auto-chunked under the hood (see Agents.md §2 long-notes note); if any slide audio is unexpectedly short, surface it.

## Phase 5 — Stage 3a: Video POC (slide 1)

**Critical:** write the Wav2Lip output directly into `video/` (not `video_qwen3tts/`) so the HTML reads it. Pass `QWEN_VIDEO_DIR="$RUN/video"` explicitly.

```bash
make video-qwen-poc RUN="$RUN" SLIDE=1 VIDEO_FACE_REF=<chosen_face_mp4> QWEN_VIDEO_DIR="$RUN/video"
```

First-run face detection takes ~500s; subsequent slides reuse the cached `.pk` file (~13s each). If the user switched face refs mid-run, **delete the stale cache** at `outputs/<run>/cache/lipsync/<old>.pk` first.

### ▶ Review gate 3

```bash
open "$RUN/video/slide_01_lipsync.mp4"
```

**Stop if:** lip sync timing is wrong, mouth region is grossly mismatched, or face crop is unusable. Wav2Lip's known limitation is the low-res mouth texture; if that is unacceptable, the user can run the LatentSync CUDA path separately (see README §8).

## Phase 6 — Stage 3b: Video all slides

```bash
make video-qwen-all RUN="$RUN" VIDEO_FACE_REF=<chosen_face_mp4> QWEN_VIDEO_DIR="$RUN/video"
```

Expected runtime: ~13s per slide after the cache is warm.

## Phase 7 — Stage 4: Final HTML and verify

```bash
make html RUN="$RUN"
make verify RUN="$RUN"
make open RUN="$RUN"
```

Report to the user: total slides, total audio duration, total video duration, and the final `preview.html` path.

## Resume logic

If the user comes back to an existing `RUN`, decide what to do based on what is present:

| Already present | Next action |
|---|---|
| `content.json` only | Phase 3 (voice preview) |
| `content.json` + `audio_qwen3tts/` | Phase 5 (video POC) |
| `content.json` + `audio_qwen3tts/` + partial `video/` | Run only missing slides via `make video-qwen-poc SLIDE=N`, then `make html` |
| Everything | Phase 7 (just regenerate HTML and open) |

Always re-confirm asset choices on resume — the user may want a different voice or face this time. Switching face ref requires clearing the lipsync cache.

## Recovery / common failures

- **oMLX 401:** `OMLX_API_KEY` is wrong or unset. Tell the user to `export OMLX_API_KEY=...` and re-run.
- **oMLX connection refused:** server is not running. The user starts it themselves; the skill cannot start it.
- **`f5_tts_mlx` import error:** `pip install -r requirements.txt` inside `.venv`.
- **`lipsync` import error:** `pip install -r requirements-lipsync.txt` inside `.venv`.
- **Missing Wav2Lip checkpoint:** preflight will catch this; tell the user to fetch it (path: `weights/checkpoints/wav2lip.pth`, ~416 MB) and run `python scripts/normalize_wav2lip.py` if that helper exists, else point them at `setup.sh`.
- **Audio cut off at ~70s for long slides:** known Qwen3-TTS truncation; chunking is now in `tts.py`. If it still happens, the chunking call was bypassed — verify `tts.py --engine qwen3tts-omlx` was used (not just `qwen3tts`).
- **Lip sync uses wrong face after switching ref:** stale `.pk` cache. Delete `outputs/<run>/cache/lipsync/<old_face>.pk`.

## What this skill does NOT do

- Does not start the oMLX server (user-managed daemon).
- Does not install models or weights — preflight checks and reports; user runs `setup.sh` or the suggested commands.
- Does not promote `video_eval/<backend>/` outputs into `video/`. The user does that explicitly after review.
- Does not run the LatentSync CUDA path. That requires the Ansible harness and a remote GPU host — out of scope here; refer the user to README §8.
