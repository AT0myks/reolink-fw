from __future__ import annotations

from pathlib import PurePosixPath
from stat import filemode
from typing import Iterator, Literal, Optional

from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.defines import (
    UBIFS_COMMON_HDR_SZ,
    UBIFS_ITYPE_BLK,
    UBIFS_ITYPE_CHR,
    UBIFS_ITYPE_DIR,
    UBIFS_ITYPE_FIFO,
    UBIFS_ITYPE_LNK,
    UBIFS_ITYPE_REG,
    UBIFS_ITYPE_SOCK,
    UBIFS_NODE_MAGIC,
    UBIFS_ROOT_INO,
    UBIFS_SB_NODE,
    UBIFS_SB_NODE_SZ,
)
from ubireader.ubifs.nodes import common_hdr, ino_node, sb_node
from ubireader.ubifs.output import _process_reg_file
from ubireader.ubi_io import ubi_file
from ubireader.utils import guess_leb_size

from reolinkfw.tmpfile import TempFile


class File:

    def __init__(self, image: UBIFS, nodes: dict, name: str = '', parent: Optional[Directory] = None) -> None:
        self._image = image
        self._nodes = nodes
        self._name = name
        self._parent = parent

    @property
    def name(self) -> str:
        return self._name

    @property
    def parent(self) -> Optional[Directory]:
        return self._parent

    @property
    def path(self) -> PurePosixPath:
        if self._parent is None:
            return PurePosixPath('/')
        return self._parent.path / self._name

    @property
    def inode(self) -> ino_node:
        return self._nodes["ino"]

    @property
    def mode(self) -> int:
        return self.inode.mode

    @property
    def filemode(self) -> str:
        return filemode(self.mode)

    @property
    def is_dir(self) -> bool:
        return False

    @property
    def is_file(self) -> bool:
        return False

    @property
    def is_symlink(self) -> bool:
        return False


class DataFile(File):

    def read_bytes(self) -> bytes:
        ...

    def read_text(self, encoding: str = "utf8", errors: str = "strict") -> str:
        return self.read_bytes().decode(encoding, errors)


class RegularFile(DataFile):

    @property
    def is_file(self) -> Literal[True]:
        return True

    def read_bytes(self) -> bytes:
        return _process_reg_file(self._image._ubifs, self._nodes, None)


class Symlink(DataFile):

    @property
    def is_symlink(self) -> Literal[True]:
        return True

    def read_bytes(self) -> bytes:
        return self.inode.data

    def readlink(self) -> PurePosixPath:
        return PurePosixPath(self.read_text())


class Directory(File):

    def __init__(self, image: UBIFS, nodes: dict, name: str = '', parent: Optional[Directory] = None) -> None:
        super().__init__(image, nodes, name, parent)
        self._children = {}
        for dent in nodes.get("dent", []):
            cls = filetype[dent.type]
            self._children[dent.name] = cls(image, image.inodes[dent.inum], dent.name, self)

    def __iter__(self) -> Iterator[File]:
        yield from self._children.values()

    @property
    def is_dir(self) -> Literal[True]:
        return True

    @property
    def children(self) -> dict[str, File]:
        return self._children

    def select(self, path) -> Optional[File]:
        """Select a file of any kind by path.

        The path can be absolute or relative.
        Special entries `'.'` and `'..'` are supported.
        """
        path = PurePosixPath(path)
        if str(path) == "..":
            return self.parent if self.parent is not None else self
        if path.root == '/':
            if str(self.path) == '/':
                path = path.relative_to('/')
            else:
                return self._image.root.select(path)
        if str(path) == '.':
            return self
        child, *descendants = path.parts
        if (file := self._children.get(child)) is not None:
            if isinstance(file, Directory) and descendants:
                return file.select(PurePosixPath(*descendants))
            elif not descendants:
                return file
        return None

    def riter(self) -> Iterator[File]:
        yield self
        for file in self:
            if isinstance(file, Directory):
                yield from file.riter()
            else:
                yield file


class UBIFS:

    def __init__(self, ubifs: ubifs) -> None:
        self._ubifs = ubifs
        self._inodes = {}
        self._bad_blocks = []
        walk.index(ubifs, ubifs.master_node.root_lnum, ubifs.master_node.root_offs, self._inodes, self._bad_blocks)
        self._root = Directory(self, self._inodes[UBIFS_ROOT_INO])

    def __enter__(self) -> UBIFS:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __iter__(self) -> Iterator[File]:
        yield from self._root.riter()

    @property
    def inodes(self) -> dict:
        return self._inodes

    @property
    def root(self) -> Directory:
        return self._root

    def close(self) -> None:
        self._ubifs._file._fhandle.close()

    def select(self, path) -> Optional[File]:
        return self._root.select(path)

    @classmethod
    def from_file(cls, path) -> UBIFS:
        return cls(ubifs(ubi_file(path, guess_leb_size(path))))

    @classmethod
    def from_bytes(cls, bytes_, offset: int = 0) -> UBIFS:
        chdr = common_hdr(bytes_[offset:offset+UBIFS_COMMON_HDR_SZ])
        if chdr.magic != int.from_bytes(UBIFS_NODE_MAGIC, "little") or chdr.node_type != UBIFS_SB_NODE:
            raise Exception("Not UBIFS")
        sb_start = offset + UBIFS_COMMON_HDR_SZ
        sb_end = sb_start + UBIFS_SB_NODE_SZ
        sblk = sb_node(bytes_[sb_start:sb_end])
        tmpfile = TempFile(bytes_[offset:])
        tmpfile.open()  # tmpfile's close() will not be called.
        return cls(ubifs(ubi_file(tmpfile, sblk.leb_size)))


filetype = {
    UBIFS_ITYPE_REG: RegularFile,
    UBIFS_ITYPE_DIR: Directory,
    UBIFS_ITYPE_LNK: Symlink,
    UBIFS_ITYPE_BLK: File,
    UBIFS_ITYPE_CHR: File,
    UBIFS_ITYPE_FIFO: File,
    UBIFS_ITYPE_SOCK: File,
}
