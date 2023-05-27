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
The info is read from the files contained in the firmware (mainly `dvr.xml`) and if we trust them to be 100% correct, it allows to know precisely which model/hardware version a given firmware is targeting.

It was first developed as part of [another Reolink-related project](https://github.com/AT0myks/reolink-fw-archive) but became its own thing.

Currently it doesn't do anything more and is probably not that useful outside of this other project.

## Requirements

- Python 3.9+
- [python-lzo](https://github.com/jd-boyd/python-lzo) (see below for installation)

## Installation

### Windows

To ease the pain of building/installing python-lzo on Windows I built wheels for it (only for CPython on Windows 64-bit).
[Download](https://github.com/AT0myks/reolink-fw/releases/tag/v1.0.0) the one for your Python version and install it:

```
pip install python_lzo-X.Y-cp3Z-cp3Z-win_amd64.whl
pip install reolinkfw
```

A [PR](https://github.com/jd-boyd/python-lzo/pull/65) is open to have python-lzo wheels be officially provided on PyPI.

If you are having problems installing lxml on Python 3.11, see [here](https://stackoverflow.com/a/33785756).

### Non-Windows

Install [python-lzo](https://github.com/jd-boyd/python-lzo#installation) then

```
pip install reolinkfw
```

## Usage

### Command line

```
$ reolinkfw info file_or_url
```

Example:

```
$ reolinkfw info RLC-410-5MP_20_20052300.zip -i 2
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
file name.

### As a library

```py
import reolinkfw
url = "https://reolink-storage.s3.amazonaws.com/website/firmware/20200523firmware/RLC-410-5MP_20_20052300.zip"
print(reolinkfw.get_info(url))
file = "/home/ben/RLC-410-5MP_20_20052300.zip"
print(reolinkfw.get_info(file))
```

In most cases where a URL is used, it will be a direct link to the file (meaning if you were to open it in a browser, the download would start).

But in some cases (for example beta firmwares) Reolink gives a Google Drive or a bit.ly link (that redirects to a Google Drive link).

These URLs are automatically handled so that you don't have to figure out the "real" download link, and in this case the `url` value(s) in the result JSON will not be the link that you gave but the direct download one.

However the Google Drive folder links (`drive.google.com/drive/folders`) are not handled and in these cases you must find the real URL, or you can also download the file.

## Notes

There are at least 3 types of file systems used for Reolink firmwares:
- [cramfs](https://www.kernel.org/doc/html/latest/filesystems/cramfs.html) (handled by [pycramfs](https://github.com/AT0myks/pycramfs))
- [squashfs](https://www.kernel.org/doc/html/latest/filesystems/squashfs.html) (handled by [PySquashfsImage](https://github.com/matteomattei/PySquashfsImage))
- [UBIFS](https://www.kernel.org/doc/html/latest/filesystems/ubifs.html) (handled by [ubi_reader](https://github.com/jrspruitt/ubi_reader))

Some ZIP files provided by Reolink contain multiple PAKs. This is why `get_info` always returns a list.

## Issues

The RLN36 PAKs have a very small difference in their structure that [pakler](https://github.com/vmallet/pakler) does not yet handle.
This means that without modifying the pakler source you won't be able to do anything with these files and you will get the `Could not guess section count` error.

An [issue](https://github.com/vmallet/pakler/issues/1) is open to fix this and you can check it out if you really need a (dirty) solution.
