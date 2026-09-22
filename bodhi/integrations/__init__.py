"""Integrations that plug Bodhi into third-party frameworks.

Each module here needs its framework installed; nothing in this package is
imported by the core SDK. For Pipecat::

    pip install "bodhi-api-sdk[stt]"      # speech-to-text
    pip install "bodhi-api-sdk[tts]"      # text-to-speech
    pip install "bodhi-api-sdk[stt,tts]"  # both

    from bodhi.integrations.pipecat_stt import BodhiSTTService
    from bodhi.integrations.pipecat_tts import BodhiTTSService
"""
