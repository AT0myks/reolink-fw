#!/usr/bin/env python3

import argparse
import asyncio
import json

from reolinkfw import get_info


def main():
    parser = argparse.ArgumentParser(description="Extract information from Reolink firmware files")
    parser.add_argument("file_or_url", help="URL or on-disk file")
    parser.add_argument("-i", "--indent", type=int, help="indent level for pretty print")
    args = parser.parse_args()
    info = asyncio.run(get_info(args.file_or_url))
    print(json.dumps(info, indent=args.indent))


if __name__ == "__main__":
    main()