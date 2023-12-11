from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from enum import Enum
from functools import partial
from os import scandir
from pathlib import Path
from shutil import disk_usage
from tempfile import gettempdir as _gettempdir
from typing import Any, AnyStr, BinaryIO, Optional, Union
from zipfile import is_zipfile

from pakler import PAK, is_pak_file
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


def sha256_pak(pak: PAK) -> str:
    sha = hashlib.sha256()
    pak._fd.seek(0)
    for block in iter(partial(pak._fd.read, ONEMIB), b''):
        sha.update(block)
    return sha.hexdigest()


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
