# =============================================================================
# Makefile
# Usage:  make <target> [VARIABLE=value ...]
# Guide:  RUNBOOK.md
# =============================================================================

# ── Input configuration (override on command line) ────────────────────────────
SOURCE   ?= assets/source_notes.example.txt
TITLE    ?= My Presentation
DURATION ?= 10
AUDIENCE ?= General technical audience
SLIDE    ?= 1
PROVIDER ?= omlx
THEME ?= dark_corporate
TEMPLATE ?=
LOGO ?=
CONTENT_INSTRUCTIONS ?=
QWEN_TTS_MODEL ?= Qwen3-TTS-12Hz-1.7B-Base-bf16
QWEN_VOICE ?=
QWEN_VOICE_REF ?= assets/voice_reference.wav
QWEN_VOICE_REF_TEXT ?= assets/voice_reference.txt
QWEN_INSTRUCTIONS ?= Clone this speaker's voice as closely as possible. Match the speaker's pace, pitch, and natural delivery from the reference audio. Maintain consistent voice throughout. Do not add dramatic emphasis or theatrical energy.
QWEN_SPEED ?= 1.0
QWEN_AUDIO_DIR ?= $(RUN)/audio_qwen3tts
QWEN_VIDEO_DIR ?= $(RUN)/video_qwen3tts
VIDEO_FACE_REF ?= assets/face_reference.mp4
THEME_ARGS = --theme "$(THEME)"
TEMPLATE_ARGS = $(if $(strip $(TEMPLATE)),--template "$(TEMPLATE)",)
LOGO_ARGS = $(if $(strip $(LOGO)),--logo "$(LOGO)",)
CONTENT_INSTRUCTIONS_ARG = $(if $(strip $(CONTENT_INSTRUCTIONS)),--instructions "$(CONTENT_INSTRUCTIONS)",)
PRESENTATION_ARGS = $(THEME_ARGS) $(TEMPLATE_ARGS) $(LOGO_ARGS)
QWEN_TTS_VOICE_ARG = $(if $(strip $(QWEN_VOICE)),--qwen-voice "$(QWEN_VOICE)",)

# ── Runtime ───────────────────────────────────────────────────────────────────
PYTHON := .venv/bin/python

# Auto-detect the most recently created output directory.
# Override for any target:  make voice RUN=outputs/my_specific_run
RUN := $(shell ls -td outputs/*/ 2>/dev/null | head -1 | sed 's|/$$||')

# Slide count from current run's content.json (used by video-all)
NSLIDES := $(shell \
  test -f "$(RUN)/content.json" && \
  $(PYTHON) -c "import json; print(len(json.load(open('$(RUN)/content.json'))['slides']))" \
  2>/dev/null || echo 0)

.PHONY: help env \
        content content-fast content-topic \
        slides html \
        voice-preview voice voice-slide voice-qwen-preview voice-qwen voice-qwen-slide \
        video-poc video-all video-qwen-poc video-qwen-all \
        verify report show \
        open \
        clean clean-all

# =============================================================================
# DEFAULT — show help
# =============================================================================
.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "  Pipeline (run in this order for a new deck):"
	@echo "    make content          Generate content JSON + PPTX + HTML  (with agents)"
	@echo "    make voice-preview    Generate voice for slides 1-2        (quality check)"
	@echo "    make voice            Generate voice for all slides         (~10 min)"
	@echo "    make voice-qwen-preview  Generate Qwen clone voice slides 1-2 (opt-in)"
	@echo "    make video-poc        Lip-sync POC for slide SLIDE=1        (quality check)"
	@echo "    make video-qwen-poc   Lip-sync POC using Qwen audio         (opt-in)"
	@echo "    make video-all        Lip-sync all slides                   (~30 min cached)"
	@echo "    make html             Regenerate HTML with current videos"
	@echo "    make open             Open the final HTML presentation"
	@echo ""
	@echo "  Iteration:"
	@echo "    make content-fast     Generate content without agents       (fast, no LLM checks)"
	@echo "    make content-topic    Generate from TOPIC= string instead of SOURCE= file"
	@echo "    make slides           Regenerate PPTX from existing content.json"
	@echo "    make voice-slide      Regenerate voice for one slide        (SLIDE=N)"
	@echo "    make voice-qwen-slide Generate Qwen clone voice for one slide (SLIDE=N)"
	@echo ""
	@echo "  Quality:"
	@echo "    make verify           Verify all outputs in current run"
	@echo "    make report           Show quality_report.json for current run"
	@echo "    make show             List files in current run directory"
	@echo ""
	@echo "  Housekeeping:"
	@echo "    make env              Check prerequisites (venv, provider auth/server)"
	@echo "    make clean            Remove current run directory"
	@echo "    make clean-all        Remove all outputs/"
	@echo ""
	@echo "  Current run:  $(if $(RUN),$(RUN),(none — run make content first))"
	@echo ""
	@echo "  Variables (override on command line):"
	@echo "    SOURCE=$(SOURCE)"
	@echo "    TITLE=$(TITLE)"
	@echo "    DURATION=$(DURATION) min"
	@echo "    AUDIENCE=$(AUDIENCE)"
	@echo "    SLIDE=$(SLIDE)           (for video-poc / voice-slide)"
	@echo "    PROVIDER=$(PROVIDER)     (omlx | ollama | claude)"
	@echo "    THEME=$(THEME)"
	@echo "    TEMPLATE=$(TEMPLATE)"
	@echo "    LOGO=$(LOGO)"
	@echo "    QWEN_TTS_MODEL=$(QWEN_TTS_MODEL)"
	@echo "    QWEN_VOICE=$(QWEN_VOICE)"
	@echo "    QWEN_VOICE_REF=$(QWEN_VOICE_REF)"
	@echo "    QWEN_VOICE_REF_TEXT=$(QWEN_VOICE_REF_TEXT)"
	@echo "    QWEN_SPEED=$(QWEN_SPEED)"
	@echo "    QWEN_AUDIO_DIR=$(QWEN_AUDIO_DIR)"
	@echo "    QWEN_VIDEO_DIR=$(QWEN_VIDEO_DIR)"
	@echo "    VIDEO_FACE_REF=$(VIDEO_FACE_REF)"
	@echo "    RUN=$(RUN)"
	@echo ""

# =============================================================================
# ENVIRONMENT CHECK
# =============================================================================
env:
	@echo ""
	@echo "  Checking prerequisites (PROVIDER=$(PROVIDER))..."
	@test -d .venv \
	    && echo "  [OK]  .venv exists" \
	    || (echo "  [ERR] .venv not found — run: bash setup.sh" && exit 1)
	@[ "$(PROVIDER)" = "omlx" ] \
	    && (test -n "$$OMLX_API_KEY" \
	        && echo "  [OK]  OMLX_API_KEY is set" \
	        || echo "  [ERR] OMLX_API_KEY not set — run: export OMLX_API_KEY=...") \
	    || true
	@[ "$(PROVIDER)" = "ollama" ] \
	    && (curl -sf http://localhost:11434/api/tags -o /dev/null \
	        && echo "  [OK]  Ollama is reachable" \
	        || echo "  [WRN] Ollama not reachable — run: ollama serve") \
	    || true
	@[ "$(PROVIDER)" = "claude" ] \
	    && (test -n "$$ANTHROPIC_API_KEY" \
	        && echo "  [OK]  ANTHROPIC_API_KEY is set" \
	        || echo "  [ERR] ANTHROPIC_API_KEY not set — run: export ANTHROPIC_API_KEY=...") \
	    || true
	@$(PYTHON) -c "import f5_tts_mlx" 2>/dev/null \
	    && echo "  [OK]  f5-tts-mlx installed (Phase A)" \
	    || echo "  [INFO] f5-tts-mlx not installed (only needed for --engine f5tts on Mac)"
	@$(PYTHON) -c "import lipsync" 2>/dev/null \
	    && echo "  [OK]  lipsync installed (Phase B)" \
	    || echo "  [INFO] lipsync not installed (Phase B — optional, needed for video)"
	@test -n "$(RUN)" \
	    && echo "  [OK]  current run: $(RUN)" \
	    || echo "  [INFO] no run directory found — run make content"
	@echo ""

# =============================================================================
# CONTENT GENERATION
# =============================================================================

# Full run from a notes file with all quality agents.
# Provider-specific keys (OMLX_API_KEY / ANTHROPIC_API_KEY) are checked by generate.py and the LLM call.
content:
	$(PYTHON) generate.py \
	    --source "$(SOURCE)" \
	    --title "$(TITLE)" \
	    --duration $(DURATION) \
	    --audience "$(AUDIENCE)" \
	    --provider $(PROVIDER) \
	    $(PRESENTATION_ARGS) \
	    $(CONTENT_INSTRUCTIONS_ARG)
	@echo ""
	@echo "  Next: make report   (check quality score)"
	@echo "        make open     (review slides)"
	@echo "        make voice-preview"

# Fast iteration — skip agents, no LLM quality checks
content-fast:
	$(PYTHON) generate.py \
	    --source "$(SOURCE)" \
	    --title "$(TITLE)" \
	    --duration $(DURATION) \
	    --audience "$(AUDIENCE)" \
	    --provider $(PROVIDER) \
	    $(PRESENTATION_ARGS) \
	    $(CONTENT_INSTRUCTIONS_ARG) \
	    --no-agents
	@echo ""
	@echo "  Agents skipped. Next: make open"

# Generate from a topic string instead of a notes file
# Usage: make content-topic TOPIC="Introduction to Kafka"
content-topic:
	@test -n "$(TOPIC)" || (echo "ERROR: set TOPIC= e.g.  make content-topic TOPIC=\"Introduction to Kafka\"" && exit 1)
	$(PYTHON) generate.py \
	    "$(TOPIC)" \
	    --duration $(DURATION) \
	    --audience "$(AUDIENCE)" \
	    --provider $(PROVIDER) \
	    $(PRESENTATION_ARGS) \
	    $(CONTENT_INSTRUCTIONS_ARG)

# =============================================================================
# SLIDES — regenerate PPTX or HTML from existing content.json
# =============================================================================

# Regenerate PPTX without re-running the LLM (useful after manual content.json edits)
slides:
	@test -n "$(RUN)" || (echo "ERROR: no run directory. Run 'make content' first." && exit 1)
	@test -f "$(RUN)/content.json" || (echo "ERROR: $(RUN)/content.json not found." && exit 1)
	$(PYTHON) slide_gen.py "$(RUN)" $(PRESENTATION_ARGS)

# Regenerate HTML — picks up any new video files in video/
html:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	$(PYTHON) html_gen.py "$(RUN)" $(THEME_ARGS) $(LOGO_ARGS)
	@echo "  HTML regenerated: $(RUN)/preview.html"

# =============================================================================
# VOICE (TTS)
# =============================================================================

# Preview — only slides 1-2 (90s, fast quality check)
voice-preview:
	@test -n "$(RUN)" || (echo "ERROR: no run directory. Run 'make content' first." && exit 1)
	$(PYTHON) tts.py $(RUN)/ --preview
	@echo ""
	@echo "  Listen:"
	@echo "    open $(RUN)/audio/slide_01.wav"
	@echo "    open $(RUN)/audio/slide_02.wav"
	@echo ""
	@echo "  Voice sounds good? → make voice"
	@echo "  Needs tuning?      → edit assets/speaker_style.txt, then make content + make voice-preview"

# Full voice generation — all slides (~10 min)
voice:
	@test -n "$(RUN)" || (echo "ERROR: no run directory. Run 'make content' first." && exit 1)
	$(PYTHON) tts.py $(RUN)/
	@echo ""
	@echo "  Next: make video-poc"

# Regenerate voice for a single slide only
# Usage: make voice-slide SLIDE=5
voice-slide:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	$(PYTHON) tts.py $(RUN)/ --slide $(SLIDE)
	@echo "  Regenerated: $(RUN)/audio/slide_$(shell printf '%02d' $(SLIDE)).wav"

# Qwen3-TTS via local oMLX — opt-in, writes to audio_qwen3tts/ by default.
voice-qwen-preview:
	@test -n "$(RUN)" || (echo "ERROR: no run directory. Run 'make content' first." && exit 1)
	@test -n "$$OMLX_API_KEY" || (echo "ERROR: OMLX_API_KEY not set" && exit 1)
	$(PYTHON) tts.py $(RUN)/ \
	    --engine qwen3tts-omlx \
	    --preview \
	    --voice-ref "$(QWEN_VOICE_REF)" \
	    --voice-ref-text "$(QWEN_VOICE_REF_TEXT)" \
	    --qwen-model "$(QWEN_TTS_MODEL)" \
	    $(QWEN_TTS_VOICE_ARG) \
	    --qwen-instructions "$(QWEN_INSTRUCTIONS)" \
	    --qwen-speed $(QWEN_SPEED) \
	    --output-dir "$(QWEN_AUDIO_DIR)"
	@echo ""
	@echo "  Listen:"
	@echo "    open $(QWEN_AUDIO_DIR)/slide_01.wav"
	@echo "    open $(QWEN_AUDIO_DIR)/slide_02.wav"

voice-qwen:
	@test -n "$(RUN)" || (echo "ERROR: no run directory. Run 'make content' first." && exit 1)
	@test -n "$$OMLX_API_KEY" || (echo "ERROR: OMLX_API_KEY not set" && exit 1)
	$(PYTHON) tts.py $(RUN)/ \
	    --engine qwen3tts-omlx \
	    --voice-ref "$(QWEN_VOICE_REF)" \
	    --voice-ref-text "$(QWEN_VOICE_REF_TEXT)" \
	    --qwen-model "$(QWEN_TTS_MODEL)" \
	    $(QWEN_TTS_VOICE_ARG) \
	    --qwen-instructions "$(QWEN_INSTRUCTIONS)" \
	    --qwen-speed $(QWEN_SPEED) \
	    --output-dir "$(QWEN_AUDIO_DIR)"
	@echo ""
	@echo "  Next: make video-qwen-poc"

voice-qwen-slide:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test -n "$$OMLX_API_KEY" || (echo "ERROR: OMLX_API_KEY not set" && exit 1)
	$(PYTHON) tts.py $(RUN)/ \
	    --engine qwen3tts-omlx \
	    --slide $(SLIDE) \
	    --voice-ref "$(QWEN_VOICE_REF)" \
	    --voice-ref-text "$(QWEN_VOICE_REF_TEXT)" \
	    --qwen-model "$(QWEN_TTS_MODEL)" \
	    $(QWEN_TTS_VOICE_ARG) \
	    --qwen-instructions "$(QWEN_INSTRUCTIONS)" \
	    --qwen-speed $(QWEN_SPEED) \
	    --output-dir "$(QWEN_AUDIO_DIR)"
	@echo "  Regenerated: $(QWEN_AUDIO_DIR)/slide_$(shell printf '%02d' $(SLIDE)).wav"

# =============================================================================
# VIDEO (LIP-SYNC)
# =============================================================================

# POC for one slide — run this before video-all to check quality
# Usage: make video-poc SLIDE=1
video-poc:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test -f "$(RUN)/audio/slide_$(shell printf '%02d' $(SLIDE)).wav" \
	    || (echo "ERROR: audio not found. Run 'make voice' first." && exit 1)
	$(PYTHON) lipsync_poc.py $(RUN)/ --slide $(SLIDE) --batch-size 4
	@echo ""
	@echo "  Review: open $(RUN)/video/slide_$(shell printf '%02d' $(SLIDE))_lipsync.mp4"
	@echo ""
	@echo "  Quality acceptable? → make video-all"
	@echo "  Needs improvement?  → see RUNBOOK.md § Stage 4 (GAN checkpoint / HeyGen)"

# All slides — run after video-poc passes quality check (~30 min with cached face boxes)
video-all:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test "$(NSLIDES)" != "0" || (echo "ERROR: could not read slide count from content.json" && exit 1)
	$(PYTHON) -c "\
import json, subprocess, sys; \
n = $(NSLIDES); \
print(f'Processing {n} slides...'); \
[subprocess.run([sys.executable, 'lipsync_poc.py', '$(RUN)/', '--slide', str(i), '--batch-size', '4'], \
    check=True) \
 for i in range(1, n+1)]"
	$(PYTHON) html_gen.py $(RUN)/
	@echo ""
	@echo "  All done. Next: make open"

# Qwen audio + existing Wav2Lip — opt-in, writes to video_qwen3tts/.
video-qwen-poc:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test -f "$(QWEN_AUDIO_DIR)/slide_$(shell printf '%02d' $(SLIDE)).wav" \
	    || (echo "ERROR: Qwen audio not found. Run 'make voice-qwen-preview' or 'make voice-qwen-slide' first." && exit 1)
	$(PYTHON) lipsync_poc.py $(RUN)/ \
	    --slide $(SLIDE) \
	    --face-ref "$(VIDEO_FACE_REF)" \
	    --audio-dir "$(QWEN_AUDIO_DIR)" \
	    --output-dir "$(QWEN_VIDEO_DIR)" \
	    --batch-size 4
	@echo ""
	@echo "  Review: open $(QWEN_VIDEO_DIR)/slide_$(shell printf '%02d' $(SLIDE))_lipsync.mp4"

video-qwen-all:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test "$(NSLIDES)" != "0" || (echo "ERROR: could not read slide count from content.json" && exit 1)
	$(PYTHON) -c "\
import subprocess, sys; \
n = $(NSLIDES); \
print(f'Processing {n} Qwen-audio slides...'); \
[subprocess.run([sys.executable, 'lipsync_poc.py', '$(RUN)/', '--slide', str(i), \
    '--face-ref', '$(VIDEO_FACE_REF)', '--audio-dir', '$(QWEN_AUDIO_DIR)', '--output-dir', '$(QWEN_VIDEO_DIR)', '--batch-size', '4'], \
    check=True) \
 for i in range(1, n+1)]"
	@echo ""
	@echo "  Qwen video files: $(QWEN_VIDEO_DIR)"

# =============================================================================
# QUALITY + REVIEW
# =============================================================================

verify:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	$(PYTHON) verify.py $(RUN)/

report:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test -f "$(RUN)/quality_report.json" \
	    || (echo "No quality_report.json — run 'make content' (not content-fast)" && exit 1)
	@$(PYTHON) -m json.tool $(RUN)/quality_report.json

show:
	@test -n "$(RUN)" || (echo "No run directory found." && exit 0)
	@echo "Run: $(RUN)"
	@echo ""
	@ls -lh $(RUN)/ 2>/dev/null
	@echo ""
	@test -d "$(RUN)/audio" && echo "Audio files:" && ls -lh $(RUN)/audio/ || true
	@test -d "$(RUN)/video" && echo "Video files:" && ls -lh $(RUN)/video/ || true

# =============================================================================
# OPEN
# =============================================================================

open:
	@test -n "$(RUN)" || (echo "ERROR: no run directory." && exit 1)
	@test -f "$(RUN)/preview.html" || (echo "ERROR: preview.html not found. Run 'make content' first." && exit 1)
	open $(RUN)/preview.html

# =============================================================================
# HOUSEKEEPING
# =============================================================================

clean:
	@test -n "$(RUN)" || (echo "Nothing to clean." && exit 0)
	@echo "Removing $(RUN)..."
	@rm -rf "$(RUN)"
	@echo "Done."

clean-all:
	@echo "Removing all output directories..."
	@rm -rf outputs/*/
	@echo "Done."
