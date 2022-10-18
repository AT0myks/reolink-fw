import asyncio
import io
import re
import zlib
from pathlib import Path
from zipfile import ZipFile, is_zipfile

import aiohttp
from lxml.etree import fromstring
from PySquashfsImage import SquashFsImage
from ubireader.ubi import ubi
from ubireader.ubi.defines import UBI_EC_HDR_MAGIC
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.output import _process_reg_file
from ubireader.utils import guess_peb_size

from . import mypakler
from .cramfs import Cramfs
from .tmpfile import TempFile

FILES = ("version_file", "version.json", "dvr.xml", "dvr", "router")
INFO_KEYS = ("firmware_version_prefix", "board_type", "board_name", "build_date", "display_type_info", "detail_machine_type", "type")


async def download_zip(url):
    """Return ZIP file as bytes.
    
    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


def extract_fs(pakbytes):
    """Return the fs.bin or app.bin file as bytes."""
    section_count = mypakler.guess_section_count(pakbytes)
    if not section_count:
        return "Could not guess section count"
    header = mypakler.read_header(pakbytes, section_count)
    sections = {s.name: s for s in header.sections if s.name in ("fs", "app")}
    if len(sections) == 2:
        return mypakler.extract_section(pakbytes, sections["app"])
    elif len(sections) == 1:
        return mypakler.extract_section(pakbytes, sections.popitem()[1])
    else:
        return "No section found"


def extract_pak(zip):
    """Return the PAK file as bytes if found in the ZIP, else None."""
    with ZipFile(zip) as myzip:
        pak = [f for f in myzip.namelist() if any(s in Path(f).suffix for s in (".pak", ".IPC"))]
        if not pak:
            return None
        return myzip.read(pak[0])


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
    image = SquashFsImage()
    image.setFile(io.BytesIO(binbytes))
    files = dict.fromkeys(FILES)
    for f in image.root.findAll():
        name = Path(f.getPath()).name
        if name in files:
            files[name] = f.getContent()
    image.close()
    return files


def get_files_from_ubi(binbytes):
    with TempFile(binbytes) as t:
        start_offset = 0
        block_size = guess_peb_size(t)
        ubi_obj = ubi(ubi_file(t, block_size, start_offset, None))

    vol_blocks = ubi_obj.images[0].volumes["app"].get_blocks(ubi_obj.blocks)
    ubifs_obj = ubifs(leb_virtual_file(ubi_obj, vol_blocks))
    inodes = {}
    bad_blocks = []
    walk.index(ubifs_obj, ubifs_obj.master_node.root_lnum, ubifs_obj.master_node.root_offs, inodes, bad_blocks)
    files = dict.fromkeys(FILES)
    for dent in inodes[1]['dent']:
        if dent.name in files:
            files[dent.name] = _process_reg_file(ubifs_obj, inodes[dent.inum], None)
    return files


def get_files_from_cramfs(binbytes):
    files = dict.fromkeys(FILES)
    for child in Cramfs.from_bytes(binbytes).super_block.root.as_dir.children:
        if child.type == Cramfs.Inode.FileType.reg_file:
            name = child.name.strip('\x00')
            if name in files:
                file = child.as_reg_file
                content = b''
                for block in file.raw_blocks:
                    content += zlib.decompress(block)
                files[name] = content
    return files


def is_ubi(bytes_):
    return bytes_[:4] == UBI_EC_HDR_MAGIC


def is_squashfs(bytes_):
    return bytes_[:4] == b"hsqs"


def is_cramfs(bytes_):
    return bytes_[:4] == b'E=\xcd('


def is_url(string):
    return str(string).startswith("http")


def is_local_file(string):
    return Path(string).is_file()


def is_pak(filename):
    try:
        with open(filename, "rb") as f:
            return f.read(4) == b"\x13Yr2"
    except OSError:
        return False


async def get_info(file_or_url):
    """Retreive firmware info from an on-disk file or a URL.
    
    The file may be a ZIP or a PAK.
    """
    if is_url(file_or_url):
        type_ = "url"
        zipbytes = await download_zip(file_or_url)
        if isinstance(zipbytes, int):
            return {type_: file_or_url, "error": zipbytes}
        with io.BytesIO(zipbytes) as f:
            if is_zipfile(f):
                pakbytes = extract_pak(f)
            else:
                return {type_: file_or_url, "error": "Not a ZIP file"}
    elif is_local_file(file_or_url):
        type_ = "file"
        if is_zipfile(file_or_url):
            pakbytes = extract_pak(file_or_url)
        elif is_pak(file_or_url):
            with open(file_or_url, "rb") as f:
                pakbytes = f.read()
        else:
            return {type_: file_or_url, "error": "Not a ZIP or a PAK"}
    else:
        return {"arg": file_or_url, "error": "Not a URL or file"}
    if pakbytes is None:
        return {type_: file_or_url, "error": "PAK not found in ZIP file"}
    binbytes = await asyncio.to_thread(extract_fs, pakbytes)
    if isinstance(binbytes, str):
        return {type_: file_or_url, "error": binbytes}
    if is_cramfs(binbytes):
        func = get_files_from_cramfs
    elif is_ubi(binbytes):
        func = get_files_from_ubi
    elif is_squashfs(binbytes):
        func = get_files_from_squashfs
    else:
        return {type_: file_or_url, "error": "Unrecognized image type"}
    files = await asyncio.to_thread(func, binbytes)
    info = await asyncio.to_thread(get_info_from_files, files)
    return {**info, type_: file_or_url}
