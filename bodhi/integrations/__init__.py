"""Integrations that plug Bodhi into third-party frameworks.

Each module here needs its framework installed; nothing in this package is
imported by the core SDK. For Pipecat::

    pip install "bodhi-sdk[pipecat]"

    from bodhi.integrations.pipecat_stt import BodhiSTTService
"""
