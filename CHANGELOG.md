# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.0] - 2026-09-02

### Added

- Pipecat integration: `bodhi.integrations.pipecat_stt.BodhiSTTService`, a streaming STT service for [Pipecat](https://github.com/pipecat-ai/pipecat) voice agents. Install with `pip install "bodhi-sdk[pipecat]"`. Bodhi partials become `InterimTranscriptionFrame`s and endpointed finals become `TranscriptionFrame`s, with hotwords, number parsing and endpointing exposed through `BodhiSTTService.Settings`. Written for pipecat-ai 1.4 and verified on 1.4.0 and 1.8.1, with streaming also verified on 1.0.0 (runtime settings changes need 1.4+); needs Python 3.10+, as Pipecat does.
- Final transcripts below 50% utterance confidence are dropped, following Pipecat's guidance for services that report confidence; tune or disable with `min_confidence`. Measured live at 0.87-0.93 on clean speech, so the default drops nothing in practice.
- The first keepalive after audio stops carries 1.5s of silence, past Bodhi's longest endpointing threshold, so a pending utterance is finalised during the gap instead of being merged with whatever is said after it. Verified live against `wss://bodhi.navana.ai` with a 25s gap.
- `upload.sh` now builds with `python -m build` and refuses a non-normalised sdist filename. PyPI enforces PEP 625, and `setup.py sdist` on older setuptools produces `bodhi-sdk-<version>.tar.gz`, which PyPI rejects with a 400 *after* the wheel has uploaded — burning the version. The `safety` scan is now skipped when the tool isn't installed instead of aborting the release.
- `tests/pipecat_smoke_test.py`, which exercises the integration against a fake Bodhi server (no credentials, no network); `examples/pipecat_stream_wav.py`, which streams a WAV file through a real Pipecat pipeline; and `examples/pipecat_mic_bot.py`, a browser microphone bot that prints live transcripts.

---

## [1.2.0] - 2026-01-08

### Added

- `language_code` field in `TranscriptionResponse` to expose the detected language from the server (e.g., `"hi"`, `"te"`, `"en"`).

---

## [1.1.0] - 2026-01-08

### Added

- New configuration options in `TranscriptionConfig`:
  - `at_start_lid` (bool, default: `False`): Enable language identification at the start of transcription.
  - `transliterate` (bool, default: `False`): Enable transliteration output for transcription results.

---

## [1.0.0.post1] - 2025-06-05

### Changed

- Refactored internal exception handling logic to improve consistency and maintainability.
- No changes to the public API or user-facing behavior.

## [1.0.0] - 2025-06-04

### Changed

- Migrated WebSocket implementation from `websockets` to `aiohttp` for improved performance and compatibility.
- Centralized and standardized error response handling using `error_utils`.

### Breaking Changes ⚠️

- `client.close_connection()`, `client.transcribe_local_file()`, and `client.transcribe_remote_url()` no longer return values directly.  
  🔁 Instead, responses are sent via the event listener mechanism.
- Event system now supports **only one listener per event type**.  
  Registering a new listener for an event will **overwrite** the previous one.

---

## [0.1.0] - 2023-04-28

### Added

- Initial release of Bodhi Python SDK.
- Support for connecting to Bodhi WebSocket server.
