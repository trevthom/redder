"""Low-level X11 gamma-ramp control for Redder.

The XRandR CLI refuses a zero gamma factor, so this module talks to libXrandr
through ctypes and writes the 16-bit color lookup table directly.  That is what
lets the green and blue channels reach a real zero.
"""

from __future__ import annotations

import base64
import ctypes
import math
import os
import re
import shutil
import struct
import subprocess
import threading
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    c_char_p,
    c_int,
    c_ubyte,
    c_ulong,
    c_ushort,
    c_void_p,
)


class GammaError(RuntimeError):
    """Raised when the display's gamma table cannot be read or written."""


class _XRRCrtcGamma(Structure):
    _fields_ = [
        ("size", c_int),
        ("red", POINTER(c_ushort)),
        ("green", POINTER(c_ushort)),
        ("blue", POINTER(c_ushort)),
    ]


class _XRRScreenResources(Structure):
    _fields_ = [
        ("timestamp", c_ulong),
        ("configTimestamp", c_ulong),
        ("ncrtc", c_int),
        ("crtcs", POINTER(c_ulong)),
        ("noutput", c_int),
        ("outputs", POINTER(c_ulong)),
        ("nmode", c_int),
        ("modes", c_void_p),
    ]


class _XErrorEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("display", c_void_p),
        ("resourceid", c_ulong),
        ("serial", c_ulong),
        ("error_code", c_ubyte),
        ("request_code", c_ubyte),
        ("minor_code", c_ubyte),
    ]


_ERROR_HANDLER = CFUNCTYPE(c_int, c_void_p, c_void_p)
_libs_lock = threading.Lock()
_libs: tuple[ctypes.CDLL, ctypes.CDLL] | None = None
_last_x_error: tuple[int, int, int] | None = None


def _on_x_error(_display: c_void_p, event_ptr: c_void_p) -> int:
    """Swallow X protocol errors and remember them for the next check."""
    global _last_x_error
    if event_ptr:
        event = ctypes.cast(event_ptr, POINTER(_XErrorEvent)).contents
        _last_x_error = (int(event.error_code), int(event.request_code), int(event.minor_code))
    return 0


_x_error_callback = _ERROR_HANDLER(_on_x_error)


def _load_libs() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    global _libs
    with _libs_lock:
        if _libs is not None:
            return _libs

        try:
            x11 = ctypes.CDLL("libX11.so.6")
            xrandr = ctypes.CDLL("libXrandr.so.2")
        except OSError as exc:
            raise GammaError(
                "Redline needs the X11 and XRandR libraries (libX11.so.6 and libXrandr.so.2)."
            ) from exc

        x11.XInitThreads.argtypes = []
        x11.XInitThreads.restype = c_int
        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XOpenDisplay.restype = c_void_p
        x11.XDefaultRootWindow.argtypes = [c_void_p]
        x11.XDefaultRootWindow.restype = c_ulong
        x11.XSync.argtypes = [c_void_p, c_int]
        x11.XSync.restype = c_int
        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XCloseDisplay.restype = c_int
        x11.XSetErrorHandler.argtypes = [c_void_p]
        x11.XSetErrorHandler.restype = c_void_p

        xrandr.XRRGetScreenResourcesCurrent.argtypes = [c_void_p, c_ulong]
        xrandr.XRRGetScreenResourcesCurrent.restype = c_void_p
        xrandr.XRRFreeScreenResources.argtypes = [c_void_p]
        xrandr.XRRFreeScreenResources.restype = None
        xrandr.XRRGetCrtcGammaSize.argtypes = [c_void_p, c_ulong]
        xrandr.XRRGetCrtcGammaSize.restype = c_int
        xrandr.XRRGetCrtcGamma.argtypes = [c_void_p, c_ulong]
        xrandr.XRRGetCrtcGamma.restype = POINTER(_XRRCrtcGamma)
        xrandr.XRRAllocGamma.argtypes = [c_int]
        xrandr.XRRAllocGamma.restype = POINTER(_XRRCrtcGamma)
        xrandr.XRRSetCrtcGamma.argtypes = [c_void_p, c_ulong, POINTER(_XRRCrtcGamma)]
        xrandr.XRRSetCrtcGamma.restype = None
        xrandr.XRRFreeGamma.argtypes = [POINTER(_XRRCrtcGamma)]
        xrandr.XRRFreeGamma.restype = None

        if not x11.XInitThreads():
            raise GammaError("Could not initialise thread-safe X11 access.")

        # Must be installed before any request is made.  Returning 0 keeps
        # Xlib's default handler from terminating this process on a BadValue.
        x11.XSetErrorHandler(ctypes.cast(_x_error_callback, c_void_p))
        _libs = (x11, xrandr)
        return _libs


def query_outputs() -> list[dict]:
    """Return the connected outputs reported by xrandr, if it is installed."""
    if not shutil.which("xrandr"):
        return []
    try:
        result = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    outputs: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != "connected":
            continue
        geometry = next(
            (part for part in parts[2:] if re.match(r"^\d+x\d+\+\d+\+\d+$", part)), ""
        )
        outputs.append(
            {
                "name": parts[0],
                "resolution": geometry.split("+", 1)[0] if geometry else "",
                "primary": "primary" in parts,
            }
        )
    return outputs


class GammaController:
    """Read, save, and write the color lookup table on every XRandR CRTC."""

    def __init__(self, capture_originals: bool = True) -> None:
        self._lock = threading.RLock()
        self._x11: ctypes.CDLL | None = None
        self._xrandr: ctypes.CDLL | None = None
        self._display: c_void_p | None = None
        self._crtcs: list[tuple[c_ulong, int]] = []
        self._originals: dict[c_ulong, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = {}
        self._percent = 0.0
        self._closed = False
        self._open(capture_originals)

    # -- setup ---------------------------------------------------------

    def _open(self, capture_originals: bool) -> None:
        if not os.environ.get("DISPLAY"):
            raise GammaError(
                "No X11 display found. Redline currently requires an X11 session "
                "(launch it from your desktop session, not a remote TTY)."
            )

        x11, xrandr = _load_libs()
        display = x11.XOpenDisplay(None)
        if not display:
            raise GammaError(
                "Could not open display :0. Check that you are in an X11 desktop session."
            )

        resources = None
        try:
            self._x11 = x11
            self._xrandr = xrandr
            self._display = display

            root = x11.XDefaultRootWindow(display)
            resources = xrandr.XRRGetScreenResourcesCurrent(display, root)
            if not resources:
                raise GammaError("The X server did not return any screen resources.")
            screen = _XRRScreenResources.from_address(resources)

            for index in range(screen.ncrtc):
                crtc = screen.crtcs[index]
                size = xrandr.XRRGetCrtcGammaSize(display, crtc)
                if size <= 0:
                    continue

                gamma = xrandr.XRRGetCrtcGamma(display, crtc)
                if not gamma:
                    continue
                try:
                    red = tuple(int(gamma.contents.red[i]) for i in range(size))
                    green = tuple(int(gamma.contents.green[i]) for i in range(size))
                    blue = tuple(int(gamma.contents.blue[i]) for i in range(size))
                finally:
                    xrandr.XRRFreeGamma(gamma)

                self._crtcs.append((crtc, size))
                if capture_originals:
                    self._originals[crtc] = (red, green, blue)

            if not self._crtcs:
                raise GammaError("This display does not expose any XRandR gamma tables.")
        except Exception:
            x11.XCloseDisplay(display)
            self._display = None
            raise
        finally:
            if resources:
                xrandr.XRRFreeScreenResources(resources)

    # -- low-level helpers --------------------------------------------

    def _require_open(self) -> None:
        if self._closed or not self._display or not self._xrandr or not self._x11:
            raise GammaError("The display connection is closed.")

    @staticmethod
    def _clear_error() -> None:
        global _last_x_error
        _last_x_error = None

    def _sync(self) -> None:
        assert self._x11 is not None and self._display
        self._x11.XSync(self._display, 0)
        if _last_x_error is not None:
            code, request, minor = _last_x_error
            self._clear_error()
            raise GammaError(
                f"The X server rejected the gamma update "
                f"(error {code}, request {request}.{minor})."
            )

    def _apply_ramps(
        self,
        ramps: dict[c_ulong, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]],
    ) -> None:
        """Write several ramps, keeping their buffers alive until XSync."""
        self._require_open()
        assert self._xrandr is not None and self._display is not None
        allocated = []
        self._clear_error()
        try:
            for crtc, size in self._crtcs:
                ramp = ramps.get(crtc)
                if ramp is None:
                    continue
                red, green, blue = ramp
                if len(red) != size or len(green) != size or len(blue) != size:
                    raise GammaError("Gamma table size changed while updating the display.")
                gamma = self._xrandr.XRRAllocGamma(size)
                if not gamma:
                    raise GammaError("Could not allocate a gamma lookup table.")
                allocated.append(gamma)
                for index in range(size):
                    gamma.contents.red[index] = max(0, min(65535, red[index]))
                    gamma.contents.green[index] = max(0, min(65535, green[index]))
                    gamma.contents.blue[index] = max(0, min(65535, blue[index]))
                self._xrandr.XRRSetCrtcGamma(self._display, crtc, gamma)
            self._sync()
        finally:
            if self._xrandr is not None:
                for gamma in allocated:
                    self._xrandr.XRRFreeGamma(gamma)

    def _restore_originals(self) -> None:
        if self._originals:
            self._apply_ramps(dict(self._originals))
            self._percent = 0.0

    # -- public operations --------------------------------------------

    def apply_percent(self, percent: float) -> float:
        """Preserve red and attenuate green/blue by ``percent``.

        At 0 the original ramps are written back exactly. At 100 green and
        blue are written as literal zeroes, leaving only red subpixel output.
        """
        if not math.isfinite(percent):
            raise GammaError("The isolation percentage must be a finite number.")
        percent = max(0.0, min(100.0, float(percent)))
        if not self._originals:
            raise GammaError("No saved color table is available to scale.")

        with self._lock:
            self._require_open()
            factor = (100.0 - percent) / 100.0
            ramps = {}
            for crtc, _size in self._crtcs:
                red, green, blue = self._originals[crtc]
                ramps[crtc] = (
                    red,
                    tuple(int(round(value * factor)) for value in green),
                    tuple(int(round(value * factor)) for value in blue),
                )
            try:
                self._apply_ramps(ramps)
            except GammaError:
                # Never knowingly leave a half-written display behind.
                try:
                    self._restore_originals()
                except GammaError:
                    pass
                raise
            self._percent = percent
            return self._percent

    def restore(self) -> None:
        with self._lock:
            self._require_open()
            self._restore_originals()

    def apply_identity(self) -> None:
        """Write a standard linear 1.0 ramp; used by the crash-recovery flag."""
        with self._lock:
            self._require_open()
            ramps = {}
            for crtc, size in self._crtcs:
                if size < 2:
                    continue
                ramp = tuple(round(65535 * index / (size - 1)) for index in range(size))
                ramps[crtc] = (ramp, ramp, ramp)
            self._apply_ramps(ramps)
            self._percent = 0.0

    def export_originals(self) -> dict:
        """Return the captured pre-Redder ramps as a JSON-safe snapshot."""
        with self._lock:
            self._require_open()
            if not self._originals:
                raise GammaError("No original color table has been captured.")
            crtcs = []
            for crtc, size in self._crtcs:
                red, green, blue = self._originals[crtc]
                crtcs.append(
                    {
                        "crtc": int(crtc),
                        "size": size,
                        "red": base64.b64encode(struct.pack(f"<{size}H", *red)).decode("ascii"),
                        "green": base64.b64encode(
                            struct.pack(f"<{size}H", *green)
                        ).decode("ascii"),
                        "blue": base64.b64encode(struct.pack(f"<{size}H", *blue)).decode("ascii"),
                    }
                )
            return {"version": 1, "crtcs": crtcs}

    def apply_snapshot(self, snapshot: dict) -> int:
        """Restore a snapshot from :meth:`export_originals` and return CRTC count."""
        if not isinstance(snapshot, dict):
            raise GammaError("The saved color table is not a JSON object.")
        entries = snapshot.get("crtcs")
        if not isinstance(entries, list):
            raise GammaError("The saved color table has no CRTC entries.")

        by_id: dict[int, dict] = {}
        for entry in entries:
            if isinstance(entry, dict):
                try:
                    by_id[int(entry["crtc"])] = entry
                except (KeyError, TypeError, ValueError):
                    continue

        with self._lock:
            self._require_open()
            ramps = {}
            for crtc, size in self._crtcs:
                entry = by_id.get(int(crtc))
                if not entry or int(entry.get("size", -1)) != size:
                    continue
                try:
                    red = struct.unpack(
                        f"<{size}H", base64.b64decode(entry["red"], validate=True)
                    )
                    green = struct.unpack(
                        f"<{size}H", base64.b64decode(entry["green"], validate=True)
                    )
                    blue = struct.unpack(
                        f"<{size}H", base64.b64decode(entry["blue"], validate=True)
                    )
                except Exception:
                    continue
                ramps[crtc] = (red, green, blue)

            if not ramps:
                raise GammaError(
                    "The saved color table does not match any gamma table on this display."
                )
            self._apply_ramps(ramps)
            self._percent = 0.0
            return len(ramps)

    def close(self, restore: bool = True) -> None:
        """Restore the saved table and close the display connection once."""
        with self._lock:
            if self._closed:
                return
            try:
                if restore and self._display:
                    try:
                        self._restore_originals()
                    except Exception:
                        pass
            finally:
                if self._display:
                    assert self._x11 is not None
                    self._x11.XCloseDisplay(self._display)
                    self._display = None
                self._closed = True

    # -- read-only properties -----------------------------------------

    @property
    def percent(self) -> float:
        return self._percent

    @property
    def crtc_count(self) -> int:
        return len(self._crtcs)

    def channel_percentages(self) -> dict[str, float]:
        factor = 100.0 - self._percent
        return {"red": 100.0, "green": factor, "blue": factor}

    def __enter__(self) -> "GammaController":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()
