# Bodhi API Python SDK

Bodhi API Python SDK provides a client for Navana's streaming speech recognition API.

## Installation

```bash
pip install bodhi-api-sdk
```

### Moving from `bodhi-sdk`

`bodhi-api-sdk` is the SDK for Bodhi's new API platform. Your imports do not
change — only the package you install, and the credentials you pass:

```bash
pip uninstall bodhi-sdk
pip install bodhi-api-sdk
```

```python
# before, with bodhi-sdk
from bodhi import BodhiClient
client = BodhiClient(api_key=API_KEY, customer_id=CUSTOMER_ID)

# now, with bodhi-api-sdk
from bodhi import BodhiClient
client = BodhiClient(api_key=API_KEY)
```

Drop `customer_id` and you are done. Classes, methods, events, config fields and
`BODHI_API_KEY` are all unchanged. The default endpoint is now
`wss://stt.navana.ai`.

**Uninstall `bodhi-sdk` first.** Both packages install the same `bodhi` module,
so they cannot be installed at the same time — installing one on top of the
other leaves you with a mix of the two. `bodhi-sdk` 1.4.x keeps working against
the old endpoint and will be retired once everyone has moved across.

## Usage

To use the Bodhi Python SDK, follow these steps:

1.  **Installation:**
    Install the SDK using pip:

    ```bash
    pip install bodhi-api-sdk
    ```

2.  **Initialization:**
    Create a `BodhiClient` instance with your API key:

    ```python
    from bodhi import BodhiClient

    client = BodhiClient(api_key="YOUR_API_KEY")
    ```

3.  **Transcription:**
    Use the client methods to transcribe audio. The SDK supports transcription from local files, remote URLs, and streams.

    - **Local File Transcription:**

      ```python
      config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=False,    # Enable language identification at start (default: False)
        transliterate=False,   # Enable transliteration output (default: False)
        endpoint_silence_duration=0.6,  # Trailing silence before an utterance
                                        # is finalised, in seconds. Omitted
                                        # unless set; server default 0.44,
                                        # clamped to 0.44-1.2
      )
      response = client.transcribe_local_file(audio_file_path, config=config)
      print(response.text)
      ```

    - **Remote URL Transcription:**

      ```python
      config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=False,    # Enable language identification at start (default: False)
        transliterate=False,   # Enable transliteration output (default: False)
        endpoint_silence_duration=0.6,  # Trailing silence before an utterance
                                        # is finalised, in seconds. Omitted
                                        # unless set; server default 0.44,
                                        # clamped to 0.44-1.2
      )
      response = client.transcribe_remote_url("http://example.com/audio.wav", config)
      print(response.text)
      ```

    - **Streaming Transcription:**
      Refer to the examples for detailed instructions on setting up streaming transcription.

4.  **Event Handling:**
    You can register event listeners to handle different stages of the transcription process using the `client.on` method and the `LiveTranscriptionEvents` enum. This is particularly useful for streaming and remote URL transcriptions where events are emitted asynchronously.

    ```python
    from bodhi import LiveTranscriptionEvents

    async def on_transcript(response):
        print(f"Transcript: {response.text}")

    async def on_utterance_end(response):
        print(f"UtteranceEnd: {response}")

    async def on_speech_started(response):
        print(f"SpeechStarted: {response}")

    async def on_error(e):
        print(f"Error: {str(e)}")

    client.on(LiveTranscriptionEvents.Transcript, on_transcript)
    client.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    client.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
    client.on(LiveTranscriptionEvents.Error, on_error)
    ```

    Common events include:

    - `LiveTranscriptionEvents.Transcript`: Emitted when a new transcription segment is available.
    - `LiveTranscriptionEvents.UtteranceEnd`: Emitted when an utterance is detected as complete.
    - `LiveTranscriptionEvents.SpeechStarted`: Emitted when speech activity is detected.
    - `LiveTranscriptionEvents.Error`: Emitted when an error occurs during transcription.
    - `LiveTranscriptionEvents.Close`: Emitted when the WebSocket connection is closed.

## Pipecat integration

Building a voice agent with [Pipecat](https://github.com/pipecat-ai/pipecat)? Bodhi
drops into the STT slot of a Pipecat pipeline:

```bash
pip install "bodhi-api-sdk[stt]"
```

```python
import os

from pipecat.pipeline.pipeline import Pipeline

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

stt = BodhiSTTService(
    api_key=os.environ["BODHI_API_KEY"],
    model="hi-general-v2-8khz",
    settings=BodhiSTTService.Settings(
        parse_number=True,                   # normalise numbers, dates, currency
        endpoint_silence_duration=0.6,       # server-side endpointing, 0.44-1.2s
        hotwords=[BodhiHotword("बजाज फिनसर्व", 2.0)],
    ),
)

pipeline = Pipeline([
    transport.input(),
    stt,
    context_aggregator.user(),
    llm,
    tts,
    transport.output(),
])
```

That is the whole integration — `api_key` and `model` are the only
required arguments, and `url` defaults to `wss://stt.navana.ai`. Bodhi's
partial results arrive as `InterimTranscriptionFrame`s and its endpointed final
results as `TranscriptionFrame`s, so interruption handling and turn taking work
exactly as they do with any other Pipecat STT service.

Maintained by [Navana Tech](https://navana.ai/), who build Bodhi.

Written for pipecat-ai 1.4 and verified on 1.4.0 and 1.8.1. Streaming and
transcripts also work as far back as 1.0.0; switching model or hotwords at
runtime needs 1.4+, since that is where Pipecat's reconnect hook arrived. Needs
Python 3.10+, as Pipecat does.

If you pin `pipecat-ai` below 1.4, install plain `bodhi-api-sdk` (so pip doesn't
touch your pin) and import the same module, or copy
`bodhi/integrations/pipecat_stt.py` into your project — it is self-contained and
imports nothing else from this SDK.

### Advanced features

Every field from the streaming
[advanced features](https://docs.navana.ai/speech-to-text/advanced-features/)
page is reachable from here:

| Bodhi feature | How to set it |
|---|---|
| Context biasing (hotwords) | `Settings(hotwords=[BodhiHotword("phrase", 2.0)])` |
| Endpoint silence threshold | `Settings(endpoint_silence_duration=0.6)` — seconds; server default 0.44, clamped to 0.44–1.2 |
| Parse numbers into numerals | `Settings(parse_number=True)` |
| Partial result exclusion | `BodhiSTTService(..., interim_results=False)` — also stops the server sending them |
| Aux metadata | `BodhiSTTService(..., aux=True)` |
| Confidence and word timings | already on every frame's `result` |
| Confidence filtering | `BodhiSTTService(..., min_confidence=0.5)` — Pipecat's guidance; `0` keeps everything |

`result` carries the raw Bodhi message, so word timings and confidence are
there on every final without setting any flag:

```python
segment = frame.result["segment_meta"]
segment["confidence"]        # 0.87 — utterance level
segment["words"][0]          # {"word": "आपने", "confidence": 0.873,
                             #  "start_time": 0.32, "end_time": 0.48}
```

`aux=True` adds an `aux_info` block on top of that, with `request_time`,
`eot_wait_time` and `processed_audio_duration` for latency debugging.

### Trying it out

Two runnable examples, neither needing an LLM or TTS key:

```bash
export BODHI_API_KEY=...

# 1. Transcribe a recording through a real Pipecat pipeline.
curl -O https://stt.navana.ai/audios/loan.wav
python -m bodhi.examples.pipecat_stream_wav loan.wav --model hi-banking-v2-8khz

# 2. Transcribe your microphone live in the browser.
pip install "pipecat-ai[webrtc,silero,runner]"
python -m bodhi.examples.pipecat_mic_bot     # then open http://localhost:7860/client
```

For a full talking bot, follow the
[Pipecat quickstart](https://docs.pipecat.ai/pipecat/get-started/quickstart) and
replace its `DeepgramSTTService(...)` line with the `BodhiSTTService(...)` above.

`docs/pipecat-listing/bodhi.mdx` is this integration's service page for
docs.pipecat.ai, submitted as a
[community integration](https://github.com/pipecat-ai/pipecat/blob/main/COMMUNITY_INTEGRATIONS.md).
Keep it in step with the parameters above.

For complete code examples and detailed usage instructions for various scenarios, please refer to the [official documentation](https://docs.navana.ai/introduction/).
