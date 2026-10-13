#
# Bodhi (Navana Tech) plugin for LiveKit Agents.
#
# Licensed under the Apache License, Version 2.0.
#

"""Bodhi plugin for LiveKit Agents.

Indian-language speech-to-text and text-to-speech from Bodhi (Navana Tech),
exposed as LiveKit ``stt.STT`` / ``tts.TTS`` providers::

    from livekit.plugins import bodhi

    session = AgentSession(
        stt=bodhi.STT(model="hi-general-v2-8khz", sample_rate=8000),
        tts=bodhi.TTS(language="hi", voice="default_female"),
        # ... vad, llm
    )

Set ``BODHI_API_KEY`` in the environment, or pass ``api_key=`` to either class.
Both default to the Navana hosts (``stt.navana.ai`` / ``tts.navana.ai``); pass
``base_url=`` to point at another deployment.

The implementation lives in the ``bodhi-api-sdk`` package; this plugin re-exports
it under the ``livekit.plugins.bodhi`` namespace so it installs and imports the
same way as every other LiveKit provider plugin.
"""

try:
    from bodhi.integrations.livekit_stt import (
        BodhiHotword,
        BodhiSTT as STT,
        SpeechStream,
    )
    from bodhi.integrations.livekit_tts import (
        BodhiTTS as TTS,
        ChunkedStream,
        SynthesizeStream,
    )
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "livekit-plugins-bodhi needs bodhi-api-sdk with its LiveKit integration. "
        'Install with: pip install "livekit-plugins-bodhi" '
        "(or upgrade bodhi-api-sdk)."
    ) from e

from .version import __version__

__all__ = [
    "STT",
    "TTS",
    "BodhiHotword",
    "SpeechStream",
    "ChunkedStream",
    "SynthesizeStream",
    "__version__",
]

from livekit.agents import Plugin

from .log import logger


class BodhiPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(BodhiPlugin())

# Hide re-exported helper modules from generated docs.
_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}
for _n in NOT_IN_ALL:
    __pdoc__[_n] = False
