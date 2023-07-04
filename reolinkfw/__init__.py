import asyncio
import hashlib
import io
import posixpath
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse
from zipfile import ZipFile, is_zipfile

import aiohttp
from lxml.etree import fromstring
from lxml.html import document_fromstring
from pakler import PAK, is_pak_file
from pycramfs import Cramfs
from PySquashfsImage import SquashFsImage
from ubireader.ubi.defines import UBI_EC_HDR_MAGIC
from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.defines import UBIFS_NODE_MAGIC
from ubireader.ubifs.output import _process_reg_file

from reolinkfw.util import DummyLEB, get_fs_from_ubi

__version__ = "1.1.0"

FILES = ("version_file", "version.json", "dvr.xml", "dvr", "router")
INFO_KEYS = ("firmware_version_prefix", "board_type", "board_name", "build_date", "display_type_info", "detail_machine_type", "type")

SQUASHFS_MAGIC = b"hsqs"
CRAMFS_MAGIC = b'E=\xcd('


async def download(url):
    """Return resource as bytes.

    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


def extract_fs(pakbytes):
    """Return the fs.bin, app.bin or rootfs.bin file as bytes."""
    with PAK.from_bytes(pakbytes) as pak:
        sections = {s.name: s for s in pak.sections if s.name in ("fs", "app", "rootfs")}
        if len(sections) == 2:
            return pak.extract_section(sections["app"])
        elif len(sections) == 1:
            return pak.extract_section(sections.popitem()[1])
        else:
            return "No section found"


def extract_paks(zip) -> list[tuple[str, bytes]]:
    """Return a list of tuples, one for each PAK file found in the ZIP."""
    paks = []
    with ZipFile(zip) as myzip:
        for name in myzip.namelist():
            with myzip.open(name) as file:
                if is_pak_file(file):
                    paks.append((file.name, myzip.read(name)))
    return paks


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
    # Firmwares using squashfs have either one or two file system
    # sections. When there is only one, the app directory is located at
    # /mnt/app. Otherwise it's the same as with cramfs and ubifs.
    files = dict.fromkeys(FILES)
    with SquashFsImage.from_bytes(binbytes) as image:
        for name in files:
            path2 = posixpath.join("/mnt/app", name)
            if (file := (image.select(name) or image.select(path2))) is not None:
                files[name] = file.read_bytes()
    return files


def get_files_from_ubifs(binbytes):
    files = dict.fromkeys(FILES)
    with DummyLEB.from_bytes(binbytes) as leb:
        image = ubifs(leb)
        inodes = {}
        bad_blocks = []
        walk.index(image, image.master_node.root_lnum, image.master_node.root_offs, inodes, bad_blocks)
        for dent in inodes[1]['dent']:
            if dent.name in files:
                files[dent.name] = _process_reg_file(image, inodes[dent.inum], None)
    return files


def get_files_from_ubi(binbytes):
    fsbytes = get_fs_from_ubi(binbytes)
    if is_ubifs(fsbytes):
        return get_files_from_ubifs(fsbytes)
    elif is_squashfs(fsbytes):
        return get_files_from_squashfs(fsbytes)
    else:
        raise Exception("unknown file system in UBI")


def get_files_from_cramfs(binbytes):
    # For now all firmwares using cramfs have two file system sections.
    # The interesting files are in the root directory of the "app" one.
    # Using select() with a relative path is enough.
    files = dict.fromkeys(FILES)
    with Cramfs.from_bytes(binbytes) as cramfs:
        for name in files:
            if (file := cramfs.select(name)) is not None:
                files[name] = file.read_bytes()
    return files


def is_ubi(bytes_):
    return bytes_[:4] == UBI_EC_HDR_MAGIC


def is_squashfs(bytes_):
    return bytes_[:4] == SQUASHFS_MAGIC


def is_cramfs(bytes_):
    return bytes_[:4] == CRAMFS_MAGIC


def is_ubifs(bytes_):
    return bytes_[:4] == UBIFS_NODE_MAGIC


def is_url(string):
    return str(string).startswith("http")


def is_local_file(string):
    return Path(string).is_file()


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
    info = get_info_from_files(files)
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


async def get_paks(file_or_url) -> list[tuple[Optional[str], bytes]]:
    """Return PAK files read from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK. On success return a
    list of 2-tuples where each tuple is of the form
    `(pak_name, pak_bytes)`. When the argument is a URL, `pak_name` may
    be None. If the file is a ZIP the list might be empty.
    """
    if is_url(file_or_url):
        file_or_url = await direct_download_url(file_or_url)
        zip_or_pak_bytes = await download(file_or_url)
        if isinstance(zip_or_pak_bytes, int):
            raise Exception(f"HTTP error {zip_or_pak_bytes}")
        elif is_pak_file(zip_or_pak_bytes):
            pakname = dict(parse_qsl(urlparse(file_or_url).query)).get("name")
            return [(pakname, zip_or_pak_bytes)]
        else:
            with io.BytesIO(zip_or_pak_bytes) as f:
                if is_zipfile(f):
                    return await asyncio.to_thread(extract_paks, f)
            raise Exception("Not a ZIP or a PAK file")
    elif is_local_file(file_or_url):
        file_or_url = Path(file_or_url)
        if is_zipfile(file_or_url):
            return await asyncio.to_thread(extract_paks, file_or_url)
        elif is_pak_file(file_or_url):
            with open(file_or_url, "rb") as f:
                return [(file_or_url.name, f.read())]
        raise Exception("Not a ZIP or a PAK file")
    raise Exception("Not a URL or file")


async def get_info(file_or_url):
    """Retrieve firmware info from an on-disk file or a URL.

    The file or resource may be a ZIP or a PAK.
    """
    try:
        paks = await get_paks(file_or_url)
    except Exception as e:
        return [{"file": file_or_url, "error": str(e)}]
    if not paks:
        return [{"file": file_or_url, "error": "No PAKs found in ZIP file"}]
    return [{**await get_info_from_pak(pakbytes), "file": file_or_url, "pak": pakname} for pakname, pakbytes in paks]
