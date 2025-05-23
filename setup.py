from setuptools import setup, find_packages

setup(
    name="bodhi-sdk",
    version="0.1.3",
    packages=["bodhi", "bodhi.utils"],
    install_requires=[
        "requests",
        "websockets<=12.0",
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
