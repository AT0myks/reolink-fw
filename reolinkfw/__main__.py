#!/usr/bin/env python3

import argparse
import asyncio
import json

from reolinkfw import get_info, __version__


def info(args):
    info = asyncio.run(get_info(args.file_or_url))
    print(json.dumps(info, indent=args.indent))


def main():
    parser = argparse.ArgumentParser(description="Extract information from Reolink firmware files")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}", help="print version")
    subparsers = parser.add_subparsers(required=True)
    parser_i = subparsers.add_parser("info")
    parser_i.add_argument("file_or_url", help="URL or on-disk file")
    parser_i.add_argument("-i", "--indent", type=int, help="indent level for pretty print")
    parser_i.set_defaults(func=info)
    args = parser.parse_args()
    args.func(args)    


if __name__ == "__main__":
    main()