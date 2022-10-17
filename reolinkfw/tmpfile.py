import os


class TempFile(os.PathLike):

    def __init__(self, filebytes):
        self._filebytes = filebytes
        self._fd = -1

    def __enter__(self):
        self._fd = self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __fspath__(self):
        return f"/proc/self/fd/{self._fd}"
    
    def open(self):
        self._fd = os.memfd_create("temp")
        os.write(self._fd, self._filebytes)
        return self._fd
    
    def close(self):
        os.close(self._fd)
        self._fd = -1