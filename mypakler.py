import io
import itertools
from typing import Optional

import pakler


def read_header(pakbytes, section_count, mtd_part_count=None):
    """Read and parse the header of a PAK firmware file.

    :param pakbytes: PAK firmware file as bytes
    :param section_count: number of sections present in the header
    :param mtd_part_count: optional number of mtd_parts, defaults to section_count
    :return: the parsed Header object
    """
    if not mtd_part_count:
        mtd_part_count = section_count

    header_size = pakler.calc_header_size(section_count, mtd_part_count)

    buf = pakbytes[:header_size]
    if len(buf) != header_size:
        raise Exception("Header size error, expected: {}, got: {}".format(header_size, len(buf)))

    return pakler.Header(buf, section_count, mtd_part_count)


def extract_section(pakbytes, section):
    res = b''
    length = section.len
    chunk_size = pakler.CHUNK_SIZE
    with io.BytesIO(pakbytes) as f:
        f.seek(section.start)
        while length > 0:
            if length < chunk_size:
                chunk_size = length
            chunk = f.read(chunk_size)
            if not chunk:
                raise Exception("Read error with chunk_size={} length={}".format(chunk_size, length))
            res += chunk
            length -= chunk_size
    return res


def guess_section_count(pakbytes) -> Optional[int]:
    """
    Attempt to guess the number of sections for the given PAK firmware file.

    :return: Guessed number of sections, or None if it couldn't be guessed
    """
    # Attempt all counts between 1 and 30 starting with the most probable first
    for i in itertools.chain(range(8, 14), range(1, 8), range(14, 30)):
        try:
            read_header(pakbytes, i)
            return i
        except Exception:  # Broad clause: the goal is to blindly try to parse the header, ignoring ALL errors
            pass

    return None