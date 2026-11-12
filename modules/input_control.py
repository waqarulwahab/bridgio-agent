"""
Input control + Virtual Desktop manager.

Virtual desktop flow:
  1. Agent starts → _create_virtual_desktop() called once
  2. A new Windows desktop "BridgioRC" is created via CreateDesktopW
  3. Agent thread switches to that desktop
  4. screen.py captures only that desktop region
  5. Physical user stays on their own desktop — ZERO interference
  6. Mouse/keyboard events are injected into the virtual desktop only
"""
import ctypes
import ctypes.wintypes as wt
import logging
import sys

logger = logging.getLogger(__name__)

try:
    from pynput.mouse    import Controller as MouseController, Button
    from pynput.keyboard import Controller as KeyboardController, Key
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    logger.warning("pynput not available — input control disabled. pip install pynput")

# ── Windows API helpers ───────────────────────────────────────────────────────
_u32 = ctypes.windll.user32 if sys.platform == "win32" else None

# ── Virtual desktop state ─────────────────────────────────────────────────────
_vdesk       = None   # HDESK of our virtual desktop
_orig_desk   = None   # HDESK of the original desktop (to restore on exit)
_mouse       = None
_keyboard    = None


def _get_mouse():
    global _mouse
    if _mouse is None and PYNPUT_AVAILABLE:
        _mouse = MouseController()
    return _mouse


def _get_keyboard():
    global _keyboard
    if _keyboard is None and PYNPUT_AVAILABLE:
        _keyboard = KeyboardController()
    return _keyboard


# ── Virtual desktop creation ──────────────────────────────────────────────────
def create_virtual_desktop() -> bool:
    """
    Create a new Windows desktop 'BridgioRC' and switch THIS thread to it.
    The physical user stays on their original desktop — independent sessions.
    Returns True on success.
    """
    global _vdesk, _orig_desk

    if sys.platform != "win32":
        logger.warning("virtual desktop only supported on Windows")
        return False

    try:
        # save current desktop so we can restore later
        _orig_desk = _u32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP

        # create new desktop
        _vdesk = _u32.CreateDesktopW(
            "BridgioRC",   # name
            None,          # device
            None,          # devmode
            0,             # flags
            0x01FF,        # GENERIC_ALL access mask
            None           # security attrs
        )
        if not _vdesk:
            err = ctypes.get_last_error()
            logger.error("CreateDesktopW failed: error=%d", err)
            return False

        # switch THIS thread to the new desktop
        _u32.SetThreadDesktop(_vdesk)

        # launch explorer.exe on the virtual desktop so it has a proper shell
        import subprocess
        si = subprocess.STARTUPINFO()
        si.lpDesktop = "BridgioRC"
        subprocess.Popen(["explorer.exe"], startupinfo=si, close_fds=True)

        logger.info("virtual desktop 'BridgioRC' created — physical user undisturbed")

        # tell screen module about the new desktop
        from modules.screen import set_virtual_desktop
        set_virtual_desktop(_vdesk)

        return True

    except Exception as e:
        logger.error("create_virtual_desktop error: %s", e)
        return False


def destroy_virtual_desktop():
    """Restore original desktop and close virtual one."""
    global _vdesk, _orig_desk
    if sys.platform != "win32":
        return
    try:
        if _orig_desk:
            _u32.SwitchDesktop(_orig_desk)
            _u32.SetThreadDesktop(_orig_desk)
            _u32.CloseDesktop(_orig_desk)
            _orig_desk = None
        if _vdesk:
            _u32.CloseDesktop(_vdesk)
            _vdesk = None
        logger.info("virtual desktop destroyed — restored original")
    except Exception as e:
        logger.error("destroy_virtual_desktop error: %s", e)


def set_cursor_visible(visible: bool):
    """Show/hide the mouse cursor."""
    if sys.platform != "win32":
        return
    try:
        _u32.ShowCursor(visible)
        logger.debug("cursor visible=%s", visible)
    except Exception as e:
        logger.warning("cursor visibility error: %s", e)


# ── Input handling ────────────────────────────────────────────────────────────
def handle_input(action: dict):
    """Handle mouse/keyboard action from the dashboard viewer."""
    if not PYNPUT_AVAILABLE:
        return
    try:
        atype = action.get("type")

        if atype == "mousemove":
            _get_mouse().position = (action["x"], action["y"])

        elif atype in ("mousedown", "mouseup", "click"):
            btn = Button.right if action.get("button") == "right" else Button.left
            m = _get_mouse()
            m.position = (action["x"], action["y"])
            if atype == "mousedown":
                m.press(btn)
            elif atype == "mouseup":
                m.release(btn)
            else:
                m.click(btn)

        elif atype == "dblclick":
            m = _get_mouse()
            m.position = (action["x"], action["y"])
            m.click(Button.left, 2)

        elif atype == "scroll":
            _get_mouse().scroll(action.get("dx", 0), -action.get("dy", 0))

        elif atype == "keydown":
            _press_key(action.get("key", ""), press=True)

        elif atype == "keyup":
            _press_key(action.get("key", ""), press=False)

        elif atype == "text":
            _get_keyboard().type(action.get("text", ""))

    except Exception as e:
        logger.error("input error: %s | action: %s", e, action)


def _press_key(key_str: str, press: bool):
    """Map JS key names to pynput Key and press/release."""
    _KEY_MAP = {
        "Enter": Key.enter, "Backspace": Key.backspace, "Tab": Key.tab,
        "Escape": Key.esc,  "Delete": Key.delete,       "ArrowUp": Key.up,
        "ArrowDown": Key.down, "ArrowLeft": Key.left,   "ArrowRight": Key.right,
        "Home": Key.home,   "End": Key.end,             "PageUp": Key.page_up,
        "PageDown": Key.page_down, "F1": Key.f1,        "F2": Key.f2,
        "F3": Key.f3,       "F4": Key.f4,               "F5": Key.f5,
        "F6": Key.f6,       "F7": Key.f7,               "F8": Key.f8,
        "F9": Key.f9,       "F10": Key.f10,             "F11": Key.f11,
        "F12": Key.f12,     "Control": Key.ctrl,        "Shift": Key.shift,
        "Alt": Key.alt,     "Meta": Key.cmd,            " ": Key.space,
    }
    k = _KEY_MAP.get(key_str) or (key_str[0] if len(key_str) == 1 else None)
    if k is None:
        return
    kb = _get_keyboard()
    if press:
        kb.press(k)
    else:
        kb.release(k)
