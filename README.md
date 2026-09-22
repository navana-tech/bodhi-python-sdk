# Bodhi API Python SDK

Streaming speech recognition for Indian languages, from
[Navana](https://navana.ai/). Ten languages, bilingual with English, built for
telephony — 8 kHz and 16 kHz models, server-side endpointing, word-level
timings and confidence.

[Documentation](https://docs.navana.ai/introduction/) ·
[Advanced features](https://docs.navana.ai/speech-to-text/advanced-features/) ·
[Pipecat integration](#pipecat-integration)

## Install

```bash
pip install bodhi-api-sdk
```

Python 3.7+. You need an API key from your Bodhi dashboard.

## Moving from `bodhi-sdk`

`bodhi-api-sdk` targets Bodhi's new API platform. Imports do not change — only
the package you install and the credentials you pass:

```bash
pip uninstall bodhi-sdk        # do this first, see below
pip install bodhi-api-sdk
```

```python
client = BodhiClient(api_key=API_KEY, customer_id=CUSTOMER_ID)   # before
client = BodhiClient(api_key=API_KEY)                            # now
```

Drop `customer_id` and you are done. Classes, methods, events, config fields and
`BODHI_API_KEY` are unchanged; the default endpoint is now
`wss://stt.navana.ai`.

**Uninstall `bodhi-sdk` first.** Both packages install the same `bodhi` module,
so installing one over the other leaves a mix of the two. `bodhi-sdk` 1.4.x
keeps working against the old endpoint until it is retired.

## Quickstart

Results arrive through events, so register a handler and then hand the client
some audio:

```python
import asyncio
import os

from bodhi import BodhiClient, LiveTranscriptionEvents, TranscriptionConfig


async def main():
    client = BodhiClient(api_key=os.environ["BODHI_API_KEY"])

    async def on_transcript(response):
        # "partial" results stream as you speak; "complete" is a finished utterance
        if response.type == "complete" and response.text.strip():
            print(response.text, response.segment_meta.confidence)

    async def on_error(error):
        print("error:", error)

    client.on(LiveTranscriptionEvents.Transcript, on_transcript)
    client.on(LiveTranscriptionEvents.Error, on_error)

    await client.transcribe_local_file(
        "call.wav",
        TranscriptionConfig(model="hi-banking-v2-8khz", sample_rate=8000),
    )


asyncio.run(main())
```

```
आपने हाल ही में कोई नया इन्वेस्टमेंट प्लान देखा है क्या 0.887073
digital banking के युग में 0.9034769
```

The audio must be mono 16-bit PCM WAV. Convert with
`ffmpeg -i in.wav -af "pan=mono|c0=c0" -ar 8000 -c:a pcm_s16le out.wav`, which
also picks a single channel out of a stereo call recording.

## Configuration

```python
TranscriptionConfig(
    model="hi-banking-v2-8khz",       # required
    sample_rate=8000,                 # match your audio: 8000 for telephony
    parse_number=True,                # "तीन लाख अस्सी हजार" -> "380000"
    exclude_partial=True,             # finals only, less traffic
    endpoint_silence_duration=0.6,    # trailing silence before an utterance ends
    hotwords=[Hotword("बजाज फिनसर्व", 2.5), Hotword("सुविधा")],
    aux=True,                         # timing metadata for latency debugging
)
```

| Field | Default | Notes |
|---|---|---|
| `model` | required | e.g. `hi-banking-v2-8khz`; banking or general, per language |
| `sample_rate` | `8000` | must match the audio |
| `parse_number` | `False` | spoken numbers become numerals |
| `exclude_partial` | `False` | stop the server sending partial results |
| `endpoint_silence_duration` | server default `0.44` | seconds, clamped to 0.44–1.2; higher merges utterances, lower cuts turns sooner |
| `hotwords` | — | boost domain phrases; score ~2–2.5 for multi-word phrases |
| `aux` | `False` | adds `request_time`, `eot_wait_time`, `processed_audio_duration` |

## What you get back

Every response carries the text and its metadata:

```python
response.type                        # "partial" or "complete"
response.text                        # the transcript
response.segment_meta.confidence     # 0.887 — utterance level
response.segment_meta.words[0].word  # "आपने"
response.segment_meta.words[0].confidence
response.segment_meta.timestamps     # per-token times
```

Confidence is reported on finished utterances, so read it inside the
`type == "complete"` branch above. Clean speech typically scores 0.87–0.93;
around 0.5 is a sensible floor for discarding noise.

## Live audio

For a microphone, a phone call or any live source, drive the session yourself
instead of handing over a file:

```python
await client.start_connection(config=config)

while True:
    chunk = get_audio()            # 16-bit PCM, e.g. 20 ms at a time
    if not chunk:
        break
    await client.send_audio_stream(chunk)

await client.close_connection()
```

Transcripts arrive on the same event handlers throughout.

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

### Text-to-speech

Bodhi's TTS fills the other end of the same pipeline:

```bash
pip install "bodhi-api-sdk[tts]"
```

```python
from pipecat.transcriptions.language import Language

from bodhi.integrations.pipecat_tts import BodhiTTSService

tts = BodhiTTSService(
    api_key=os.environ["BODHI_API_KEY"],
    language=Language.HI,
    voice="default_female",        # or default_male, or a voice you have added
)
```

Ten languages, two built-in voices each, and audio at 8, 16 or 24 kHz — the
service asks for whatever rate your pipeline runs at. Synthesis streams back
chunk by chunk, so playback starts before the whole utterance is generated.

Because Bodhi's TTS has no cancel command, the service is built on Pipecat's
`InterruptibleTTSService`: when the user barges in, it reconnects rather than
leaving stale audio in flight. Interruption handling therefore behaves as it
does with any other Pipecat TTS service.

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
