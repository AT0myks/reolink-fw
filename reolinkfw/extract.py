from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from pakler import PAK
from pycramfs import Cramfs
from pycramfs.extract import extract_dir as extract_cramfs
from PySquashfsImage import SquashFsImage
from PySquashfsImage.extract import extract_dir as extract_squashfs
from ubireader.ubifs import ubifs
from ubireader.ubifs.output import extract_files as extract_ubifs

from reolinkfw import FS_SECTIONS, ROOTFS_SECTIONS, FileSystem
from reolinkfw.util import DummyLEB, get_fs_from_ubi


def extract_file_system(fs_bytes, dest: Path = None):
    dest = (Path.cwd() / "reolink_fs") if dest is None else dest
    dest.mkdir(parents=True, exist_ok=True)
    fs = FileSystem.from_magic(fs_bytes[:4])
    if fs == FileSystem.UBI:
        extract_file_system(get_fs_from_ubi(fs_bytes), dest)
    elif fs == FileSystem.UBIFS:
        with DummyLEB.from_bytes(fs_bytes) as leb:
            with redirect_stdout(StringIO()):
                # If files already exist they are not written again.
                extract_ubifs(ubifs(leb), dest)
    elif fs == FileSystem.SQUASHFS:
        with SquashFsImage.from_bytes(fs_bytes) as image:
            extract_squashfs(image.root, dest, True)
    elif fs == FileSystem.CRAMFS:
        with Cramfs.from_bytes(fs_bytes) as image:
            extract_cramfs(image.rootdir, dest, True)
    else:
        raise Exception("Unknown file system")


def extract_pak(pak: PAK, dest: Path = None, force: bool = False):
    dest = (Path.cwd() / "reolink_firmware") if dest is None else dest
    dest.mkdir(parents=True, exist_ok=force)
    rootfsdir = [s.name for s in pak.sections if s.name in ROOTFS_SECTIONS][0]
    for section in pak.sections:
        if section.name in FS_SECTIONS:
            if section.name == "app":
                outpath = dest / rootfsdir / "mnt" / "app"
            else:
                outpath = dest / rootfsdir
            extract_file_system(pak.extract_section(section), outpath)
