from __future__ import annotations

from ctypes import BigEndianStructure, c_char, c_uint32, c_uint8, sizeof
from enum import IntEnum
from typing import BinaryIO

UBOOT_MAGIC = 0x27051956


class Arch(IntEnum):
    ARM = 2
    MIPS = 5
    ARM64 = 22


class Compression(IntEnum):
    LZMA = 3


class LegacyImageHeader(BigEndianStructure):
    _fields_ = [
        ("_magic", c_uint32),
        ("_hcrc", c_uint32),
        ("_time", c_uint32),
        ("_size", c_uint32),
        ("_load", c_uint32),
        ("_ep", c_uint32),
        ("_dcrc", c_uint32),
        ("_os", c_uint8),
        ("_arch", c_uint8),
        ("_type", c_uint8),
        ("_comp", c_uint8),
        ("_name", c_char * 32),
    ]

    _magic: int
    _hcrc: int
    _time: int
    _size: int
    _load: int
    _ep: int
    _dcrc: int
    _os: int
    _arch: int
    _type: int
    _comp: int
    _name: bytes

    @property
    def magic(self) -> int:
        return self._magic

    @property
    def hcrc(self) -> int:
        return self._hcrc

    @property
    def time(self) -> int:
        return self._time

    @property
    def size(self) -> int:
        return self._size

    @property
    def load(self) -> int:
        return self._load

    @property
    def ep(self) -> int:
        return self._ep

    @property
    def dcrc(self) -> int:
        return self._dcrc

    @property
    def os(self) -> int:
        return self._os

    @property
    def arch(self) -> Arch:
        return Arch(self._arch)

    @property
    def type(self) -> int:
        return self._type

    @property
    def comp(self) -> int:
        return self._comp

    @property
    def name(self) -> str:
        return self._name.decode()

    @classmethod
    def from_fd(cls, fd: BinaryIO) -> LegacyImageHeader:
        return cls.from_buffer_copy(fd.read(sizeof(cls)))


def get_arch_name(arch: Arch) -> str:
    if arch == Arch.ARM:
        return "ARM"
    elif arch == Arch.MIPS:
        return "MIPS"
    elif arch == Arch.ARM64:
        return "AArch64"
    raise Exception("Unknown architecture")
