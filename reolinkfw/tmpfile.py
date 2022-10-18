import os
import platform
import sys
import tempfile
from abc import abstractmethod


class TempFileFromBytes(os.PathLike):

    def __init__(self, filebytes):
        self._filebytes = filebytes
        self._fd = -1
        self._path = None

    def __enter__(self):
        self.open()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
    
    def __fspath__(self):
        return self._path
    
    @abstractmethod
    def open(self):
        ...
    
    @abstractmethod
    def close(self):
        ...


class LinuxInMemoryFile(TempFileFromBytes):

    def open(self):
        self._fd = os.memfd_create("temp")
        os.write(self._fd, self._filebytes)
        self._path = f"/proc/self/fd/{self._fd}"
        return self._fd
    
    def close(self):
        os.close(self._fd)
        self._fd = -1
        self._path = None


class OnDiskTempFile(TempFileFromBytes):
    
    def open(self):
        self._fd, self._path = tempfile.mkstemp()
        os.write(self._fd, self._filebytes)
        return self._fd

    def close(self):
        os.close(self._fd)
        os.unlink(self._path)
        self._fd = -1
        self._path = None


if sys.platform.startswith("linux") and platform.libc_ver()[1] >= "2.27":
    TempFile = LinuxInMemoryFile
else:
    TempFile = OnDiskTempFile
