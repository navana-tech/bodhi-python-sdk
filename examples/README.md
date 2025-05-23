# Bodhi Python SDK Examples

This folder contains example scripts demonstrating how to use the Bodhi Python SDK.

## Prerequisites

1. Set your credentials as environment variables:

   ```bash
   export BODHI_API_KEY='your_api_key'
   export BODHI_CUSTOMER_ID='your_customer_id'
   ```

2. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Example Scripts

- `local_transcription.py`: Demonstrates local file transcription
- `remote_transcription.py`: Demonstrates remote URL transcription
- `streaming_transcription.py`: Demonstrates streaming transcription

## Running Examples

```bash
python local_transcription.py
python remote_transcription.py
python streaming_transcription.py
```

## Best Practices

- Never hardcode credentials in your code
- Handle errors appropriately
- Use environment variables or secure credential storage
- Examples now include basic logging for better error reporting and information output.
