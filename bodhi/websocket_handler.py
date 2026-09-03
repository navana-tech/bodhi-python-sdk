"""WebSocket Handler Module for Bodhi Client"""

import asyncio
import json
import ssl
from typing import Any, Dict, Optional, Union

import aiohttp

from bodhi.utils.error_utils import make_error_response, BodhiErrors

from .utils.logger import logger
from .utils.exceptions import (
    BodhiAPIError,
    InvalidJSONError,
    WebSocketTimeoutError,
    WebSocketError,
)
from .transcription_response import TranscriptionResponse, SegmentMeta, Word
from .events import LiveTranscriptionEvents


class EventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event, listener):
        # Allow only one listener per event — replace if exists
        self._listeners[event] = listener

    def off(self, event):
        # Remove the listener for the event
        if event in self._listeners:
            del self._listeners[event]

    async def emit(self, event, *args, **kwargs):
        if event in self._listeners:
            listener = self._listeners[event]
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
        self.session = None
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
            "headers": request_headers,
        }
        if "wss://" in self.websocket_url:
            connect_kwargs["ssl"] = self.ssl_context

        logger.info("Establishing WebSocket connection")
        self.session = aiohttp.ClientSession()
        try:
            ws = await self.session.ws_connect(self.websocket_url, **connect_kwargs)
            return ws
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {str(e)}")
            raise e

    async def send_config(self, ws: Any, config: dict) -> None:
        """Send configuration to WebSocket.

        Args:
            ws: WebSocket connection
            config: Configuration dictionary
        """
        await ws.send_str(json.dumps({"config": config}))

    async def process_transcription_stream(
        self,
        ws: Any,
    ) -> None:
        """Process transcription stream from WebSocket.

        Args:
            ws: WebSocket connection
        """
        while True:
            try:
                if ws.closed or ws.exception():
                    await self.emit(LiveTranscriptionEvents.Close)
                msg = await asyncio.wait_for(ws.receive(), timeout=30.0)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    response = msg.data
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    await self.emit(LiveTranscriptionEvents.Close)
                    return
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error = make_error_response(
                        message="WebSocket connection closed with error",
                        code=BodhiErrors.ClientClosed.value,
                    )
                    await self.emit(
                        LiveTranscriptionEvents.Error,
                        WebSocketError(json.dumps(error)),
                    )
                    raise error
                else:
                    continue
                response_data = json.loads(response)

                if response_data.get("error"):
                    error = BodhiAPIError(json.dumps(response_data))
                    await self.emit(LiveTranscriptionEvents.Error, error)

                    # Cancel any ongoing tasks
                    for task in asyncio.all_tasks():
                        if task != asyncio.current_task():
                            task.cancel()
                    await ws.close()
                    raise error

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
                    language_code=response_data.get("language_code"),
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
                    try:
                        if not ws.closed:
                            await ws.close()
                            if hasattr(self, "session"):
                                await self.session.close()
                            logger.info("WebSocket connection closed")
                        await self.emit(LiveTranscriptionEvents.Close)
                        return
                    except (aiohttp.ClientError, Exception) as e:
                        logger.error(f"Error during WebSocket closure: {str(e)}")
                        error_msg = make_error_response(
                            message=str(e),
                            code=BodhiErrors.InternalServerError.value,
                        )
                        await self.emit(
                            LiveTranscriptionEvents.Error,
                            WebSocketError(json.dumps(error_msg)),
                        )
            except json.JSONDecodeError as e:
                await self.emit(LiveTranscriptionEvents.Close)
                error_msg = make_error_response(
                    message="Received invalid JSON response",
                    code=BodhiErrors.InternalServerError.value,
                )
                await self.emit(
                    LiveTranscriptionEvents.Error,
                    InvalidJSONError(json.dumps(error_msg)),
                )
                try:
                    if not ws.closed:
                        await ws.close()
                        if hasattr(self, "session"):
                            await self.session.close()
                        logger.error("WebSocket connection closed due to JSON error")
                except Exception as close_error:
                    error_msg = make_error_response(
                        message=str(close_error),
                        code=BodhiErrors.ClientClosed.value,
                    )
                    await self.emit(
                        LiveTranscriptionEvents.Error,
                        WebSocketError(json.dumps(error_msg)),
                    )
                error_msg = make_error_response(
                    message="Received invalid JSON response",
                    code=BodhiErrors.InternalServerError.value,
                )
                raise InvalidJSONError(json.dumps(error_msg))
            except aiohttp.ClientError as e:
                error_msg = make_error_response(
                    message=str(e),
                    code=BodhiErrors.InternalServerError.value,
                )
                await self.emit(
                    LiveTranscriptionEvents.Error, BodhiAPIError(json.dumps(error_msg))
                )
                try:
                    if not ws.closed:
                        await ws.close()
                        if hasattr(self, "session"):
                            await self.session.close()
                except Exception as close_error:
                    logger.error(f"Error during WebSocket closure: {str(close_error)}")
                await self.emit(LiveTranscriptionEvents.Close)
                return
            except asyncio.TimeoutError:
                error_msg = make_error_response(
                    message="WebSocket connection timed out",
                    code=BodhiErrors.GatewayTimeout.value,
                )
                await self.emit(
                    LiveTranscriptionEvents.Error,
                    WebSocketTimeoutError(json.dumps(error_msg)),
                )
                try:
                    if not ws.closed:
                        await ws.close()
                        if hasattr(self, "session"):
                            await self.session.close()
                except Exception as close_error:
                    logger.error(f"Error during WebSocket closure: {str(close_error)}")
                await self.emit(LiveTranscriptionEvents.Close)
                return
            except Exception as e:
                try:
                    if not ws.closed:
                        await ws.close()
                        if hasattr(self, "session"):
                            await self.session.close()
                except Exception as close_error:
                    logger.error(f"Error during WebSocket closure: {str(close_error)}")
                await self.emit(LiveTranscriptionEvents.Close)
