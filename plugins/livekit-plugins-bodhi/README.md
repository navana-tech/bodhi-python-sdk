# LiveKit Agents plugin for Bodhi

Indian-language speech-to-text and text-to-speech from [Bodhi](https://www.navanatech.in/)
(Navana Tech), as LiveKit Agents `stt.STT` / `tts.TTS` providers.

## Install

```bash
pip install "livekit-plugins-bodhi"
```

## Usage

```python
from livekit.agents import AgentSession
from livekit.plugins import bodhi, silero

session = AgentSession(
    stt=bodhi.STT(model="hi-general-v2-8khz", sample_rate=8000),
    vad=silero.VAD.load(),
    # llm=...,
    tts=bodhi.TTS(language="hi", voice="default_female"),
)
```

Set `BODHI_API_KEY` in the environment, or pass `api_key=` to either class.

Both default to the Navana hosts (`wss://stt.navana.ai` / `https://tts.navana.ai`);
pass `base_url=` to target another deployment.

### STT options

`model`, `language`, `sample_rate`, `interim_results`, `parse_number`, `aux`,
`min_confidence`, `hotwords` (`bodhi.BodhiHotword(phrase, score)`),
`endpoint_silence_duration`.

### TTS options

`voice`, `language`, `sample_rate` (8000/16000/24000), `use_fast`, `num_step`,
`guidance_scale`.

## License

Apache-2.0. The implementation ships in the
[`bodhi-api-sdk`](https://github.com/navana-tech/bodhi-python-sdk) package; this
plugin re-exports it under the `livekit.plugins.bodhi` namespace.
