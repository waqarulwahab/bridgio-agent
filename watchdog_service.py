"""
BridgioWatchdog — Windows Service that keeps BridgioAgent.exe running.

Runs as SYSTEM. Launches BridgioAgent.exe inside the active user session
(via CreateProcessAsUser) so screen capture / input control work correctly.
Restarts the agent automatically whenever it crashes or is killed.

Commands (run as Administrator):
  BridgioWatchdog.exe install   — register as Windows service
  BridgioWatchdog.exe start     — start the service
  BridgioWatchdog.exe stop      — stop the service
  BridgioWatchdog.exe remove    — uninstall
  net start BridgioWatchdog     — start via sc/net
"""
import sys
import time
import ctypes
import ctypes.wintypes as wt
import logging
import logging.handlers
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE_DIR  = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path(__file__).parent
_AGENT_EXE = str(_BASE_DIR / "BridgioAgent.exe")
_LOG_DIR   = _BASE_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging (file only — service has no console)
# ---------------------------------------------------------------------------
_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s")
_fh  = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "watchdog.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_fh.setFormatter(_fmt)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(_fh)
logger = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# Win32 helpers — launch process in the interactive user session
# ---------------------------------------------------------------------------
_kernel32 = ctypes.windll.kernel32
_advapi32 = ctypes.windll.advapi32
_wtsapi32 = ctypes.windll.wtsapi32
_userenv  = ctypes.windll.userenv

class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb",              wt.DWORD),
        ("lpReserved",      wt.LPWSTR),
        ("lpDesktop",       wt.LPWSTR),
        ("lpTitle",         wt.LPWSTR),
        ("dwX",             wt.DWORD),
        ("dwY",             wt.DWORD),
        ("dwXSize",         wt.DWORD),
        ("dwYSize",         wt.DWORD),
        ("dwXCountChars",   wt.DWORD),
        ("dwYCountChars",   wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags",         wt.DWORD),
        ("wShowWindow",     wt.WORD),
        ("cbReserved2",     wt.WORD),
        ("lpReserved2",     ctypes.POINTER(wt.BYTE)),
        ("hStdInput",       wt.HANDLE),
        ("hStdOutput",      wt.HANDLE),
        ("hStdError",       wt.HANDLE),
    ]

class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess",    wt.HANDLE),
        ("hThread",     wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId",  wt.DWORD),
    ]

_TOKEN_ALL_ACCESS       = 0xF01FF
_SecurityImpersonation  = 2
_TokenPrimary           = 1
_CREATE_NO_WINDOW       = 0x08000000
_NORMAL_PRIORITY_CLASS  = 0x00000020
_CREATE_UNICODE_ENV     = 0x00000400
_STARTF_USESHOWWINDOW   = 0x0001
_SW_HIDE                = 0
_STILL_ACTIVE           = 259


def _launch_agent_in_user_session() -> wt.HANDLE:
    """
    Launches _AGENT_EXE inside the interactive user session.
    Returns the process HANDLE (to monitor later), or None on failure.
    """
    session_id = _kernel32.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        logger.warning("No active console session — will retry")
        return None

    h_token = wt.HANDLE(0)
    if not _wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
        logger.warning("WTSQueryUserToken failed (err=%d)", _kernel32.GetLastError())
        return None

    h_dup = wt.HANDLE(0)
    ok = _advapi32.DuplicateTokenEx(
        h_token,
        _TOKEN_ALL_ACCESS,
        None,
        _SecurityImpersonation,
        _TokenPrimary,
        ctypes.byref(h_dup),
    )
    _kernel32.CloseHandle(h_token)
    if not ok:
        logger.warning("DuplicateTokenEx failed (err=%d)", _kernel32.GetLastError())
        return None

    env = ctypes.c_void_p(0)
    _userenv.CreateEnvironmentBlock(ctypes.byref(env), h_dup, False)

    si = _STARTUPINFOW()
    si.cb        = ctypes.sizeof(si)
    si.lpDesktop = "winsta0\\default"
    si.dwFlags   = _STARTF_USESHOWWINDOW
    si.wShowWindow = _SW_HIDE

    pi    = _PROCESS_INFORMATION()
    flags = _CREATE_NO_WINDOW | _NORMAL_PRIORITY_CLASS | _CREATE_UNICODE_ENV

    work_dir = str(_BASE_DIR)
    success  = _advapi32.CreateProcessAsUserW(
        h_dup,
        None,
        _AGENT_EXE,
        None, None,
        False,
        flags,
        env,
        work_dir,
        ctypes.byref(si),
        ctypes.byref(pi),
    )

    _userenv.DestroyEnvironmentBlock(env)
    _kernel32.CloseHandle(h_dup)

    if not success:
        logger.error("CreateProcessAsUser failed (err=%d)", _kernel32.GetLastError())
        return None

    pid = pi.dwProcessId
    _kernel32.CloseHandle(pi.hThread)
    logger.info("Agent launched — PID %d", pid)
    return wt.HANDLE(pi.hProcess)


def _kill_existing_agents():
    """Kill any stray BridgioAgent.exe processes (e.g. mutex holders from manual runs)."""
    import subprocess
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "BridgioAgent.exe"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _process_alive(h_proc: wt.HANDLE) -> bool:
    """Returns True if the process handle is still running."""
    code = wt.DWORD(0)
    if not _kernel32.GetExitCodeProcess(h_proc, ctypes.byref(code)):
        return False
    return code.value == _STILL_ACTIVE


# ---------------------------------------------------------------------------
# Windows Service
# ---------------------------------------------------------------------------
import win32serviceutil
import win32service
import win32event
import servicemanager


class BridgioWatchdogService(win32serviceutil.ServiceFramework):
    _svc_name_         = "BridgioWatchdog"
    _svc_display_name_ = "Bridgio Agent Watchdog"
    _svc_description_  = (
        "Monitors and auto-restarts Bridgio Remote Control Agent. "
        "Launches agent in the interactive user session so screen capture works."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
        self._running  = True
        self._h_agent  = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        logger.info("Watchdog stop requested")
        self._running = False
        # Kill the agent process we launched
        if self._h_agent:
            try:
                _kernel32.TerminateProcess(self._h_agent, 0)
                _kernel32.CloseHandle(self._h_agent)
            except Exception:
                pass
            self._h_agent = None
        win32event.SetEvent(self._stop_evt)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        logger.info("BridgioWatchdog service started — watching %s", _AGENT_EXE)
        self._watch_loop()

    def _watch_loop(self):
        _kill_existing_agents()   # clear any stale instances before first launch
        while self._running:
            if self._h_agent is None:
                logger.info("Launching agent…")
                self._h_agent = _launch_agent_in_user_session()
                if self._h_agent is None:
                    logger.warning("Launch failed — retrying in 10s")
            else:
                if not _process_alive(self._h_agent):
                    logger.warning("Agent died — restarting in 3s")
                    _kernel32.CloseHandle(self._h_agent)
                    self._h_agent = None
                    win32event.WaitForSingleObject(self._stop_evt, 3000)
                    continue

            # Sleep 5 s, wake early if stop is requested
            win32event.WaitForSingleObject(self._stop_evt, 5000)

        logger.info("BridgioWatchdog stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Called by SCM — start the service dispatcher
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(BridgioWatchdogService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # CLI: install / remove / start / stop / etc.
        win32serviceutil.HandleCommandLine(BridgioWatchdogService)
