#!/usr/bin/env bash
# bootstrap script — run once on a new machine
# Detects the LLM provider from config.yaml and prints provider-specific guidance.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  uruvagam setup  →  $REPO_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Phase A venv + base deps (always) ──────────────────────────
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "✓ created .venv"
fi

source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ Phase A dependencies installed (content gen, TTS, slides)"

# ── Phase B (lip sync) — install instructions only ─────────────
echo ""
echo "  Phase B (lip sync — torch, Wav2Lip, PyAV) is heavy and optional."
echo "  Install it later when you want talking-head video:"
echo "      pip install -r requirements-lipsync.txt"
echo "      pip install --no-deps -r requirements-lipsync-nodeps.txt"

# ── Asset placeholders (always) ────────────────────────────────
mkdir -p assets outputs weights/checkpoints

if [ ! -f "assets/speaker_style.txt" ]; then
  echo "# add 4-8 sentences of how you naturally speak" > assets/speaker_style.txt
  echo "✓ created assets/speaker_style.txt (placeholder — edit with your own voice)"
fi

if [ ! -f "assets/voice_reference.txt" ]; then
  echo "(add the exact transcript of your voice_reference.wav here)" > assets/voice_reference.txt
  echo "✓ created assets/voice_reference.txt (placeholder)"
fi

# ── Provider-specific guidance ─────────────────────────────────
PROVIDER="$(python3 -c "import yaml; print(yaml.safe_load(open('config.yaml')).get('provider', 'omlx'))" 2>/dev/null || echo omlx)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LLM provider in config.yaml: $PROVIDER"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

case "$PROVIDER" in
  omlx)
    echo "  Start the oMLX server manually (Mac Silicon), then export the key:"
    echo "      export OMLX_API_KEY=<your-key>"
    ;;
  ollama)
    if command -v ollama >/dev/null 2>&1; then
      echo "  ✓ ollama binary found"
    else
      echo "  ⚠  ollama binary not found — install from https://ollama.com"
    fi
    echo "  Pull a model (one of):"
    echo "      ollama pull llama3.2"
    echo "      ollama pull qwen2.5:14b"
    echo "  Make sure ollama is running (it usually auto-starts as a service)."
    ;;
  claude)
    echo "  Export your Anthropic API key:"
    echo "      export ANTHROPIC_API_KEY=<your-key>"
    ;;
  *)
    echo "  ⚠  Unknown provider '$PROVIDER' in config.yaml. Use omlx, ollama, or claude."
    ;;
esac

# ── Quick start ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Quick start:"
echo ""
echo "  # generate slides from your notes"
echo "  make content \\"
echo "      SOURCE=assets/your-notes.txt \\"
echo "      TITLE='Your Topic' \\"
echo "      DURATION=10 \\"
echo "      AUDIENCE='Your audience'"
echo ""
echo "  # or from a topic string only"
echo "  make content-topic TOPIC='Introduction to Kafka' DURATION=10"
echo ""
echo "  # open the result"
echo "  make open"
echo ""
echo "  See README.md for the full pipeline (voice, video, html)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
