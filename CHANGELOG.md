# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] - 2026-09-22

### Added

- `BodhiTTSService`, a Pipecat text-to-speech service in `bodhi.integrations.pipecat_tts`, built on Pipecat's `InterruptibleTTSService` — Bodhi's TTS protocol has no cancel command, so barge-in reconnects rather than leaving stale audio in flight. Ten languages, the built-in voices, and 8/16/24 kHz output. Verified live in a Pipecat pipeline: two utterances produced matching started/stopped frames and 4s of speech.
- `stt` and `tts` extras, so the install string names the service rather than the framework: `pip install "bodhi-api-sdk[stt]"`. Both resolve to `pipecat-ai>=1.4` today; naming them now keeps the install string stable if either later needs a dependency the other does not. `[pipecat]` still works as an alias.
- `bodhi.integrations.pipecat_stt` raises an `ImportError` naming the extra when Pipecat is missing, instead of failing on a transitive import.

---

## [1.0.0] - 2026-09-16 (`bodhi-api-sdk`)

First release of `bodhi-api-sdk`, the SDK for Bodhi's new API platform. It is
published alongside `bodhi-sdk` 1.4.x, which keeps working against the old
endpoint; `bodhi-sdk` will be retired once everyone has moved across.

### Changed

- **Package renamed** to `bodhi-api-sdk` on PyPI. The import is unchanged — still `from bodhi import BodhiClient` — so only your install line moves. Both packages ship the same `bodhi` module and so cannot be installed at the same time: run `pip uninstall bodhi-sdk` before installing this one, or you end up with a mix of the two.
- **Auth is the API key alone.** `customer_id` is gone from `BodhiClient` and `BodhiSTTService`, along with the `BODHI_CUSTOMER_ID` environment variable, the UUID check and the `x-customer-id` header. Only `x-api-key` is sent now. Passing `customer_id=` raises `TypeError`, so a missed call site fails at once rather than silently authenticating as nobody.
- **Default endpoint is `wss://stt.navana.ai`**, was `wss://bodhi.navana.ai`. Pass `uri=` (`BodhiClient`) or `url=` (`BodhiSTTService`) to point elsewhere.

### Migrating from `bodhi-sdk`

```bash
pip uninstall bodhi-sdk
pip install bodhi-api-sdk
```

```python
# before
client = BodhiClient(api_key=API_KEY, customer_id=CUSTOMER_ID)

# after
client = BodhiClient(api_key=API_KEY)
```

`BODHI_API_KEY` is unchanged, and so is every other import, class, method, event
and config field. Drop the customer id and you are done.

---

# Earlier history (`bodhi-sdk`)

Everything below is the release history of `bodhi-sdk`, the package this one
replaces. Version numbers restart above, so the `1.0.0` dated 2025-06-04 is a
`bodhi-sdk` release and unrelated to the `1.0.0` at the top of this file.

---

## [1.4.0] - 2026-09-03

### Added

- `endpoint_silence_duration` on `TranscriptionConfig`: trailing silence, in seconds, before an utterance is finalised. Documented on the [advanced features](https://navana.gitbook.io/bodhi/quickstart/streaming-websocket/advanced-features) page but previously unreachable from the SDK. The server clamps it to 0.44-1.2; `None` keeps the model's default. Verified live: 0.44 produced 6 utterances on a 23s call where 1.2 produced 4.

### Fixed

- `_prepare_config` rebuilt the caller's `TranscriptionConfig` from a hardcoded field list before sending it, so any field missing from that list was silently dropped on the way to the wire. It now serialises the caller's config directly. This affected `endpoint_silence_duration` and would have silently swallowed any future field.
- Hotwords without a score no longer send `"score": null`, matching the Pipecat integration's serialisation.

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
