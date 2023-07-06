#!/usr/bin/env python3

import argparse
import asyncio
import json
import sys
from pathlib import Path, PurePath

from reolinkfw import __version__, get_info, get_paks
from reolinkfw.extract import extract_pak
from reolinkfw.util import sha256_pak


def info(args: argparse.Namespace) -> None:
    info = asyncio.run(get_info(args.file_or_url))
    print(json.dumps(info, indent=args.indent, default=str))


async def extract(args: argparse.Namespace) -> None:
    paks = await get_paks(args.file_or_url)
    if not paks:
        raise Exception("No PAKs found in ZIP file")
    dest = Path.cwd() if args.dest is None else args.dest
    for pakname, pakfile in paks:
        name = sha256_pak(pakfile) if pakname is None else PurePath(pakname).stem
        await asyncio.to_thread(extract_pak, pakfile, dest / name, args.force)
        pakfile.close()


def main():
    parser = argparse.ArgumentParser(description="Extract information and files from Reolink firmwares")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(required=True)

    parser_i = subparsers.add_parser("info")
    parser_i.add_argument("file_or_url", help="URL or on-disk file")
    parser_i.add_argument("-i", "--indent", type=int, help="indent level for pretty print")
    parser_i.set_defaults(func=info)

    descex = "Extract the file system from a Reolink firmware"
    parser_e = subparsers.add_parser("extract", help=descex.lower(), description=descex)
    parser_e.add_argument("file_or_url", help="URL or on-disk file")
    parser_e.add_argument("-d", "--dest", type=Path, help="destination directory. Default: current directory")
    parser_e.add_argument("-f", "--force", action="store_true", help="overwrite existing files. Does not apply to UBIFS. Default: %(default)s")
    parser_e.set_defaults(func=extract)

    args = parser.parse_args()
    try:
        if asyncio.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)
    except Exception as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
