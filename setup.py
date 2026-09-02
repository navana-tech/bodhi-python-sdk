from setuptools import setup, find_packages

setup(
    name="bodhi-sdk",
    version="1.2.0",
    packages=["bodhi", "bodhi.utils"],
    install_requires=[
        "requests",
        "aiohttp",
    ],
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
