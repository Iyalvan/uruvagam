# Contributing

Thanks for taking a look. uruvagam is intentionally small — a handful of `.py` files at root, no abstractions beyond what two providers actually share. Please keep additions in that spirit.

## How to add a new LLM provider

1. Add `_call_<provider>()` to `content_gen.py` matching the signature `(prompt, system, model, ...) -> str`.
2. Add it to the dispatch block in `generate_content()` in `content_gen.py`.
3. Add `_call_<provider>()` to `agents.py` and a branch in `_dispatch_llm()`.
4. Add the choice to `--provider` in `generate.py`.
5. Add any default URL / model keys to `config.yaml` and wire them through `generate.py` like `ollama_base_url`.
6. Document the new provider in the README provider matrix.
7. If the provider has no quality-agent support (rate limits, cost, etc.), gate it in `generate.py` next to the existing `provider in ("omlx", "ollama")` check.

## How to add a new TTS engine

1. Add `_run_<engine>()` to `tts.py`.
2. Add the engine name to the `--engine` choices in `tts.py`'s argparse setup.
3. Add a corresponding Makefile target if it has a distinct usage pattern (see `voice-qwen-preview` / `voice-qwen`).
4. Document required env vars or asset formats in the README "Assets you provide" section.

## How to add a new video backend

1. Add the backend to `video.py` (use the existing Wav2Lip path as the template).
2. Add a `video.backends.<name>` section to `config.yaml` with whatever paths/flags the backend needs.
3. If it needs a heavy install, add the requirements to `requirements-lipsync.txt` (don't add them to base `requirements.txt`).

## Testing locally

For a fast end-to-end smoke test with no LLM cost:

```bash
ollama serve &
ollama pull llama3.2
make content-topic TOPIC="Git branching strategies" DURATION=5 PROVIDER=ollama
```

For a content-only check (skip agents, fastest):

```bash
make content-fast SOURCE=assets/source_notes.example.txt TITLE="Smoke test" DURATION=5
```

There is no test suite. PRs that touch a stage should include a manual run in the description: which command, which provider, what the output looked like.

## Code style

- Simple > clever. Three similar lines beats a premature abstraction.
- No comments that restate what the code does. Comments are for the *why* — a constraint, a workaround, a non-obvious invariant.
- No defensive validation past the system boundary. Trust internal calls.
- Match existing patterns in the file you're editing — if `content_gen.py` uses `httpx`, don't introduce `requests` for one new call.
