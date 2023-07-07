import hashlib
import io
from contextlib import contextmanager
from functools import partial
from os import scandir
from pathlib import Path
from shutil import disk_usage
from tempfile import gettempdir as _gettempdir
from zipfile import is_zipfile

from pakler import PAK, is_pak_file
from ubireader.ubi import ubi
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.utils import guess_peb_size

from reolinkfw.tmpfile import TempFile

ONEMIB = 1024**2
ONEGIB = 1024**3


class DummyLEB:
    """A class that emulates ubireader's `leb_virtual_file`."""

    def __init__(self, fd):
        self._fd = fd
        self._last_read_addr = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self._fd.close()

    def read(self, size):
        self._last_read_addr = self._fd.tell()
        return self._fd.read(size)

    def reset(self):
        return self._fd.seek(0)

    def seek(self, offset):
        return self._fd.seek(offset)

    def last_read_addr(self):
        """Start address of last physical file read."""
        return self._last_read_addr

    @classmethod
    def from_bytes(cls, bytes_):
        return cls(io.BytesIO(bytes_))


@contextmanager
def closing_ubifile(ubifile):
    try:
        yield ubifile
    finally:
        ubifile._fhandle.close()


def get_fs_from_ubi(fd, size, offset=0) -> bytes:
    """Return the first file system that sits on top of the UBI volume."""
    fd.seek(offset)
    binbytes = fd.read(size)
    with TempFile(binbytes) as t:
        block_size = guess_peb_size(t)
        with closing_ubifile(ubi_file(t, block_size)) as ubifile:
            ubi_obj = ubi(ubifile)
            vol_blocks = ubi_obj.images[0].volumes.popitem()[1].get_blocks(ubi_obj.blocks)
            return b''.join(leb_virtual_file(ubi_obj, vol_blocks).reader())


def sha256_pak(pak: PAK) -> str:
    sha = hashlib.sha256()
    pak._fd.seek(0)
    for block in iter(partial(pak._fd.read, ONEMIB), b''):
        sha.update(block)
    return sha.hexdigest()


def dir_size(path):
    size = 0
    try:
        with scandir(path) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    size += dir_size(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    size += entry.stat().st_size
    except OSError:
        pass
    return size


def gettempdir() -> Path:
    return Path(_gettempdir()) / "reolinkfwcache"


def get_cache_file(url: str) -> Path:
    file = gettempdir() / hashlib.sha256(url.encode("utf8")).hexdigest()
    if is_zipfile(file) or is_pak_file(file):
        return file
    try:
        with open(file, 'r', encoding="utf8") as f:
            return gettempdir() / f.read(256)
    except (OSError, UnicodeDecodeError):
        return file


def has_cache(url: str) -> bool:
    return get_cache_file(url).is_file()


def make_cache_file(url: str, filebytes, name=None) -> bool:
    tempdir = gettempdir()
    tempdir.mkdir(exist_ok=True)
    if disk_usage(tempdir).free < ONEGIB or dir_size(tempdir) > ONEGIB:
        return False
    sha = hashlib.sha256(url.encode("utf8")).hexdigest()
    name = sha if not isinstance(name, str) else name
    try:
        with open(tempdir / name, "wb") as f:
            f.write(filebytes)
        if name != sha:
            with open(tempdir / sha, 'w', encoding="utf8") as f:
                f.write(name)
    except OSError:
        return False
    return True
