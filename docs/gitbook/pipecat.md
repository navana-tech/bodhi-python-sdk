# Pipecat

[Pipecat](https://github.com/pipecat-ai/pipecat) is an open-source Python framework for real-time voice agents. A bot is a pipeline of processors, and Bodhi drops into the speech-to-text slot:

```
transport.input() → STT → LLM → TTS → transport.output()
```

Pipecat handles VAD, interruptions and turn taking. `BodhiSTTService` handles the listening.

***

### :rocket: Install

```bash
pip install "bodhi-sdk[pipecat]"
```

Needs `pipecat-ai` 1.4 or later and Python 3.10 or later. If you pin `pipecat-ai` below 1.4, install plain `bodhi-sdk` instead so pip leaves your pin alone — the same import still works.

***

### :zap: Quickstart

```python
import os

from pipecat.pipeline.pipeline import Pipeline

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

stt = BodhiSTTService(
    api_key=os.environ["BODHI_API_KEY"],
    customer_id=os.environ["BODHI_CUSTOMER_ID"],
    model="hi-banking-v2-8khz",
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

That is the whole integration. If you are moving from another provider, delete their STT line and paste this one — nothing else in your bot changes.

***

### :level_slider: Configuration

Set when you create the service:

| Parameter          | Default                | Purpose                                                                       |
| ------------------ | ---------------------- | ----------------------------------------------------------------------------- |
| `api_key`          | required               | Sent as the `x-api-key` header                                                |
| `customer_id`      | required               | Your customer UUID, sent as `x-customer-id`                                   |
| `model`           | required                | Any Bodhi ASR model — see the list below                         |
| `url`              | `wss://bodhi.navana.ai`| Point at your own deployment if you run one                                   |
| `interim_results`  | `True`                 | Emit partials. Turning it off also stops the server sending them              |
| `min_confidence`   | `0.5`                  | Drop finals below this confidence. `0` keeps everything                       |
| `aux`              | `False`                | Attach latency metadata to each result                                        |
| `transaction_id`   | a fresh UUID           | Correlates the session with your Bodhi logs and billing                       |

Recognition settings, passed through `BodhiSTTService.Settings`:

| Setting                     | Purpose                                                                    |
| --------------------------- | -------------------------------------------------------------------------- |
| `hotwords`                  | Boost domain phrases — see [Advanced Features](../quickstart/streaming-websocket/advanced-features.md)       |
| `parse_number`              | Convert spoken numbers to numerals                                         |
| `endpoint_silence_duration` | Trailing silence before an utterance is finalised (0.44–1.2s)              |

```python
stt = BodhiSTTService(
    api_key=os.environ["BODHI_API_KEY"],
    customer_id=os.environ["BODHI_CUSTOMER_ID"],
    model="hi-banking-v2-8khz",
    settings=BodhiSTTService.Settings(
        parse_number=True,
        endpoint_silence_duration=0.6,
        hotwords=[BodhiHotword("बजाज फिनसर्व", 2.5), BodhiHotword("सुविधा")],
    ),
)
```

#### Models you can pass

Model names follow `<language>-<general|banking>-v2-8khz`. All models are bilingual with English and handle code-switching, except English, Gujarati and Odia.

| Language                 | General               | Banking               |
| ------------------------ | --------------------- | --------------------- |
| Bengali                  | `bn-general-v2-8khz`  | `bn-banking-v2-8khz`  |
| English *(en-IN)*        | `en-general-v2-8khz`  | `en-banking-v2-8khz`  |
| Gujarati                 | `gu-general-v2-8khz`  | `gu-banking-v2-8khz`  |
| Hindi                    | `hi-general-v2-8khz`  | `hi-banking-v2-8khz`  |
| Hinglish (Hindi–English) | `hi-en-general-v2-8khz` | `hi-en-banking-v2-8khz` |
| Kannada                  | `kn-general-v2-8khz`  | `kn-banking-v2-8khz`  |
| Malayalam                | `ml-general-v2-8khz`  | `ml-banking-v2-8khz`  |
| Marathi                  | `mr-general-v2-8khz`  | `mr-banking-v2-8khz`  |
| Odia                     | `or-general-v3-8khz`  | *(TBD)*               |
| Tamil                    | `ta-general-v2-8khz`  | `ta-banking-v2-8khz`  |
| Telugu                   | `te-general-v2-8khz`  | `te-banking-v2-8khz`  |

Use a banking model for financial conversations — account numbers, EMIs, policy terms — and a general model otherwise. See [Bodhi Overview](/pages/DEhLxI5Cvi4ED5EhSslR) for the current list and language notes.

> Settings can also be changed while the bot is running, through `STTUpdateSettingsFrame`. Bodhi accepts one configuration per connection, so the service reconnects to apply them — which starts a new `transaction_id`, and cuts short whatever utterance was in flight. Apply changes between turns, and configure at construction where you can.

***

### :repeat: What Bodhi sends, what Pipecat sees

| Bodhi                                          | Pipecat                                    |
| ---------------------------------------------- | ------------------------------------------ |
| `type: "partial"`                              | `InterimTranscriptionFrame`                |
| `type: "complete"` (endpointed utterance)      | `TranscriptionFrame`, marked finalised     |
| the whole message                              | the frame's `result` field                 |
| `{"error": ..., "message": ...}`               | `ErrorFrame` pushed upstream               |

Bodhi decides when an utterance is final; your pipeline's VAD still drives interruptions, exactly as with any other Pipecat STT service.

Word timings and confidence ride along on every final, with no extra flag:

```python
segment = frame.result["segment_meta"]
segment["confidence"]   # 0.87 — utterance level
segment["words"][0]     # {"word": "आपने", "confidence": 0.873,
                        #  "start_time": 0.32, "end_time": 0.48}
```

Set `aux=True` to add `request_time`, `eot_wait_time` and `processed_audio_duration` for latency work — see [Measuring Latency](../quickstart/streaming-websocket/measuring-latency.md).

***

### :telephone_receiver: Telephony and sample rates

Bodhi models are served at 8 kHz and 16 kHz. If your pipeline runs at either, audio passes through untouched; at any other rate it is resampled to 16 kHz for you, so a 24 kHz browser pipeline and an 8 kHz phone call both work without configuration.

> Two operational notes for production calls. Bodhi closes a connection that has been idle for 15 seconds, so the service sends silence to hold it open — relevant when a call sits on hold or the bot mutes the user. And each reconnect starts a new `transaction_id`, so a call that reconnects appears as more than one record in your logs.

***

### :test_tube: Try it in five minutes

Two runnable examples ship with the SDK. Neither needs an LLM or a text-to-speech key — just your API key and customer ID from the dashboard (see [Bodhi Overview](/pages/DEhLxI5Cvi4ED5EhSslR)) and Python 3.10 or later.

```bash
pip install "bodhi-sdk[pipecat]"

export BODHI_API_KEY=your-api-key
export BODHI_CUSTOMER_ID=your-customer-id
```

#### 1. Transcribe your microphone

```bash
pip install "pipecat-ai[webrtc,silero,runner]"
python -m bodhi.examples.pipecat_mic_bot
```

Open [http://localhost:7860/client](http://localhost:7860/client), click **Connect**, allow microphone access, and start talking. Transcripts appear in the browser and in your terminal. There is no LLM or text-to-speech in this pipeline, so nothing talks back — it is the shortest path to hearing Bodhi transcribe your own voice inside Pipecat.

Set `BODHI_MODEL` to pick a different model, and `BODHI_HOTWORDS` (comma-separated, `phrase:score`) to bias recognition.

> On macOS the WebRTC dependencies print a wall of `objc[...] Class AVFFrameReceiver is implemented in both...` warnings on startup. They come from duplicate ffmpeg libraries inside opencv and PyAV, and are harmless.

#### 2. Transcribe a recording

```bash
python -m bodhi.examples.pipecat_stream_wav call.wav --model hi-banking-v2-8khz
```

Any mono 16-bit WAV works — convert with `ffmpeg -i in.wav -ac 1 -ar 8000 -sample_fmt s16 out.wav`. The file is streamed at real-time speed through a real Pipecat pipeline, so partials and finals arrive with the timing a live call would see:

```
streaming 23.0s of 16000 Hz audio to wss://bodhi.navana.ai as hi-banking-v2-8khz
[  6.89s] FINAL   आपने हाल ही में कोई नया इन्वेस्टमेंट प्लान देखा है क्या
[ 10.32s] FINAL   digital banking के युग में
[ 15.89s] FINAL   आप किस ऐप को बैंकिंग के लिए सबसे ज्यादा पसंद करते हैं
transaction_id: 46138098-f5d6-4b38-a685-c7877cc7f06e
```

Add `--parse-number` for numerals, `--hotword "बजाज फिनसर्व:2.5"` to boost a phrase, or `--no-partials` for finals only.

For a full talking bot, follow the [Pipecat quickstart](https://docs.pipecat.ai/pipecat/get-started/quickstart) and replace its speech-to-text service with `BodhiSTTService`.

***

### :speech_balloon: Support

Questions and issues: [github.com/navana-tech/bodhi-python-sdk](https://github.com/navana-tech/bodhi-python-sdk) or support@navanatech.in.
