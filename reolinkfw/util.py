import hashlib
import io
from contextlib import contextmanager
from functools import partial

from pakler import PAK
from pycramfs.const import MAGIC_BYTES as CRAMFS_MAGIC
from PySquashfsImage.const import SQUASHFS_MAGIC
from ubireader.ubi import ubi
from ubireader.ubi.defines import UBI_EC_HDR_MAGIC as UBI_MAGIC
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.ubifs.defines import UBIFS_NODE_MAGIC as UBIFS_MAGIC
from ubireader.utils import guess_peb_size

from reolinkfw.tmpfile import TempFile

SQUASHFS_MAGIC = SQUASHFS_MAGIC.to_bytes(4, "little")


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


def get_fs_from_ubi(binbytes):
    """Return the first file system that sits on top of the UBI volume."""
    with TempFile(binbytes) as t:
        block_size = guess_peb_size(t)
        with closing_ubifile(ubi_file(t, block_size)) as ubifile:
            ubi_obj = ubi(ubifile)
            vol_blocks = ubi_obj.images[0].volumes.popitem()[1].get_blocks(ubi_obj.blocks)
            return b''.join(leb_virtual_file(ubi_obj, vol_blocks).reader())


def is_ubi(bytes_):
    return bytes_[:4] == UBI_MAGIC


def is_squashfs(bytes_):
    return bytes_[:4] == SQUASHFS_MAGIC


def is_cramfs(bytes_):
    return bytes_[:4] == CRAMFS_MAGIC


def is_ubifs(bytes_):
    return bytes_[:4] == UBIFS_MAGIC


def sha256_pak(pak: PAK) -> str:
    sha = hashlib.sha256()
    pak._fd.seek(0)
    for block in iter(partial(pak._fd.read, 1024**2), b''):
        sha.update(block)
    return sha.hexdigest()
