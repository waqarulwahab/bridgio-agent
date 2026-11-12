"""
Screen capture module — captures the screen and streams as JPEG frames.
When virtual desktop mode is active, uses GDI BitBlt directly (bypasses
PIL ImageGrab which fails on non-interactive thread desktops).
"""
import base64
import ctypes
import ctypes.wintypes as wt
import logging
import sys
from io import BytesIO

logger = logging.getLogger(__name__)

try:
    from PIL import ImageGrab, Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not available — screen capture disabled. pip install pillow")

_virtual_desktop = None
_virtual_bounds  = None


def set_virtual_desktop(hdesk, bounds=None):
    global _virtual_desktop, _virtual_bounds
    _virtual_desktop = hdesk
    _virtual_bounds  = bounds
    logger.info("screen: virtual desktop set, bounds=%s", bounds)


def _capture_gdi(x=0, y=0, w=None, h=None) -> "Image.Image | None":
    """
    Capture the PHYSICAL screen via GDI.
    When the calling thread is on a virtual desktop (SetThreadDesktop was called),
    we temporarily switch back to the interactive desktop, capture, then switch back.
    This ensures we capture the actual monitor content, not an empty virtual surface.
    """
    try:
        user32 = ctypes.windll.user32
        gdi32  = ctypes.windll.gdi32

        if w is None:
            w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        if h is None:
            h = user32.GetSystemMetrics(1)   # SM_CYSCREEN

        # Switch thread to interactive desktop so GetDC(NULL) gives the physical screen
        h_input_desk = user32.OpenInputDesktop(0, False, 0x0100)
        if h_input_desk:
            user32.SetThreadDesktop(h_input_desk)

        hdc_screen = user32.GetDC(None)   # DC for thread's current (physical) desktop
        if not hdc_screen:
            return None

        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp    = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        SRCCOPY = 0x00CC0020
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY)

        # read bitmap pixels
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize",          ctypes.c_uint32),
                ("biWidth",         ctypes.c_int32),
                ("biHeight",        ctypes.c_int32),
                ("biPlanes",        ctypes.c_uint16),
                ("biBitCount",      ctypes.c_uint16),
                ("biCompression",   ctypes.c_uint32),
                ("biSizeImage",     ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed",       ctypes.c_uint32),
                ("biClrImportant",  ctypes.c_uint32),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize        = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth       = w
        bmi.biHeight      = -h   # negative = top-down row order
        bmi.biPlanes      = 1
        bmi.biBitCount    = 32
        bmi.biCompression = 0

        buf = (ctypes.c_char * (w * h * 4))()
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

        # cleanup GDI resources
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

        # switch thread back to virtual desktop if one is active
        if h_input_desk:
            user32.CloseDesktop(h_input_desk)
            if _virtual_desktop:
                user32.SetThreadDesktop(_virtual_desktop)

        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        return img.convert("RGB")

    except Exception as e:
        logger.error("GDI capture error: %s", e)
        return None


def capture_frame(quality: int = 50) -> "str | None":
    """Capture screen and return base64-encoded JPEG string."""
    if not PIL_AVAILABLE:
        return None
    try:
        if _virtual_desktop:
            # virtual desktop mode: PIL ImageGrab won't work on the switched thread
            # use GDI CreateDC('DISPLAY') which is display-level, not desktop-level
            if _virtual_bounds:
                x, y, r, b = _virtual_bounds
                img = _capture_gdi(x, y, r - x, b - y)
            else:
                img = _capture_gdi()
        else:
            if _virtual_bounds:
                img = ImageGrab.grab(bbox=_virtual_bounds)
            else:
                img = ImageGrab.grab()

        if img is None:
            return None

        # scale down to 1280px wide max
        max_w = 1280
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    except Exception as e:
        logger.error("screen capture error: %s", e)
        return None
