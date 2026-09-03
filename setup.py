from setuptools import setup, find_packages

setup(
    name="bodhi-sdk",
    version="1.4.0",
    packages=["bodhi", "bodhi.utils", "bodhi.integrations", "bodhi.examples"],
    install_requires=[
        "requests",
        "aiohttp",
    ],
    extras_require={
        # The Pipecat integration; needs Python 3.10+, like Pipecat itself.
        "pipecat": ["pipecat-ai>=1.4"],
    },
    python_requires=">=3.7",
    author="Navana",
    description="Bodhi Python SDK for Streaming Speech Recognition",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/navana-ai/bodhi-python-sdk",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
