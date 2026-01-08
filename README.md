# Bodhi Python SDK

Bodhi Python SDK provides a client for Navana's streaming speech recognition API.

## Installation

```bash
pip install bodhi-sdk
```

## Usage

To use the Bodhi Python SDK, follow these steps:

1.  **Installation:**
    Install the SDK using pip:

    ```bash
    pip install bodhi-sdk
    ```

2.  **Initialization:**
    Create a `BodhiClient` instance with your API key and customer ID:

    ```python
    from bodhi import BodhiClient

    client = BodhiClient(api_key="YOUR_API_KEY", customer_id="YOUR_CUSTOMER_ID")
    ```

3.  **Transcription:**
    Use the client methods to transcribe audio. The SDK supports transcription from local files, remote URLs, and streams.

    - **Local File Transcription:**

      ```python
      config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=False,    # Enable language identification at start (default: False)
        transliterate=False,   # Enable transliteration output (default: False)
      )
      response = client.transcribe_local_file(audio_file_path, config=config)
      print(response.text)
      ```

    - **Remote URL Transcription:**

      ```python
      config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=False,    # Enable language identification at start (default: False)
        transliterate=False,   # Enable transliteration output (default: False)
      )
      response = client.transcribe_remote_url("http://example.com/audio.wav", config)
      print(response.text)
      ```

    - **Streaming Transcription:**
      Refer to the examples for detailed instructions on setting up streaming transcription.

4.  **Event Handling:**
    You can register event listeners to handle different stages of the transcription process using the `client.on` method and the `LiveTranscriptionEvents` enum. This is particularly useful for streaming and remote URL transcriptions where events are emitted asynchronously.

    ```python
    from bodhi import LiveTranscriptionEvents

    async def on_transcript(response):
        print(f"Transcript: {response.text}")

    async def on_utterance_end(response):
        print(f"UtteranceEnd: {response}")

    async def on_speech_started(response):
        print(f"SpeechStarted: {response}")

    async def on_error(e):
        print(f"Error: {str(e)}")

    client.on(LiveTranscriptionEvents.Transcript, on_transcript)
    client.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    client.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
    client.on(LiveTranscriptionEvents.Error, on_error)
    ```

    Common events include:

    - `LiveTranscriptionEvents.Transcript`: Emitted when a new transcription segment is available.
    - `LiveTranscriptionEvents.UtteranceEnd`: Emitted when an utterance is detected as complete.
    - `LiveTranscriptionEvents.SpeechStarted`: Emitted when speech activity is detected.
    - `LiveTranscriptionEvents.Error`: Emitted when an error occurs during transcription.
    - `LiveTranscriptionEvents.Close`: Emitted when the WebSocket connection is closed.

For complete code examples and detailed usage instructions for various scenarios, please refer to the [official documentation](https://navana.gitbook.io/bodhi).
