from __future__ import annotations
import io
import threading
from typing import Optional

import cv2
import numpy as np


class CameraStream:
    """
    USB camera wrapper using OpenCV (V4L2) with a background capture thread
    that shares the latest frame with multiple consumers (MJPEG, WebRTC).
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        framerate: int = 24,
        device: int = 0,
    ):
        self._cap = cv2.VideoCapture(device)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Could not open camera device {device}. "
                "Check that the USB camera is connected and try /dev/video0, 1, ..."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, framerate)
        self.width = width
        self.height = height
        self.framerate = framerate
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> "CameraStream":
        self._running = True
        threading.Thread(
            target=self._capture_loop, daemon=True, name="camera-capture"
        ).start()
        return self

    def _capture_loop(self) -> None:
        while self._running:
            ret, bgr = self._cap.read()
            if ret:
                # cv2 returns BGR — convert to RGB for Pillow / aiortc
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                with self._lock:
                    self._frame = rgb

    def get_frame(self) -> Optional[np.ndarray]:
        """Return the latest frame as an RGB numpy array."""
        with self._lock:
            return self._frame

    def get_jpeg(self, quality: int = 85) -> Optional[bytes]:
        """Return the latest frame encoded as JPEG bytes."""
        from PIL import Image

        frame = self.get_frame()
        if frame is None:
            return None
        buf = io.BytesIO()
        Image.fromarray(frame).save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def stop(self) -> None:
        self._running = False
        self._cap.release()
