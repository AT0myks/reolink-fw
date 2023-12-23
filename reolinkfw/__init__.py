import asyncio
import gzip
import hashlib
import io
import lzma
import posixpath
import re
import zlib
from ast import literal_eval
from collections.abc import Iterator
from contextlib import redirect_stdout
from ctypes import sizeof
from functools import partial
from pathlib import Path
from typing import IO, Any, BinaryIO, Optional, Union
from urllib.parse import parse_qsl, urlparse
from zipfile import ZipFile, is_zipfile

import aiohttp
import pybcl
from aiohttp.typedefs import StrOrURL
from lxml.etree import fromstring
from lxml.html import document_fromstring
from pakler import PAK, Section, is_pak_file
from pycramfs import Cramfs
from pycramfs.extract import extract_dir as extract_cramfs
from pyfdt.pyfdt import Fdt, FdtBlobParse
from PySquashfsImage import SquashFsImage
from PySquashfsImage.extract import extract_dir as extract_squashfs
from ubireader.ubifs import ubifs as ubifs_
from ubireader.ubifs.output import extract_files as extract_ubifs
from ubireader.ubi_io import ubi_file
from ubireader.utils import guess_leb_size

from reolinkfw.fdt import RE_FDT_HEADER, FDTHeader
from reolinkfw.tmpfile import TempFile
from reolinkfw.typedefs import Buffer, DVRInfo, InfoFiles, StrPath, StrPathURL
from reolinkfw.ubifs import UBIFS
from reolinkfw.uboot import Compression, LegacyImageHeader, get_arch_name
from reolinkfw.util import (
    ONEMIB,
    FileType,
    SectionFile,
    closing_ubifile,
    get_cache_file,
    get_fs_from_ubi,
    has_cache,
    lz4_legacy_decompress,
    make_cache_file,
)

__version__ = "2.0.0"

FILES = ("version_file", "dvr.xml", "dvr", "router")
INFO_KEYS = ("firmware_version_prefix", "board_type", "board_name", "build_date", "display_type_info", "detail_machine_type", "type")

UBOOT_SECTIONS = ("uboot", "uboot1", "BOOT")
KERNEL_SECTIONS = ("kernel", "KERNEL")
ROOTFS_SECTIONS = ("fs", "rootfs")
FS_SECTIONS = ROOTFS_SECTIONS + ("app",)

RE_BANNER = re.compile(rb"\x00(Linux version .+? \(.+?@.+?\) \(.+?\) .+?)\n\x00")
RE_COMPLINK = re.compile(rb"\x00([^\x00]+?-linux-.+? \(.+?\) [0-9].+?)\n\x00+(.+?)\n\x00")
RE_IKCFG = re.compile(b"IKCFG_ST(.+?)IKCFG_ED", re.DOTALL)
RE_KERNEL_COMP = re.compile(
    b"(?P<lz4>" + FileType.LZ4_LEGACY_FRAME.value + b')'
    b"|(?P<xz>\xFD\x37\x7A\x58\x5A\x00\x00.(?!XZ))"
    b"|(?P<lzma>.{5}\xff{8})"
    b"|(?P<gzip>\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03)"
)
RE_LZMA_OR_XZ = re.compile(b".{5}\xff{8}|\xFD\x37\x7A\x58\x5A\x00\x00")
# Pattern for a legacy image header with these properties:
# OS: U-Boot / firmware (0x11)
# Type: kernel (0x02)
# Only used for MStar/SigmaStar cameras (Lumus and RLC-410W IPC_30K128M4MP)
RE_MSTAR = re.compile(FileType.UIMAGE.value + b".{24}\x11.\x02.{33}", re.DOTALL)
RE_UBOOT = re.compile(rb"U-Boot [0-9]{4}\.[0-9]{2}.*? \(.+?\)")

DUMMY = object()


class ReolinkFirmware(PAK):

    def __init__(self, fd: BinaryIO, offset: int = 0, closefd: bool = True) -> None:
        super().__init__(fd, offset, closefd)
        self._uboot_section_name = self._get_uboot_section_name()
        self._uboot_section = None
        self._uboot = None
        self._kernel_section_name = self._get_kernel_section_name()
        self._kernel_section = None
        self._kernel = None
        self._fdt = DUMMY
        self._sdict = {s.name: s for s in self}
        self._open_files = 1
        self._fs_sections = [s for s in self if s.name in FS_SECTIONS]

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

    @property
    def uboot_section(self) -> bytes:
        """Return the firmware's U-Boot section as bytes."""
        if self._uboot_section is not None:
            return self._uboot_section
        self._uboot_section = self.extract_section(self["uboot"])
        return self._uboot_section

    @property
    def uboot(self) -> bytes:
        """Return the firmware's decompressed U-Boot as bytes.

        If the U-Boot is not compressed this gives the same result
        as the `uboot_section` property.
        """
        if self._uboot is not None:
            return self._uboot
        self._uboot = self._decompress_uboot()
        return self._uboot

    @property
    def kernel_section(self) -> bytes:
        """Return the firmware's kernel section as bytes."""
        if self._kernel_section is not None:
            return self._kernel_section
        self._kernel_section = self.extract_section(self["kernel"])
        return self._kernel_section

    @property
    def kernel(self) -> bytes:
        """Return the firmware's decompressed kernel as bytes."""
        if self._kernel is not None:
            return self._kernel
        self._kernel = self._decompress_kernel()
        return self._kernel

    @property
    def fdt(self) -> Optional[Fdt]:
        if self._fdt is not DUMMY:
            return self._fdt  # type: ignore
        self._fdt = self._get_fdt()
        return self._fdt

    @property
    def fdt_json(self) -> Optional[dict[str, Any]]:
        if self.fdt is not None:
            return literal_eval(self.fdt.to_json().replace("null", "None"))
        return None

    @property
    def board(self) -> Optional[str]:
        if self.fdt_json is not None:
            return self.fdt_json["model"][1]
        return None

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

    def _decompress_uboot(self) -> bytes:
        uboot = self.uboot_section
        if uboot.startswith(pybcl.BCL_MAGIC_BYTES):
            # Sometimes section.len - sizeof(hdr) is 1 to 3 bytes larger
            # than hdr.size. The extra bytes are 0xff (padding?). This
            # could explain why the compressed size is added to the header.
            hdr = pybcl.HeaderVariant.from_buffer_copy(uboot)
            compressed = uboot[sizeof(hdr):sizeof(hdr)+hdr.size]
            return pybcl.decompress(compressed, hdr.algo, hdr.outsize)
        if (match := RE_MSTAR.search(uboot)) is not None:
            hdr = LegacyImageHeader.from_buffer_copy(uboot, match.start())
            start = match.start() + sizeof(hdr)
            if hdr.comp == Compression.LZMA:
                return lzma.decompress(uboot[start:start+hdr.size])
            raise Exception(f"Unexpected compression {hdr.comp}")
        return uboot  # Assume no compression

    def _decompress_kernel(self) -> bytes:
        # Use lzma.LZMADecompressor instead of lzma.decompress
        # because we know there's only one stream.
        data = self.kernel_section
        uimage_hdr_size = sizeof(LegacyImageHeader)
        # RLN36 kernel image headers report no compression
        # so don't bother reading the header and just look for
        # a compression magic.
        if RE_LZMA_OR_XZ.match(data, uimage_hdr_size):
            return lzma.LZMADecompressor().decompress(data[uimage_hdr_size:])
        if (halt := data.find(b" -- System halted")) == -1:
            raise Exception("'System halted' string not found")
        match = RE_KERNEL_COMP.search(data, halt)
        if match is None:
            raise Exception("No known compression found in kernel")
        start = match.start()
        if match.lastgroup == "lz4":
            return lz4_legacy_decompress(io.BytesIO(data[start:]))
        elif match.lastgroup in ("xz", "lzma"):
            return lzma.LZMADecompressor().decompress(data[start:])
        elif match.lastgroup == "gzip":
            # wbits=31 because only one member to decompress.
            return zlib.decompress(data[start:], wbits=31)
        raise Exception("unreachable")

    def _get_fdt(self) -> Optional[Fdt]:
        # At most 2 FDTs can be found in a firmware, and usually only one.
        # Most of the time it's in the fdt section or in the decompressed kernel.
        # Hardware versions starting with IPC_30 or IPC_32 have 1 FDT
        # in the decompressed kernel.
        # Hardware versions starting with IPC_35, IPC_36 or IPC_38 have no FDT.
        # HI3536CV100 -> only firmwares with FDT in kernel_section
        # Some firmwares with one FDT in the fdt section have a 2nd FDT
        # in the U-Boot section with no model.
        match = data = None
        if "fdt" in self._sdict:
            # Reolink Duo 1: 2 FDTs, section starts with header FKLR (Reolink FDT?)
            # Some NVRs: 2 FDTs
            data = self.extract_section(self["fdt"])
            match = RE_FDT_HEADER.search(data)
        elif (match := RE_FDT_HEADER.search(self.kernel_section)) is not None:
            data = self.kernel_section
        elif (match := RE_FDT_HEADER.search(self.kernel)) is not None:
            data = self.kernel
        if match is not None and data is not None:
            start = match.start()
            hdr = FDTHeader.from_buffer_copy(data, start)
            end = start + hdr.totalsize
            return FdtBlobParse(io.BytesIO(data[start:end])).to_fdt()
        return None

    def open(self, section: Union[Section, str]) -> SectionFile:
        if isinstance(section, str):
            section = self[section]
        self._open_files += 1
        return SectionFile(self._fd, section, self._fdclose)

    def close(self) -> None:
        if self._fd is not None:
            self._fdclose(self._fd)
            self._fd = None

    def sha256(self) -> str:
        sha = hashlib.sha256()
        self._fd.seek(0)
        for block in iter(partial(self._fd.read, ONEMIB), b''):
            sha.update(block)
        return sha.hexdigest()

    def get_uboot_info(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        # Should never be None.
        match_ub = RE_UBOOT.search(self.uboot)
        version = match_ub.group().decode() if match_ub is not None else None
        # Should only be None for HiSilicon devices.
        match_cl = RE_COMPLINK.search(self.uboot)
        compiler = match_cl.group(1).decode() if match_cl is not None else None
        linker = match_cl.group(2).decode() if match_cl is not None else None
        return version, compiler, linker

    def get_kernel_image_header(self) -> Optional[LegacyImageHeader]:
        with self.open("kernel") as f:
            data = f.read(sizeof(LegacyImageHeader))
        if FileType.from_magic(data[:4]) == FileType.UIMAGE:
            return LegacyImageHeader.from_buffer_copy(data)
        return None

    def get_kernel_image_header_info(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        hdr = self.get_kernel_image_header()
        if hdr is None:
            return None, None, None
        os = "Linux" if hdr.os == 5 else "Unknown"
        return os, get_arch_name(hdr.arch), hdr.name

    def get_linux_banner(self) -> Optional[str]:
        match = RE_BANNER.search(self.kernel)
        return match.group(1).decode() if match is not None else None

    def get_kernel_config(self) -> Optional[bytes]:
        if (match := RE_IKCFG.search(self.kernel)) is not None:
            return gzip.decompress(match.group(1))
        return None

    def get_vendor(self) -> Optional[str]:
        map_ = {
            "novatek": "Novatek",
            "sstar": "MStar/SigmaStar",
            "hisilicon": "HiSilicon",
        }
        if self.fdt_json is not None:
            key = self.fdt_json["compatible"][1].split(',')[0]
            return map_.get(key.lower(), key)
        with self.open("uboot") as f:
            if re.match(b"GM[0-9]{4}", f.read(6)):
                return "Grain Media"
        if re.search(b"HISILICON LOGO MAGIC", self.uboot_section) is not None:
            return "HiSilicon"
        return None

    def get_fs_info(self) -> list[dict[str, str]]:
        result = []
        for section in self._fs_sections:
            with self.open(section) as f:
                fs = FileType.from_magic(f.read(4))
                if fs == FileType.UBI:
                    f.seek(266240)
                    fs = FileType.from_magic(f.read(4))
            result.append({
                "name": section.name,
                "type": fs.name.lower() if fs is not None else "unknown"
            })
        return result

    async def get_info(self) -> dict[str, Any]:
        ha = await asyncio.to_thread(self.sha256)
        app = self._fs_sections[-1]
        with self.open(app) as f:
            fs = FileType.from_magic(f.read(4))
            if fs == FileType.CRAMFS:
                files = await asyncio.to_thread(get_files_from_cramfs, f, 0, False)
            elif fs == FileType.UBI:
                files = await asyncio.to_thread(get_files_from_ubi, f, app.len, 0)
            elif fs == FileType.SQUASHFS:
                files = await asyncio.to_thread(get_files_from_squashfs, f, 0, False)
            else:
                return {"error": "Unrecognized image type", "sha256": ha}
        os, architecture, kernel_image_name = self.get_kernel_image_header_info()
        uboot_version, compiler, linker = self.get_uboot_info()
        return {
            **get_info_from_files(files),
            "os": os,
            "architecture": architecture,
            "kernel_image_name": kernel_image_name,
            "uboot_version": uboot_version,
            "uboot_compiler": compiler,
            "uboot_linker": linker,
            "linux_banner": self.get_linux_banner(),
            "board": self.board,
            "board_vendor": self.get_vendor(),
            "filesystems": self.get_fs_info(),
            "sha256": ha
        }

    def extract_file_system(self, section: Section, dest: Optional[Path] = None) -> None:
        dest = (Path.cwd() / "reolink_fs") if dest is None else dest
        dest.mkdir(parents=True, exist_ok=True)
        with self.open(section) as f:
            fs = FileType.from_magic(f.read(4))
            if fs == FileType.UBI:
                fs_bytes = get_fs_from_ubi(f, section.len, 0)
                fs = FileType.from_magic(fs_bytes[:4])
                if fs == FileType.UBIFS:
                    with TempFile(fs_bytes) as file:
                        block_size = guess_leb_size(file)
                        with closing_ubifile(ubi_file(file, block_size)) as ubifile:
                            with redirect_stdout(io.StringIO()):
                                # Files that already exist are not written again.
                                extract_ubifs(ubifs_(ubifile), dest)
                elif fs == FileType.SQUASHFS:
                    with SquashFsImage.from_bytes(fs_bytes) as image:
                        extract_squashfs(image.root, dest, True)
                else:
                    raise Exception("Unknown file system in UBI")
            elif fs == FileType.SQUASHFS:
                with SquashFsImage(f, 0, False) as image:
                    extract_squashfs(image.root, dest, True)
            elif fs == FileType.CRAMFS:
                with Cramfs.from_fd(f, 0, False) as image:
                    extract_cramfs(image.rootdir, dest, True)
            else:
                raise Exception("Unknown file system")

    def extract(self, dest: Optional[Path] = None, force: bool = False) -> None:
        dest = (Path.cwd() / "reolink_firmware") if dest is None else dest
        dest.mkdir(parents=True, exist_ok=force)
        rootfsdir = [s.name for s in self if s.name in ROOTFS_SECTIONS][0]
        for section in self._fs_sections:
            if section.name == "app":
                outpath = dest / rootfsdir / "mnt" / "app"
            else:
                outpath = dest / rootfsdir
            self.extract_file_system(section, outpath)
        mode = "wb" if force else "xb"
        with open(dest / "uboot", mode) as f:
            f.write(self.uboot)
        with open(dest / "kernel", mode) as f:
            f.write(self.kernel)
        if (kcfg := self.get_kernel_config()) is not None:
            with open(dest / ".config", mode) as f:
                f.write(kcfg)
        if self.fdt is not None:
            with open(dest / "camera.dts", 'w' if force else 'x', encoding="utf8") as f:
                f.write(self.fdt.to_dts())


async def download(url: StrOrURL) -> Union[bytes, int]:
    """Return resource as bytes.

    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


def firmwares_from_zip(zip: Union[StrPath, IO[bytes]]) -> list[tuple[str, ReolinkFirmware]]:
    """Return a list of tuples, one for each firmware found in the ZIP.

    It is the caller's responsibility to close the firmware files.
    """
    fws = []
    with ZipFile(zip) as myzip:
        for name in myzip.namelist():
            file = myzip.open(name)
            if is_pak_file(file):
                fws.append((file.name, ReolinkFirmware.from_fd(file)))
            else:
                file.close()
    return fws


def get_info_from_files(files: InfoFiles) -> DVRInfo:
    xml: dict[str, str] = dict(fromstring(files["dvr.xml"]).items())
    info = {k: xml.get(k) for k in INFO_KEYS}
    info["version_file"] = files["version_file"].decode().strip()
    if not info.get("firmware_version_prefix"):
        thefile = files["dvr"] if files["dvr"] is not None else files["router"]
        match = re.search(rb"echo (v[23]\.0\.0)", thefile) if thefile is not None else None
        info["firmware_version_prefix"] = match.group(1).decode() if match else None
    return info  # type: ignore


def get_files_from_squashfs(fd: BinaryIO, offset: int = 0, closefd: bool = True) -> InfoFiles:
    # Firmwares using squashfs have either one or two file system
    # sections. When there is only one, the app directory is located at
    # /mnt/app. Otherwise it's the same as with cramfs and ubifs.
    files = dict.fromkeys(FILES)
    with SquashFsImage(fd, offset, closefd) as image:
        for name in files:
            path2 = posixpath.join("/mnt/app", name)
            if (file := (image.select(name) or image.select(path2))) is not None:
                files[name] = file.read_bytes()
    return files  # type: ignore


def get_files_from_ubifs(binbytes: Buffer) -> InfoFiles:
    # For now all firmwares using ubifs have two file system sections.
    # The interesting files are in the root directory of the "app" one.
    # Using select() with a relative path is enough.
    files = dict.fromkeys(FILES)
    with TempFile(binbytes) as tempfile:
        with UBIFS.from_file(tempfile) as image:
            for name in files:
                if (file := image.select(name)) is not None:
                    files[name] = file.read_bytes()
    return files  # type: ignore


def get_files_from_ubi(fd: BinaryIO, size: int, offset: int = 0) -> InfoFiles:
    fsbytes = get_fs_from_ubi(fd, size, offset)
    fs = FileType.from_magic(fsbytes[:4])
    if fs == FileType.UBIFS:
        return get_files_from_ubifs(fsbytes)
    elif fs == FileType.SQUASHFS:
        return get_files_from_squashfs(io.BytesIO(fsbytes))
    raise Exception("Unknown file system in UBI")


def get_files_from_cramfs(fd: BinaryIO, offset: int = 0, closefd: bool = True) -> InfoFiles:
    # For now all firmwares using cramfs have two file system sections.
    # The interesting files are in the root directory of the "app" one.
    # Using select() with a relative path is enough.
    files = dict.fromkeys(FILES)
    with Cramfs.from_fd(fd, offset, closefd) as cramfs:
        for name in files:
            if (file := cramfs.select(name)) is not None:
                files[name] = file.read_bytes()
    return files  # type: ignore


def is_url(string: StrOrURL) -> bool:
    return str(string).startswith("http")


def is_local_file(string: StrPath) -> bool:
    return Path(string).is_file()


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


async def firmwares_from_file(file_or_url: StrPathURL, use_cache: bool = True) -> list[tuple[Optional[str], ReolinkFirmware]]:
    """Return firmwares read from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK. On success return a
    list of 2-tuples where each tuple is of the form
    `(filename, firmware)`. When the argument is a URL, `filename` may
    be None. If the file is a ZIP the list might be empty.
    It is the caller's responsibility to close the firmware files.
    """
    file_or_url = str(file_or_url)
    if is_url(file_or_url):
        if use_cache and has_cache(file_or_url):
            return await firmwares_from_file(get_cache_file(file_or_url))
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
                return await asyncio.to_thread(firmwares_from_zip, zipfile)
            zipfile.close()
            raise Exception("Not a ZIP or a PAK file")
    elif is_local_file(file_or_url):
        file_or_url = Path(file_or_url)
        if is_zipfile(file_or_url):
            return await asyncio.to_thread(firmwares_from_zip, file_or_url)
        elif is_pak_file(file_or_url):
            return [(file_or_url.name, ReolinkFirmware.from_file(file_or_url))]
        raise Exception("Not a ZIP or a PAK file")
    raise Exception("Not a URL or file")


async def firmware_info(file_or_url: StrPathURL, use_cache: bool = True) -> list[dict[str, Any]]:
    """Retrieve firmware info from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK.
    """
    try:
        fws = await firmwares_from_file(file_or_url, use_cache)
    except Exception as e:
        return [{"file": file_or_url, "error": str(e)}]
    if not fws:
        return [{"file": file_or_url, "error": "No PAKs found in ZIP file"}]
    info = [{**await fw.get_info(), "file": file_or_url, "pak": pakname} for pakname, fw in fws]
    for _, fw in fws:
        fw.close()
    return info
