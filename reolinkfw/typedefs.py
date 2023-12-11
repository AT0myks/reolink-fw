import collections.abc
from os import PathLike
from typing import AnyStr, Literal, Union

from aiohttp.typedefs import StrOrURL

if hasattr(collections.abc, "Buffer"):
    Buffer = collections.abc.Buffer
else:
    Buffer = Union[bytes, bytearray, memoryview]

Files = Literal["version_file", "version.json", "dvr.xml", "dvr", "router"]
GenericPath = Union[AnyStr, PathLike[AnyStr]]
StrPath = Union[str, PathLike[str]]
StrPathURL = Union[StrPath, StrOrURL]
StrOrBytesPath = Union[StrPath, bytes, PathLike[bytes]]
FileDescriptorOrPath = Union[int, StrOrBytesPath]
