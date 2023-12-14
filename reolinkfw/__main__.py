#!/usr/bin/env python3

import asyncio
import json
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime
from pathlib import Path, PurePath

from reolinkfw import __version__, get_info, get_paks

HW_FIELDS = ("board_type", "detail_machine_type", "board_name")


async def info(args: Namespace) -> None:
    pak_infos = await get_info(args.file_or_url, not args.no_cache)
    if args.json is None:
        width = 21
        for idx, info in enumerate(pak_infos):
            info = Namespace(**info)
            fs_types = set(fs["type"] for fs in info.filesystems)
            fs_names = [fs["name"] for fs in info.filesystems]
            version = f"{info.firmware_version_prefix}.{info.version_file}"
            hw_names = set(getattr(info, key) for key in HW_FIELDS)
            build_date = datetime.strptime(info.build_date, "%y%m%d").date()
            print(info.pak)
            print(f"{'Model:':{width}}", info.display_type_info)
            print(f"{'Hardware info:':{width}}", ', '.join(sorted(hw_names)))
            print(f"{'Device type:':{width}}", info.type)
            print(f"{'Firmware version:':{width}}", version)
            print(f"{'Build date:':{width}}", build_date)
            print(f"{'Architecture:':{width}}", info.architecture)
            print(f"{'OS:':{width}}", info.os)
            print(f"{'Kernel image name:':{width}}", info.kernel_image_name)
            print(f"{'U-Boot version:':{width}}", info.uboot_version or "Unknown")
            print(f"{'File system:':{width}}", ', '.join(sorted(fs_types)))
            print(f"{'File system sections:':{width}}", ', '.join(fs_names))
            if idx != len(pak_infos) - 1:
                print()
    else:
        indent = None if args.json < 0 else args.json
        print(json.dumps(pak_infos, indent=indent, default=str))


async def extract(args: Namespace) -> None:
    paks = await get_paks(args.file_or_url, not args.no_cache)
    if not paks:
        raise Exception("No PAKs found in ZIP file")
    dest = Path.cwd() if args.dest is None else args.dest
    for pakname, pakfile in paks:
        name = pakfile.sha256() if pakname is None else PurePath(pakname).stem
        await asyncio.to_thread(pakfile.extract_pak, dest / name, args.force)
        pakfile.close()


def main() -> None:
    parser = ArgumentParser(description="Extract information and files from Reolink firmwares")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(required=True)

    pcache = ArgumentParser(add_help=False)
    pcache.add_argument("--no-cache", action="store_true", help="don't use cache for remote files (URLs)")

    parser_i = subparsers.add_parser("info", parents=[pcache])
    parser_i.add_argument("file_or_url", help="URL or on-disk file")
    parser_i.add_argument("-j", "--json", nargs='?', type=int, const=-1, metavar="indent", help="JSON output with optional indentation level for pretty print")
    parser_i.set_defaults(func=info)

    descex = "Extract the file system from a Reolink firmware"
    parser_e = subparsers.add_parser("extract", parents=[pcache], help=descex.lower(), description=descex)
    parser_e.add_argument("file_or_url", help="URL or on-disk file")
    parser_e.add_argument("-d", "--dest", type=Path, help="destination directory. Default: current directory")
    parser_e.add_argument("-f", "--force", action="store_true", help="overwrite existing files. Does not apply to UBIFS. Default: %(default)s")
    parser_e.set_defaults(func=extract)

    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except Exception as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
