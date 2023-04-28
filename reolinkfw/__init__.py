import asyncio
import hashlib
import io
import re
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, is_zipfile

import aiohttp
from lxml.etree import fromstring
from lxml.html import document_fromstring
from pycramfs import Cramfs
from PySquashfsImage import SquashFsImage
from ubireader.ubi import ubi
from ubireader.ubi.defines import UBI_EC_HDR_MAGIC
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.output import _process_reg_file
from ubireader.utils import guess_peb_size

from . import mypakler
from .tmpfile import TempFile

__version__ = "1.1.0"

FILES = ("version_file", "version.json", "dvr.xml", "dvr", "router")
INFO_KEYS = ("firmware_version_prefix", "board_type", "board_name", "build_date", "display_type_info", "detail_machine_type", "type")

SQUASHFS_MAGIC = b"hsqs"
CRAMFS_MAGIC = b'E=\xcd('
PAK_MAGIC = b"\x13Yr2"


async def download(url):
    """Return resource as bytes.

    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


def extract_fs(pakbytes):
    """Return the fs.bin, app.bin or rootfs.bin file as bytes."""
    section_count = mypakler.guess_section_count(pakbytes)
    if not section_count:
        return "Could not guess section count"
    header = mypakler.read_header(pakbytes, section_count)
    sections = {s.name: s for s in header.sections if s.name in ("fs", "app", "rootfs")}
    if len(sections) == 2:
        return mypakler.extract_section(pakbytes, sections["app"])
    elif len(sections) == 1:
        return mypakler.extract_section(pakbytes, sections.popitem()[1])
    else:
        return "No section found"


def extract_paks(zip):
    """Return a list of unique PAK files found in the ZIP."""
    paks = set()
    with ZipFile(zip) as myzip:
        for name in myzip.namelist():
            with myzip.open(name) as file:
                if is_pak(file):
                    paks.add(myzip.read(name))
    return sorted(paks)  # Always return in the same order.


def get_info_from_files(files):
    xml = dict(fromstring(files["dvr.xml"]).items())
    info = {k: xml.get(k) for k in INFO_KEYS}
    info["version_file"] = files["version_file"].decode().strip()
    if not info.get("firmware_version_prefix"):
        thefile = files["dvr"] if files["dvr"] is not None else files["router"]
        match = re.search(b"echo (v[23]\.0\.0)", thefile) if thefile is not None else None
        info["firmware_version_prefix"] = match.group(1).decode() if match else None
    return info


def get_files_from_squashfs(binbytes):
    files = dict.fromkeys(FILES)
    image = SquashFsImage()
    image.set_file(io.BytesIO(binbytes))
    for file in image.root.find_all():
        name = PurePosixPath(file.path).name
        if name in files:
            files[name] = file.read_bytes()
    image.close()
    return files


def get_files_from_ubi(binbytes):
    files = dict.fromkeys(FILES)
    with TempFile(binbytes) as t:
        block_size = guess_peb_size(t)
        ubi_obj = ubi(ubi_file(t, block_size))
        vol_blocks = ubi_obj.images[0].volumes["app"].get_blocks(ubi_obj.blocks)
        ubifs_obj = ubifs(leb_virtual_file(ubi_obj, vol_blocks))
        inodes = {}
        bad_blocks = []
        walk.index(ubifs_obj, ubifs_obj.master_node.root_lnum, ubifs_obj.master_node.root_offs, inodes, bad_blocks)
        for dent in inodes[1]['dent']:
            if dent.name in files:
                files[dent.name] = _process_reg_file(ubifs_obj, inodes[dent.inum], None)
        ubi_obj._file._fhandle.close()
    return files


def get_files_from_cramfs(binbytes):
    files = dict.fromkeys(FILES)
    with Cramfs.from_bytes(binbytes) as cramfs:
        for name in files:
            if (file := cramfs.find(name)) is not None:
                files[name] = file.read_bytes()
    return files


def is_ubi(bytes_):
    return bytes_[:4] == UBI_EC_HDR_MAGIC


def is_squashfs(bytes_):
    return bytes_[:4] == SQUASHFS_MAGIC


def is_cramfs(bytes_):
    return bytes_[:4] == CRAMFS_MAGIC


def is_url(string):
    return str(string).startswith("http")


def is_local_file(string):
    return Path(string).is_file()


def _is_pak(file):
    return file.read(4) == PAK_MAGIC


def is_pak(file):
    if isinstance(file, bytes):
        return _is_pak(io.BytesIO(file))
    elif hasattr(file, "read"):
        return _is_pak(file)
    try:
        with open(file, "rb") as f:
            return _is_pak(f)
    except OSError:
        return False


def sha256(bytes_):
    return hashlib.sha256(bytes_).hexdigest()


async def get_info_from_pak(pakbytes):
    ha = sha256(pakbytes)
    binbytes = await asyncio.to_thread(extract_fs, pakbytes)
    if isinstance(binbytes, str):
        return {"error": binbytes, "sha256": ha}
    if is_cramfs(binbytes):
        func = get_files_from_cramfs
    elif is_ubi(binbytes):
        func = get_files_from_ubi
    elif is_squashfs(binbytes):
        func = get_files_from_squashfs
    else:
        return {"error": "Unrecognized image type", "sha256": ha}
    files = await asyncio.to_thread(func, binbytes)
    info = await asyncio.to_thread(get_info_from_files, files)
    return {**info, "sha256": ha}


async def direct_download_url(url):
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


async def get_info(file_or_url):
    """Retrieve firmware info from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK.
    """
    if is_url(file_or_url):
        type_ = "url"
        file_or_url = await direct_download_url(file_or_url)
        zip_or_pak_bytes = await download(file_or_url)
        if isinstance(zip_or_pak_bytes, int):
            return [{type_: file_or_url, "error": zip_or_pak_bytes}]
        elif is_pak(zip_or_pak_bytes):
            paks = [zip_or_pak_bytes]
        else:
            with io.BytesIO(zip_or_pak_bytes) as f:
                if is_zipfile(f):
                    paks = extract_paks(f)
                else:
                    return [{type_: file_or_url, "error": "Not a ZIP or a PAK file"}]
    elif is_local_file(file_or_url):
        type_ = "file"
        if is_zipfile(file_or_url):
            paks = extract_paks(file_or_url)
        elif is_pak(file_or_url):
            with open(file_or_url, "rb") as f:
                paks = [f.read()]
        else:
            return [{type_: file_or_url, "error": "Not a ZIP or a PAK file"}]
    else:
        return [{"arg": file_or_url, "error": "Not a URL or file"}]
    if not paks:
        return [{type_: file_or_url, "error": "no PAKs found in ZIP file"}]
    return [{**await get_info_from_pak(pak), type_: file_or_url} for pak in paks]
