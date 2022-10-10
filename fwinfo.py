import asyncio
import io
import re
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import aiohttp
from PySquashfsImage import SquashFsImage

import mypakler


def extract_fs(pakbytes):
    """Return the fs.bin file as bytes."""
    section_count = mypakler.guess_section_count(pakbytes)
    if not section_count:
        print("error: no section_count")
        return None
    header = mypakler.read_header(pakbytes, section_count)
    for section in header.sections:
        if section.len and section.name == "fs":
            return mypakler.extract_section(pakbytes, section)
    return None


def extract_pak(zip):
    """Return the PAK file as bytes."""
    with ZipFile(zip) as myzip:
        pak = [f for f in myzip.namelist() if any(s in Path(f).suffix for s in (".pak", ".IPC"))][0]
        return myzip.read(pak)


def get_build_date(version):
    build_date = version.split('_')[1][:-2]
    return date(2000 + int(build_date[:2]), int(build_date[2:4]), int(build_date[4:]))


def get_info_from_image(squashfsimage):
    xml = ''
    thefile = None
    version = None
    for f in squashfsimage.root.findAll():
        if f.getPath() == "/mnt/app/version_file":
            version = f.getContent().decode().strip()
        elif f.getPath() == "/mnt/app/dvr.xml":
            xml = f.getContent().decode().strip()
        elif f.getPath() in ("/mnt/app/dvr", "/mnt/app/router"):
            thefile = f
    if not xml and thefile is None and version is None:
        return {"error": "app empty"}
    if (match := re.search('firmware_version_prefix="(.*?)"', xml)):
        prefix = match.group(1)
    elif thefile is not None:
        prefix = re.search(b"echo (v[23]\.0\.0)", thefile.getContent()).group(1).decode()
    else:
        prefix = None
    # print("board_type", re.search('board_type="(.*?)"', xml).group(1))
    # print("board_name", re.search('board_name="(.*?)"', xml).group(1))
    return {
        "version": f"{prefix}.{version}",
        "model": re.search('display_type_info="(.*?)"', xml).group(1),
        # "board_type": re.search('board_type="(.*?)"', xml).group(1),
        # "board_name": re.search('board_name="(.*?)"', xml).group(1),
        "hw_ver": re.search('detail_machine_type="(.*?)"', xml).group(1),
        "display_time": get_build_date(version)
    }


async def download_zip(url):
    """Return zip file as bytes.
    
    Return the status code of the request if it is not 200.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read() if resp.status == 200 else resp.status


async def get_info(url):
    """Retreive firmware info from URL."""
    zip = await download_zip(url)
    # print("downloaded", url)
    if isinstance(zip, int):
        return {"url": url, "error": zip}
    with io.BytesIO(zip) as f:
        pakbytes = extract_pak(f)
    binbytes = await asyncio.to_thread(extract_fs, pakbytes)
    if binbytes is None:
        return {"url": url, "error": "no bin"}
    image = SquashFsImage()
    binfile = io.BytesIO(binbytes)
    try:
        image.setFile(binfile)
    except Exception as e:
        return {"url": url, "error": str(e)}
    info = await asyncio.to_thread(get_info_from_image, image)
    image.close()
    # print(info)
    return {**info, "url": url}
