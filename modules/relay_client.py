"""
WebSocket relay client — connects to Bridgio server, streams frames, forwards input.
URL comes from config.json (default: ws://localhost:8000/ws/rc-device).
"""
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class RCRelayClient:
    def __init__(self, relay_url: str, device_id: str,
                 quality: int = 50, fps: int = 15):
        self.relay_url   = relay_url
        self.device_id   = device_id
        self.quality     = quality
        self.fps         = fps
        self.status      = "disconnected"
        self._stop_event = asyncio.Event()
        self._ws         = None
        self._input_handler = None
        self._screen_task   = None

    def set_input_handler(self, handler):
        self._input_handler = handler

    def stop(self):
        self._stop_event.set()
        if self._screen_task:
            self._screen_task.cancel()

    async def start(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package not installed — run: pip install websockets")
            return

        url = f"{self.relay_url}?device_id={self.device_id}"
        logger.info("connecting to relay: %s", url)

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                    self._ws = ws
                    self.status = "connected"
                    logger.info("relay connected: %s", url)

                    # start screen streaming in background
                    self._screen_task = asyncio.ensure_future(self._stream_frames(ws))

                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                            mtype = msg.get("type")

                            if mtype == "input" and self._input_handler:
                                self._input_handler(msg.get("action", {}))

                            elif mtype == "virtual_desktop":
                                from modules.input_control import create_virtual_desktop
                                ok = create_virtual_desktop()
                                logger.info("virtual_desktop requested — result: %s", ok)

                            elif mtype == "viewer_count":
                                count = msg.get("count", 0)
                                logger.info("viewer_count=%d", count)
                                from modules.input_control import set_cursor_visible
                                set_cursor_visible(count == 0)

                        except Exception as e:
                            logger.error("message error: %s", e)

            except Exception as e:
                self.status = "disconnected"
                self._ws = None
                if self._screen_task:
                    self._screen_task.cancel()
                logger.warning("relay disconnected: %s — retrying in 5s", e)
                await asyncio.sleep(5)

    async def _stream_frames(self, ws):
        from modules.screen import capture_frame
        interval = 1.0 / max(1, self.fps)
        while not self._stop_event.is_set():
            try:
                frame_b64 = capture_frame(quality=self.quality)
                if frame_b64:
                    await ws.send(json.dumps({"type": "frame", "data": frame_b64}))
            except Exception as e:
                logger.error("frame send error: %s", e)
            await asyncio.sleep(interval)
