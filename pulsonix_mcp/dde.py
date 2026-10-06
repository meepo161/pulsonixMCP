"""Minimal DDEML client (Unicode) for sending XTYP_EXECUTE to a running Pulsonix.

Pulsonix registers DDE service "Pulsonix", topic "System" and accepts
``[psx_script_file("<path>")]`` (see the .psf shell association).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)

APPCMD_CLIENTONLY = 0x00000010
CP_WINUNICODE = 1200
XTYP_EXECUTE = 0x4050
DMLERR_NO_ERROR = 0
DMLERR_EXECACKTIMEOUT = 0x4005
DMLERR_NO_CONV_ESTABLISHED = 0x400A

HDDEDATA = wintypes.HANDLE
HSZ = wintypes.HANDLE
HCONV = wintypes.HANDLE
ULONG_PTR = ctypes.c_size_t

PFNCALLBACK = ctypes.WINFUNCTYPE(
    HDDEDATA, wintypes.UINT, wintypes.UINT, HCONV, HSZ, HSZ, HDDEDATA, ULONG_PTR, ULONG_PTR
)

_user32.DdeInitializeW.argtypes = [ctypes.POINTER(wintypes.DWORD), PFNCALLBACK, wintypes.DWORD, wintypes.DWORD]
_user32.DdeInitializeW.restype = wintypes.UINT
_user32.DdeCreateStringHandleW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_int]
_user32.DdeCreateStringHandleW.restype = HSZ
_user32.DdeFreeStringHandle.argtypes = [wintypes.DWORD, HSZ]
_user32.DdeConnect.argtypes = [wintypes.DWORD, HSZ, HSZ, ctypes.c_void_p]
_user32.DdeConnect.restype = HCONV
_user32.DdeClientTransaction.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, HCONV, HSZ, wintypes.UINT, wintypes.UINT, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.DdeClientTransaction.restype = HDDEDATA
_user32.DdeGetLastError.argtypes = [wintypes.DWORD]
_user32.DdeGetLastError.restype = wintypes.UINT
_user32.DdeDisconnect.argtypes = [HCONV]
_user32.DdeUninitialize.argtypes = [wintypes.DWORD]


class DdeError(RuntimeError):
    pass


@PFNCALLBACK
def _callback(*_args):  # client-only: nothing to handle
    return 0


def is_server_available(service: str = "Pulsonix", topic: str = "System") -> bool:
    try:
        with _Session() as s:
            conv = s.connect(service, topic)
            _user32.DdeDisconnect(conv)
            return True
    except DdeError:
        return False


def execute(command: str, service: str = "Pulsonix", topic: str = "System", timeout_ms: int = 60000) -> bool:
    """Send an XTYP_EXECUTE. Returns True if acknowledged, False on ack timeout.

    Raises DdeError if no conversation could be established.
    """
    with _Session() as s:
        conv = s.connect(service, topic)
        try:
            buf = ctypes.create_unicode_buffer(command)
            res = wintypes.DWORD(0)
            ok = _user32.DdeClientTransaction(
                buf, ctypes.sizeof(buf), conv, None, 0, XTYP_EXECUTE, timeout_ms, ctypes.byref(res)
            )
            if ok:
                return True
            err = _user32.DdeGetLastError(s.inst)
            if err == DMLERR_EXECACKTIMEOUT:
                return False
            raise DdeError(f"DDE execute failed, DMLERR=0x{err:04X}")
        finally:
            _user32.DdeDisconnect(conv)


class _Session:
    def __init__(self) -> None:
        self.inst = wintypes.DWORD(0)
        self._hsz: list[HSZ] = []

    def __enter__(self) -> "_Session":
        rc = _user32.DdeInitializeW(ctypes.byref(self.inst), _callback, APPCMD_CLIENTONLY, 0)
        if rc != DMLERR_NO_ERROR:
            raise DdeError(f"DdeInitialize failed: 0x{rc:04X}")
        return self

    def connect(self, service: str, topic: str) -> HCONV:
        hs = _user32.DdeCreateStringHandleW(self.inst, service, CP_WINUNICODE)
        ht = _user32.DdeCreateStringHandleW(self.inst, topic, CP_WINUNICODE)
        self._hsz += [hs, ht]
        conv = _user32.DdeConnect(self.inst, hs, ht, None)
        if not conv:
            raise DdeError(f"No DDE server '{service}|{topic}' (is Pulsonix running?)")
        return conv

    def __exit__(self, *exc) -> None:
        for h in self._hsz:
            _user32.DdeFreeStringHandle(self.inst, h)
        _user32.DdeUninitialize(self.inst)
