import asyncio
import io
import posixpath
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import IO, Any, BinaryIO, Optional, Union
from urllib.parse import parse_qsl, urlparse
from zipfile import ZipFile, is_zipfile

import aiohttp
from aiohttp.typedefs import StrOrURL
from lxml.etree import fromstring
from lxml.html import document_fromstring
from pakler import PAK, Section, is_pak_file
from pycramfs import Cramfs
from PySquashfsImage import SquashFsImage

from reolinkfw.tmpfile import TempFile
from reolinkfw.typedefs import Buffer, Files, StrPath, StrPathURL
from reolinkfw.ubifs import UBIFS
from reolinkfw.uboot import get_arch_name, get_uboot_version, get_uimage_header
from reolinkfw.util import (
    FileType,
    SectionFile,
    get_cache_file,
    get_fs_from_ubi,
    has_cache,
    make_cache_file,
    sha256_pak
)

__version__ = "1.1.0"

FILES = ("version_file", "version.json", "dvr.xml", "dvr", "router")
INFO_KEYS = ("firmware_version_prefix", "board_type", "board_name", "build_date", "display_type_info", "detail_machine_type", "type")

UBOOT_SECTIONS = ("uboot", "uboot1", "BOOT")
KERNEL_SECTIONS = ("kernel", "KERNEL")
ROOTFS_SECTIONS = ("fs", "rootfs")
FS_SECTIONS = ROOTFS_SECTIONS + ("app",)


class ReolinkFirmware(PAK):

    def __init__(self, fd: BinaryIO, offset: int = 0, closefd: bool = True) -> None:
        super().__init__(fd, offset, closefd)
        self._uboot_section_name = self._get_uboot_section_name()
        self._kernel_section_name = self._get_kernel_section_name()
        self._sdict = {s.name: s for s in self}
        self._open_files = 1

    def __del__(self) -> None:
        self.close()

    def __getitem__(self, key: Union[int, str]) -> Section:
        if isinstance(key, int):
            return self.sections[key]
        if isinstance(key, str):
            if key.lower() == "uboot":
                key = self._uboot_section_name
            elif key.lower() == "kernel":
                key = self._kernel_section_name
            return self._sdict[key]
        raise TypeError

    def __iter__(self) -> Iterator[Section]:
        yield from self.sections

    def _fdclose(self, fd: BinaryIO) -> None:
        self._open_files -= 1
        if self._closefd and not self._open_files:
            fd.close()

    def _get_uboot_section_name(self) -> str:
        for section in self:
            if section.len and section.name in UBOOT_SECTIONS:
                return section.name
        raise Exception("U-Boot section not found")

    def _get_kernel_section_name(self) -> str:
        for section in self:
            if section.len and section.name in KERNEL_SECTIONS:
                return section.name
        raise Exception("Kernel section not found")

    def open(self, section: Section) -> SectionFile:
        self._open_files += 1
        return SectionFile(self._fd, section, self._fdclose)

    def close(self) -> None:
        if self._fd is not None:
            self._fdclose(self._fd)
            self._fd = None


async def download(url: StrOrURL) -> Union[bytes, int]:
    """Return resource as bytes.

    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


def extract_paks(zip: Union[StrPath, IO[bytes]]) -> list[tuple[str, ReolinkFirmware]]:
    """Return a list of tuples, one for each PAK file found in the ZIP.

    It is the caller's responsibility to close the PAK files.
    """
    paks = []
    with ZipFile(zip) as myzip:
        for name in myzip.namelist():
            file = myzip.open(name)
            if is_pak_file(file):
                paks.append((file.name, ReolinkFirmware.from_fd(file)))
            else:
                file.close()
    return paks


def get_info_from_files(files: Mapping[Files, Optional[bytes]]) -> dict[str, Optional[str]]:
    xml: dict[str, str] = dict(fromstring(files["dvr.xml"]).items())
    info = {k: xml.get(k) for k in INFO_KEYS}
    info["version_file"] = files["version_file"].decode().strip()
    if not info.get("firmware_version_prefix"):
        thefile = files["dvr"] if files["dvr"] is not None else files["router"]
        match = re.search(b"echo (v[23]\.0\.0)", thefile) if thefile is not None else None
        info["firmware_version_prefix"] = match.group(1).decode() if match else None
    return info


def get_files_from_squashfs(fd: BinaryIO, offset: int = 0, closefd: bool = True) -> dict[Files, Optional[bytes]]:
    # Firmwares using squashfs have either one or two file system
    # sections. When there is only one, the app directory is located at
    # /mnt/app. Otherwise it's the same as with cramfs and ubifs.
    files = dict.fromkeys(FILES)
    with SquashFsImage(fd, offset, closefd) as image:
        for name in files:
            path2 = posixpath.join("/mnt/app", name)
            if (file := (image.select(name) or image.select(path2))) is not None:
                files[name] = file.read_bytes()
    return files


def get_files_from_ubifs(binbytes: Buffer) -> dict[Files, Optional[bytes]]:
    # For now all firmwares using ubifs have two file system sections.
    # The interesting files are in the root directory of the "app" one.
    # Using select() with a relative path is enough.
    files = dict.fromkeys(FILES)
    with TempFile(binbytes) as tempfile:
        with UBIFS.from_file(tempfile) as image:
            for name in files:
                if (file := image.select(name)) is not None:
                    files[name] = file.read_bytes()
    return files


def get_files_from_ubi(fd: BinaryIO, size: int, offset: int = 0) -> dict[Files, Optional[bytes]]:
    fsbytes = get_fs_from_ubi(fd, size, offset)
    fs = FileType.from_magic(fsbytes[:4])
    if fs == FileType.UBIFS:
        return get_files_from_ubifs(fsbytes)
    elif fs == FileType.SQUASHFS:
        return get_files_from_squashfs(io.BytesIO(fsbytes))
    raise Exception("Unknown file system in UBI")


def get_files_from_cramfs(fd: BinaryIO, offset: int = 0, closefd: bool = True) -> dict[Files, Optional[bytes]]:
    # For now all firmwares using cramfs have two file system sections.
    # The interesting files are in the root directory of the "app" one.
    # Using select() with a relative path is enough.
    files = dict.fromkeys(FILES)
    with Cramfs.from_fd(fd, offset, closefd) as cramfs:
        for name in files:
            if (file := cramfs.select(name)) is not None:
                files[name] = file.read_bytes()
    return files


def is_url(string: StrOrURL) -> bool:
    return str(string).startswith("http")


def is_local_file(string: StrPath) -> bool:
    return Path(string).is_file()


def get_fs_info(fw: ReolinkFirmware, fs_sections: Iterable[Section]) -> list[dict[str, str]]:
    result = []
    for section in fs_sections:
        with fw.open(section) as f:
            fs = FileType.from_magic(f.read(4))
            if fs == FileType.UBI:
                f.seek(266240)
                fs = FileType.from_magic(f.read(4))
        result.append({
            "name": section.name,
            "type": fs.name.lower() if fs is not None else "unknown"
        })
    return result


async def get_info_from_pak(fw: ReolinkFirmware) -> dict[str, Any]:
    ha = await asyncio.to_thread(sha256_pak, fw)
    fs_sections = [s for s in fw if s.name in FS_SECTIONS]
    app = fs_sections[-1]
    with fw.open(app) as f:
        fs = FileType.from_magic(f.read(4))
        if fs == FileType.CRAMFS:
            files = await asyncio.to_thread(get_files_from_cramfs, f, 0, False)
        elif fs == FileType.UBI:
            files = await asyncio.to_thread(get_files_from_ubi, f, app.len, 0)
        elif fs == FileType.SQUASHFS:
            files = await asyncio.to_thread(get_files_from_squashfs, f, 0, False)
        else:
            return {"error": "Unrecognized image type", "sha256": ha}
    uimage = get_uimage_header(fw)
    return {
        **get_info_from_files(files),
        "os": "Linux" if uimage.os == 5 else "Unknown",
        "architecture": get_arch_name(uimage.arch),
        "kernel_image_name": uimage.name,
        "uboot_version": get_uboot_version(fw),
        "filesystems": get_fs_info(fw, fs_sections),
        "sha256": ha
    }


async def direct_download_url(url: str) -> str:
    if url.startswith("https://drive.google.com/file/d/"):
        return f"https://drive.google.com/uc?id={url.split('/')[5]}&confirm=t"
    elif url.startswith("https://www.mediafire.com/file/"):
        doc = document_fromstring(await download(url))
        return doc.get_element_by_id("downloadButton").get("href")
    elif url.startswith("https://bit.ly/"):
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=False) as resp:
                return await direct_download_url(resp.headers["Location"])
    return url


async def get_paks(file_or_url: StrPathURL, use_cache: bool = True) -> list[tuple[Optional[str], ReolinkFirmware]]:
    """Return PAK files read from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK. On success return a
    list of 2-tuples where each tuple is of the form
    `(pak_name, pak_file)`. When the argument is a URL, `pak_name` may
    be None. If the file is a ZIP the list might be empty.
    It is the caller's responsibility to close the PAK files.
    """
    file_or_url = str(file_or_url)
    if is_url(file_or_url):
        if use_cache and has_cache(file_or_url):
            return await get_paks(get_cache_file(file_or_url))
        file_or_url = await direct_download_url(file_or_url)
        zip_or_pak_bytes = await download(file_or_url)
        if isinstance(zip_or_pak_bytes, int):
            raise Exception(f"HTTP error {zip_or_pak_bytes}")
        pakname = dict(parse_qsl(urlparse(file_or_url).query)).get("name")
        if use_cache:
            make_cache_file(file_or_url, zip_or_pak_bytes, pakname)
        if is_pak_file(zip_or_pak_bytes):
            return [(pakname, ReolinkFirmware.from_bytes(zip_or_pak_bytes))]
        else:
            zipfile = io.BytesIO(zip_or_pak_bytes)
            if is_zipfile(zipfile):
                return await asyncio.to_thread(extract_paks, zipfile)
            zipfile.close()
            raise Exception("Not a ZIP or a PAK file")
    elif is_local_file(file_or_url):
        file_or_url = Path(file_or_url)
        if is_zipfile(file_or_url):
            return await asyncio.to_thread(extract_paks, file_or_url)
        elif is_pak_file(file_or_url):
            return [(file_or_url.name, ReolinkFirmware.from_file(file_or_url))]
        raise Exception("Not a ZIP or a PAK file")
    raise Exception("Not a URL or file")


async def get_info(file_or_url: StrPathURL, use_cache: bool = True) -> list[dict[str, Any]]:
    """Retrieve firmware info from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK.
    """
    try:
        paks = await get_paks(file_or_url, use_cache)
    except Exception as e:
        return [{"file": file_or_url, "error": str(e)}]
    if not paks:
        return [{"file": file_or_url, "error": "No PAKs found in ZIP file"}]
    info = [{**await get_info_from_pak(pakfile), "file": file_or_url, "pak": pakname} for pakname, pakfile in paks]
    for _, pakfile in paks:
        pakfile.close()
    return info
