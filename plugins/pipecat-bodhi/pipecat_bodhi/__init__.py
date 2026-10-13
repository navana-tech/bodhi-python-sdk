#
# Bodhi (Navana Tech) services for Pipecat.
#
# Licensed under the Apache License, Version 2.0.
#

"""Bodhi plugin for Pipecat.

Indian-language speech-to-text and text-to-speech from Bodhi (Navana Tech), as
Pipecat services::

    from pipecat_bodhi import BodhiSTTService, BodhiTTSService

    stt = BodhiSTTService(api_key=..., model="hi-general-v2-8khz")
    tts = BodhiTTSService(api_key=..., language=Language.HI, voice="default_female")

The implementation lives in the ``bodhi-api-sdk`` package; this package
re-exports it so Pipecat users can ``pip install pipecat-bodhi`` and import the
services directly.
"""

try:
    from bodhi.integrations.pipecat_stt import (
        BodhiHotword,
        BodhiSTTService,
        BodhiSTTSettings,
    )
    from bodhi.integrations.pipecat_tts import (
        BodhiTTSService,
        BodhiTTSSettings,
    )
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "pipecat-bodhi needs bodhi-api-sdk with its Pipecat integration, plus "
        'pipecat-ai. Install with: pip install "pipecat-bodhi".'
    ) from e

from .version import __version__

__all__ = [
    "BodhiSTTService",
    "BodhiTTSService",
    "BodhiSTTSettings",
    "BodhiTTSSettings",
    "BodhiHotword",
    "__version__",
]
