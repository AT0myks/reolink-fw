from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Optional

from pakler import PAK, Section
from pycramfs import Cramfs
from pycramfs.extract import extract_dir as extract_cramfs
from PySquashfsImage import SquashFsImage
from PySquashfsImage.extract import extract_dir as extract_squashfs
from ubireader.ubifs import ubifs
from ubireader.ubifs.output import extract_files as extract_ubifs
from ubireader.ubi_io import ubi_file
from ubireader.utils import guess_leb_size

from reolinkfw import FS_SECTIONS, ROOTFS_SECTIONS
from reolinkfw.tmpfile import TempFile
from reolinkfw.util import FileType, closing_ubifile, get_fs_from_ubi


def extract_file_system(pak: PAK, section: Section, dest: Optional[Path] = None) -> None:
    dest = (Path.cwd() / "reolink_fs") if dest is None else dest
    dest.mkdir(parents=True, exist_ok=True)
    pak._fd.seek(section.start)
    fs = FileType.from_magic(pak._fd.read(4))
    if fs == FileType.UBI:
        fs_bytes = get_fs_from_ubi(pak._fd, section.len, section.start)
        fs = FileType.from_magic(fs_bytes[:4])
        if fs == FileType.UBIFS:
            with TempFile(fs_bytes) as file:
                block_size = guess_leb_size(file)
                with closing_ubifile(ubi_file(file, block_size)) as ubifile:
                    with redirect_stdout(StringIO()):
                        # Files that already exist are not written again.
                        extract_ubifs(ubifs(ubifile), dest)
        elif fs == FileType.SQUASHFS:
            with SquashFsImage.from_bytes(fs_bytes) as image:
                extract_squashfs(image.root, dest, True)
        else:
            raise Exception("Unknown file system in UBI")
    elif fs == FileType.SQUASHFS:
        with SquashFsImage(pak._fd, section.start, False) as image:
            extract_squashfs(image.root, dest, True)
    elif fs == FileType.CRAMFS:
        with Cramfs.from_fd(pak._fd, section.start, False) as image:
            extract_cramfs(image.rootdir, dest, True)
    else:
        raise Exception("Unknown file system")


def extract_pak(pak: PAK, dest: Optional[Path] = None, force: bool = False) -> None:
    dest = (Path.cwd() / "reolink_firmware") if dest is None else dest
    dest.mkdir(parents=True, exist_ok=force)
    rootfsdir = [s.name for s in pak.sections if s.name in ROOTFS_SECTIONS][0]
    for section in pak.sections:
        if section.name in FS_SECTIONS:
            if section.name == "app":
                outpath = dest / rootfsdir / "mnt" / "app"
            else:
                outpath = dest / rootfsdir
            extract_file_system(pak, section, outpath)
