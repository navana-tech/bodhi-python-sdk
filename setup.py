from setuptools import setup, find_packages

setup(
    name="bodhi-api-sdk",
    version="1.0.1",
    packages=["bodhi", "bodhi.utils", "bodhi.integrations", "bodhi.examples"],
    install_requires=[
        "requests",
        "aiohttp",
    ],
    extras_require={
        # Pipecat integrations; both need Python 3.10+, like Pipecat itself.
        # They install the same dependency today, and are named per service so
        # the install string stays stable if one of them later needs more.
        "stt": ["pipecat-ai>=1.4"],
        "tts": ["pipecat-ai>=1.4"],
        # Alias, so instructions that predate the split keep working.
        "pipecat": ["pipecat-ai>=1.4"],
    },
    python_requires=">=3.7",
    author="Navana",
    description="Bodhi API Python SDK for Streaming Speech Recognition",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/navana-tech/bodhi-python-sdk",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
