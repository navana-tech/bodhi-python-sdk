# Pipecat services for Bodhi

Indian-language speech-to-text and text-to-speech from [Bodhi](https://www.navanatech.in/)
(Navana Tech), as Pipecat services.

## Install

```bash
pip install "pipecat-bodhi"
```

## Usage

```python
from pipecat.transcriptions.language import Language
from pipecat_bodhi import BodhiSTTService, BodhiTTSService, BodhiHotword

stt = BodhiSTTService(
    api_key=...,                       # or set BODHI_API_KEY
    model="hi-general-v2-8khz",
    hotwords=[BodhiHotword("बजाज फिनसर्व", 2.0)],
)

tts = BodhiTTSService(
    api_key=...,
    language=Language.HI,
    voice="default_female",
)
```

Both default to the Navana hosts (`wss://stt.navana.ai` / `wss://tts.navana.ai/v1`);
pass `url=` to target another deployment.

## License

Apache-2.0. The implementation ships in the
[`bodhi-api-sdk`](https://github.com/navana-tech/bodhi-python-sdk) package; this
package re-exports it for Pipecat users.
