from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import Enum
from os import scandir
from pathlib import Path
from shutil import disk_usage
from tempfile import gettempdir as _gettempdir
from typing import Any, AnyStr, BinaryIO, Optional, Union
from zipfile import is_zipfile

from lz4.block import decompress as lz4_block_decompress
from pakler import Section, is_pak_file
from pycramfs.const import MAGIC_BYTES as CRAMFS_MAGIC
from PySquashfsImage.const import SQUASHFS_MAGIC
from ubireader.ubi import ubi
from ubireader.ubi.defines import UBI_EC_HDR_MAGIC as UBI_MAGIC
from ubireader.ubi_io import ubi_file
from ubireader.ubifs.defines import UBIFS_NODE_MAGIC as UBIFS_MAGIC
from ubireader.utils import guess_peb_size

from reolinkfw.tmpfile import TempFile
from reolinkfw.typedefs import Buffer, GenericPath

ONEMIB = 1024**2
ONEGIB = 1024**3


class FileType(Enum):
    CRAMFS = CRAMFS_MAGIC
    LZ4_LEGACY_FRAME = b"\x02!L\x18"
    SQUASHFS = SQUASHFS_MAGIC.to_bytes(4, "little")
    UBI = UBI_MAGIC
    UBIFS = UBIFS_MAGIC
    UIMAGE = 0x27051956.to_bytes(4, "big")

    @classmethod
    def from_magic(cls, key: bytes, default: Optional[Any] = None) -> Optional[FileType]:
        try:
            return cls(key)
        except ValueError:
            return default


class SectionFile(io.BufferedIOBase):

    def __init__(self, fd: BinaryIO, section: Section, close: Callable[[BinaryIO], None]) -> None:
        self._fd = fd
        self._close = close
        self._start = section.start
        self._end = section.start + section.len
        self._position = section.start

    def peek(self, size: int = 0, /) -> bytes:
        if self._fd is None:
            raise ValueError("peek from closed file")
        if not isinstance(size, int):
            raise ValueError("size must be an int")
        if self._position >= self._end or size == 0:
            return b''
        max_read = self._end - self._position
        if size < 0 or size > max_read:
            size = max_read
        self._fd.seek(self._position)
        data = self._fd.read(size)
        return data

    def read(self, size: Optional[int] = -1, /) -> bytes:
        if self._fd is None:
            raise ValueError("read from closed file")
        if self._position >= self._end or size == 0:
            return b''
        max_read = self._end - self._position
        if size is None or size < 0 or size > max_read:
            size = max_read
        self._fd.seek(self._position)
        data = self._fd.read(size)
        self._position = self._fd.tell()
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if self._fd is None:
            raise ValueError("seek on closed file")
        if whence == io.SEEK_SET:
            newpos = self._start + offset
        elif whence == io.SEEK_CUR:
            newpos = self._position + offset
        elif whence == io.SEEK_END:
            newpos = self._end + offset
        else:
            raise ValueError(f"whence value {whence} unsupported")
        if newpos < self._start:
            raise ValueError("new position is negative")
        self._position = newpos
        return self.tell()

    def tell(self) -> int:
        if self._fd is None:
            raise ValueError("tell on closed file")
        return self._position - self._start

    def close(self) -> None:
        try:
            if self._fd is not None:
                self._close(self._fd)
                self._fd = None
        finally:
            super().close()


@contextmanager
def closing_ubifile(ubifile: ubi_file) -> Generator[ubi_file, Any, None]:
    try:
        yield ubifile
    finally:
        ubifile._fhandle.close()


def get_fs_from_ubi(fd: BinaryIO, size: int, offset: int = 0) -> bytes:
    """Return the first file system that sits on top of the UBI volume."""
    fd.seek(offset)
    binbytes = fd.read(size)
    with TempFile(binbytes) as t:
        block_size = guess_peb_size(t)
        with closing_ubifile(ubi_file(t, block_size)) as ubifile:
            ubi_obj = ubi(ubifile)
            volume = ubi_obj.images[0].volumes.popitem()[1]
            return b''.join(volume.reader(ubi_obj))


def dir_size(path: Union[GenericPath[AnyStr], int, None] = None) -> int:
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


def make_cache_file(url: str, filebytes: Buffer, name: Optional[str] = None) -> bool:
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


def lz4_legacy_decompress(f: BinaryIO) -> bytes:
    # https://github.com/python-lz4/python-lz4/issues/169
    res = b''
    if f.read(4) != FileType.LZ4_LEGACY_FRAME.value:
        raise Exception("LZ4 legacy frame magic not found")
    while (size := int.from_bytes(f.read(4), "little")) != len(res):
        res += lz4_block_decompress(f.read(size), uncompressed_size=8*ONEMIB)
    return res
