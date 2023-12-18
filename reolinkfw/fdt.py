import re
from ctypes import BigEndianStructure, c_uint32

# Regex for a fdt_header with version=17 and last_comp_version=16.
RE_FDT_HEADER = re.compile(b"\xD0\x0D\xFE\xED.{16}\x00{3}\x11\x00{3}\x10.{12}", re.DOTALL)


class FDTHeader(BigEndianStructure):
    _fields_ = [
        ("magic", c_uint32),
        ("totalsize", c_uint32),
        ("off_dt_struct", c_uint32),
        ("off_dt_strings", c_uint32),
        ("off_mem_rsvmap", c_uint32),
        ("version", c_uint32),
        ("last_comp_version", c_uint32),
        ("boot_cpuid_phys", c_uint32),
        ("size_dt_strings", c_uint32),
        ("size_dt_struct", c_uint32),
    ]

    magic: int
    totalsize: int
    off_dt_struct: int
    off_dt_strings: int
    off_mem_rsvmap: int
    version: int
    last_comp_version: int
    boot_cpuid_phys: int
    size_dt_strings: int
    size_dt_struct: int
