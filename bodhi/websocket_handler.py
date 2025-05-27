"""WebSocket Handler Module for Bodhi Client"""

import asyncio
import json
import ssl
import websockets
from typing import Any, Callable, Optional

# import EventEmitter

from .utils.logger import logger
from .utils.exceptions import (
    BodhiAPIError,
    InvalidJSONError,
    WebSocketConnectionClosedError,
    WebSocketTimeoutError,
    WebSocketError,
    ModelNotAvailableError,
    MissingModelError,
    InvalidTransactionIDError,
    MissingTransactionIDError,
)
from .transcription_response import TranscriptionResponse, SegmentMeta, Word
from .events import LiveTranscriptionEvents


class EventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event, listener):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(listener)

    def off(self, event, listener):
        if event in self._listeners and listener in self._listeners[event]:
            self._listeners[event].remove(listener)

    async def emit(self, event, *args, **kwargs):
        if event in self._listeners:
            for listener in self._listeners[event]:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(*args, **kwargs)
                    else:
                        listener(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in event listener for {event}: {e}")


class WebSocketHandler(EventEmitter):
    def __init__(self, api_key: str, customer_id: str, websocket_url: str):
        """Initialize WebSocket handler.

        Args:
            api_key: API key for authentication
            customer_id: Customer ID for authentication
            websocket_url: WebSocket URI for the service
        """
        super().__init__()
        self.api_key = api_key
        self.customer_id = customer_id
        self.websocket_url = websocket_url
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.last_segment_id = None

    async def connect(self) -> Any:
        """Establish WebSocket connection.

        Returns:
            WebSocket connection object
        """
        request_headers = {
            "x-api-key": self.api_key,
            "x-customer-id": self.customer_id,
        }

        connect_kwargs = {
            "extra_headers": request_headers,
        }
        if "wss://" in self.websocket_url:
            connect_kwargs["ssl"] = self.ssl_context

        logger.info("Establishing WebSocket connection")
        return await websockets.connect(self.websocket_url, **connect_kwargs)

    async def send_config(self, ws: Any, config: dict) -> None:
        """Send configuration to WebSocket.

        Args:
            ws: WebSocket connection
            config: Configuration dictionary
        """
        await ws.send(json.dumps({"config": config}))

    async def process_transcription_stream(
        self,
        ws: Any,
    ) -> list:
        """Process transcription stream from WebSocket.

        Args:
            ws: WebSocket connection

        Returns:
            List of complete transcribed sentences
        """
        complete_sentences = []
        while True:
            try:
                if ws.closed:
                    await self.emit(LiveTranscriptionEvents.Close)
                    return complete_sentences
                response = await asyncio.wait_for(ws.recv(), timeout=30.0)
                response_data = json.loads(response)

                if response_data.get("error"):
                    e = response_data.get("error")
                    error = None
                    if "transaction_id is missing" in str(e):
                        error = MissingTransactionIDError(e)
                    elif "invalid transaction_id" in str(e):
                        error = InvalidTransactionIDError(e)
                    elif "model is missing" in str(e):
                        error = MissingModelError(e)
                    elif "model '" in str(e) and "' is not available" in str(e):
                        error = ModelNotAvailableError(e)
                    else:
                        error = BodhiAPIError(e)
                    await self.emit(LiveTranscriptionEvents.Error, error)
                    # Cancel any ongoing tasks
                    for task in asyncio.all_tasks():
                        if task != asyncio.current_task():
                            task.cancel()
                    return complete_sentences

                socket_response = TranscriptionResponse(
                    call_id=response_data["call_id"],
                    segment_id=response_data["segment_id"],
                    eos=response_data["eos"],
                    type=response_data["type"],
                    text=response_data["text"],
                    segment_meta=SegmentMeta(
                        tokens=response_data["segment_meta"]["tokens"],
                        timestamps=response_data["segment_meta"]["timestamps"],
                        start_time=response_data["segment_meta"]["start_time"],
                        confidence=(
                            response_data["segment_meta"].get("confidence")
                            if "segment_meta" in response_data
                            else None
                        ),
                        words=[
                            Word(word=w.get("word", ""), confidence=w.get("confidence"))
                            for w in response_data.get("segment_meta", {}).get(
                                "words", []
                            )
                        ],
                    ),
                )

                # Emit SpeechStarted if segment_id changes
                if (
                    self.last_segment_id is None
                    or socket_response.segment_id != self.last_segment_id
                ) and socket_response.text != "":
                    await self.emit(
                        LiveTranscriptionEvents.SpeechStarted,
                        socket_response.segment_meta.start_time,
                    )
                    self.last_segment_id = socket_response.segment_id

                # Emit events based on response type
                await self.emit(LiveTranscriptionEvents.Transcript, socket_response)

                if socket_response.type == "complete":
                    complete_sentences.append(socket_response.text)
                    end_time = round(
                        socket_response.segment_meta.start_time
                        + (
                            socket_response.segment_meta.timestamps[-1]
                            if socket_response.segment_meta.timestamps
                            else 0
                        ),
                        2,
                    )
                    await self.emit(
                        LiveTranscriptionEvents.UtteranceEnd,
                        {
                            "start_time": socket_response.segment_meta.start_time,
                            "end_time": end_time,
                        },
                    )

                if socket_response.eos:
                    if not ws.closed:
                        try:
                            await ws.close()
                            logger.info("WebSocket connection closed")
                            await self.emit(LiveTranscriptionEvents.Close)
                            return complete_sentences
                        except websockets.exceptions.ConnectionClosedError:
                            pass
            except json.JSONDecodeError as e:
                await self.emit(LiveTranscriptionEvents.Close)
                await self.emit(
                    LiveTranscriptionEvents.Error,
                    InvalidJSONError("Received invalid JSON response"),
                )
                try:
                    if not ws.closed:
                        await ws.close()
                        logger.error("WebSocket connection closed due to JSON error")
                except Exception as close_error:
                    await self.emit(LiveTranscriptionEvents.Error, close_error)
                raise InvalidJSONError("Received invalid JSON response")
            except websockets.exceptions.ConnectionClosedError as e:
                await self.emit(LiveTranscriptionEvents.Error, e)
                await self.emit(LiveTranscriptionEvents.Close)
                return complete_sentences
            except asyncio.TimeoutError:
                await self.emit(
                    LiveTranscriptionEvents.Error,
                    WebSocketTimeoutError("WebSocket connection timed out"),
                )
                if not ws.closed:
                    await ws.close()
                await self.emit(LiveTranscriptionEvents.Close)
                return complete_sentences
            except Exception as e:
                await self.emit(
                    LiveTranscriptionEvents.Error,
                    WebSocketError(f"An unexpected WebSocket error occurred: {e}"),
                )
                if not ws.closed:
                    await ws.close()
                await self.emit(LiveTranscriptionEvents.Close)
                raise WebSocketError(f"An unexpected WebSocket error occurred: {e}")
                return complete_sentences
