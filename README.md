# reolinkfw

<p align="left">
<a><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/reolinkfw"></a>
<a href="https://pypi.org/project/reolinkfw/"><img alt="PyPI" src="https://img.shields.io/pypi/v/reolinkfw"></a>
<!-- <a href="https://github.com/psf/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a> -->
<a href="https://github.com/AT0myks/reolink-fw/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/pypi/l/reolinkfw"></a>
</p>

* [What is it](#what-is-it)
* [Requirements](#requirements)
* [Installation](#installation)
* [Usage](#usage)
* [Notes](#notes)
* [Issues](#issues)

## What is it

This is a small tool to get information on Reolink firmwares.
It's able to read ZIP and PAK files, either local or at the end of a URL.
The info is read from the files contained in the firmware (mainly `dvr.xml`) and
if we trust them to be 100% correct, it allows to know precisely which
model/hardware version a given firmware is targeting.

It was first developed as part of
[another Reolink-related project](https://github.com/AT0myks/reolink-fw-archive)
but became its own thing.

Currently it doesn't do anything more and is probably not that useful outside of
this other project.

## Requirements

- Python 3.9+
- [python-lzo](https://github.com/jd-boyd/python-lzo) (manual steps not required on Windows, see below)

## Installation

On Linux and macOS, install
[python-lzo](https://github.com/jd-boyd/python-lzo#installation) first. Then

```
pip install reolinkfw
```

lxml doesn't have a wheel for Python 3.11 on macOS, you might want to look
[here](https://lxml.de/installation.html).

python-lzo doesn't have wheels for Linux and for Python 3.9+ on macOS.
A [PR](https://github.com/jd-boyd/python-lzo/pull/75) is open to have them be
provided on PyPI.

## Usage

### Command line

#### Info

```
usage: reolinkfw info [-h] [--no-cache] [-j [indent]] file_or_url

positional arguments:
  file_or_url                   URL or on-disk file

optional arguments:
  -h, --help                    show this help message and exit
  --no-cache                    don't use cache for remote files (URLs)
  -j [indent], --json [indent]  JSON output with optional indentation level for pretty print
```

Example:

```
$ reolinkfw info RLC-410-5MP_20_20052300.zip
IPC_51516M5M.20_20052300.RLC-410-5MP.OV05A10.5MP.REOLINK.pak
Model:                RLC-410-5MP
Hardware info:        IPC_51516M5M
Device type:          IPC
Firmware version:     v3.0.0.20_20052300
Build date:           2020-05-23
Architecture:         MIPS
OS:                   Linux
Kernel image name:    Linux-4.1.0
Linux banner:         Linux version 4.1.0 (lwy@ubuntu) (gcc version 4.9.3 (Buildroot 2015.11.1-00003-gfd1edb1) ) #1 PREEMPT Tue Feb 26 18:19:48 CST 2019
U-Boot version:       U-Boot 2014.07 (Feb 26 2019 - 18:20:07)
U-Boot compiler:      mipsel-24kec-linux-uclibc-gcc.br_real (Buildroot 2015.11.1-00003-gfd1edb1) 4.9.3
U-Boot linker:        GNU ld (GNU Binutils) 2.24
File system:          squashfs
File system sections: fs
Board vendor:         Novatek
Board:                Novatek NA51023 evaluation board
```

Or with JSON output:

```
$ reolinkfw info RLC-410-5MP_20_20052300.zip -j 2
[
  {
    "firmware_version_prefix": "v3.0.0",
    "board_type": "IPC_51516M5M",
    "board_name": "IPC_51516M5M",
    "build_date": "200523",
    "display_type_info": "RLC-410-5MP",
    "detail_machine_type": "IPC_51516M5M",
    "type": "IPC",
    "version_file": "20_20052300",
    "os": "Linux",
    "architecture": "MIPS",
    "kernel_image_name": "Linux-4.1.0",
    "uboot_version": "U-Boot 2014.07 (Feb 26 2019 - 18:20:07)",
    "uboot_compiler": "mipsel-24kec-linux-uclibc-gcc.br_real (Buildroot 2015.11.1-00003-gfd1edb1) 4.9.3",
    "uboot_linker": "GNU ld (GNU Binutils) 2.24",
    "linux_banner": "Linux version 4.1.0 (lwy@ubuntu) (gcc version 4.9.3 (Buildroot 2015.11.1-00003-gfd1edb1) ) #1 PREEMPT Tue Feb 26 18:19:48 CST 2019",
    "board": "Novatek NA51023 evaluation board",
    "board_vendor": "Novatek",
    "filesystems": [
      {
        "name": "fs",
        "type": "squashfs"
      }
    ],
    "sha256": "6ef371a51b61d7b21d8f7016d90b5fc1ed3eaa8a3f30f1e202a3474bfb4807e5",
    "file": "RLC-410-5MP_20_20052300.zip",
    "pak": "IPC_51516M5M.20_20052300.RLC-410-5MP.OV05A10.5MP.REOLINK.pak"
  }
]
```

`file` is the given argument, a file or URL. The value of `pak` depends on the
argument. If it's a local or remote ZIP file it will be the path of the PAK file
inside it. If it's a remote PAK file, it will be the value of the `name` query
parameter or `None` if not found. And finally for a local PAK file it will be
the file name.

#### Extract

```
usage: reolinkfw extract [-h] [--no-cache] [-d DEST] [-f] file_or_url

Extract the file system from a Reolink firmware

positional arguments:
  file_or_url           URL or on-disk file

optional arguments:
  -h, --help            show this help message and exit
  --no-cache            don't use cache for remote files (URLs)
  -d DEST, --dest DEST  destination directory. Default: current directory
  -f, --force           overwrite existing files. Does not apply to UBIFS. Default: False
```

A firmware's file system can be laid out in two different ways inside a PAK file:
1. In a single section named `fs` or `rootfs` containing the whole file system
1. In two sections with the second one named `app` containing the files that go in `/mnt/app`

In the second case, the contents of `app` will be extracted to the appropriate
location so that the files are organized the same way as they are when the
camera is running.

Consider the result of this command a one-way operation.
You should not use it to repack a custom firmware.

### As a library

```py
from reolinkfw import ReolinkFirmware, get_info

url = "https://reolink-storage.s3.amazonaws.com/website/firmware/20200523firmware/RLC-410-5MP_20_20052300.zip"
print(get_info(url))
file = "/home/ben/RLC-410-5MP_20_20052300.zip"
print(get_info(file))
with ReolinkFirmware.from_file(file) as fw:
    fw.extract()
```

In most cases where a URL is used, it will be a direct link to the file
(meaning if you were to open it in a browser, the download would start).

But in some cases (for example beta firmwares) Reolink gives a Google Drive or
a bit.ly link (that redirects to a Google Drive link).

These URLs are automatically handled so that you don't have to figure out the
"real" download link, and in this case the `url` value(s) in the result JSON
will not be the link that you gave but the direct download one.

However the Google Drive folder links (`drive.google.com/drive/folders`) are not
handled and in these cases you must find the real URL, or you can also download
the file.

## Notes

There are 3 types of file systems used for Reolink firmwares:
- [cramfs](https://www.kernel.org/doc/html/latest/filesystems/cramfs.html) (handled by [pycramfs](https://github.com/AT0myks/pycramfs))
- [squashfs](https://www.kernel.org/doc/html/latest/filesystems/squashfs.html) (handled by [PySquashfsImage](https://github.com/matteomattei/PySquashfsImage))
- [UBIFS](https://www.kernel.org/doc/html/latest/filesystems/ubifs.html) (handled by [ubi_reader](https://github.com/jrspruitt/ubi_reader))

Some ZIP files provided by Reolink contain multiple PAKs. This is why `get_info`
always returns a list.
