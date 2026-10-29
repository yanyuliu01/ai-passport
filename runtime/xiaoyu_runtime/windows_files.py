"""Open a regular Windows file without traversing a reparse path.

Verify the final handle's path and attributes before exposing bytes. No POSIX
O_NOFOLLOW fallback is simulated with a racy exists()/open() sequence.
"""
import ctypes
import os
from ctypes import wintypes

def open_regular(path, limit):
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final.restype = wintypes.DWORD
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    expected = os.path.abspath(path)
    handle = create(expected, 0x80000000, 1, None, 3, 0x00200000 | 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot open regular file")
    transferred = False
    try:
        name = ctypes.create_unicode_buffer(32768)
        count = final(handle, name, len(name), 0)
        attributes = (wintypes.DWORD * 2)()
        if not count or count >= len(name) or not info(handle, 9, attributes, ctypes.sizeof(attributes)):
            raise OSError("Cannot inspect file handle")
        actual = name.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        elif actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if attributes[0] & (0x400 | 0x10) or os.path.normcase(actual) != os.path.normcase(expected):
            raise OSError("Reparse paths and directories are not allowed")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        transferred = True
        stream = os.fdopen(descriptor, "rb")
        if os.fstat(descriptor).st_size > limit:
            stream.close()
            raise OSError("File exceeds size limit")
        return stream
    finally:
        if not transferred:
            close(handle)
