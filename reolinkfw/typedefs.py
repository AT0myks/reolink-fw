import collections.abc
from os import PathLike
from typing import AnyStr, Optional, TypedDict, Union

from aiohttp.typedefs import StrOrURL

if hasattr(collections.abc, "Buffer"):
    Buffer = collections.abc.Buffer
else:
    Buffer = Union[bytes, bytearray, memoryview]

GenericPath = Union[AnyStr, PathLike[AnyStr]]
StrPath = Union[str, PathLike[str]]
StrPathURL = Union[StrPath, StrOrURL]
StrOrBytesPath = Union[StrPath, bytes, PathLike[bytes]]
FileDescriptorOrPath = Union[int, StrOrBytesPath]

InfoFiles = TypedDict("InfoFiles", {
    "version_file": bytes,
    "dvr.xml": bytes,
    "dvr": Optional[bytes],
    "router": Optional[bytes],
})


class DVRInfo(TypedDict):
    """Info from the dvr.xml and version_file files."""
    board_name: str
    board_type: str
    build_date: str
    detail_machine_type: str
    display_type_info: str
    firmware_version_prefix: str
    type: str
    version_file: str
