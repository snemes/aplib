#!/usr/bin/env python3
"""A pure Python module for decompressing aPLib compressed data

Adapted from the original C source code from http://ibsensoftware.com/files/aPLib-1.1.1.zip
Approximately 20 times faster than other Python implementations.
Compatible with both Python 2 and 3.
"""
import struct
from binascii import crc32
from io import BytesIO

__all__ = ['APLib', 'APLibError', 'DEFAULT_MAX_SIZE', 'decompress']
__version__ = '0.8'
__author__ = 'Sandor Nemes'

# default limit for the size of the unpacked data (64 MiB)
DEFAULT_MAX_SIZE = 64 * 1024 * 1024

_MAX_GAMMA = 0xFFFFFFFF

try:
    _range = xrange
except NameError:
    _range = range


class APLibError(RuntimeError):
    """Exception raised for aPLib decompression errors."""


class _MaxSizeError(APLibError):
    """Internal exception raised when unpacked data exceeds max_size.

    Unlike errors caused by malformed input, it is raised in non-strict mode too.
    """


def _ensure_bytes(data):
    if isinstance(data, bytes):
        return data
    # accept any bytes-like object, but reject e.g. integers,
    # for which bytes(n) would allocate n zero bytes
    return memoryview(data).tobytes()


def _raise_error(message):
    err = APLibError(message)
    err.__cause__ = None
    raise err


class APLib(object):

    __slots__ = 'source', 'destination', 'tag', 'bitcount', 'strict', 'max_size'

    def __init__(self, source, strict=True, max_size=DEFAULT_MAX_SIZE):
        self.source = BytesIO(_ensure_bytes(source))
        self.destination = bytearray()
        self.tag = 0
        self.bitcount = 0
        self.strict = bool(strict)
        self.max_size = max_size

    def getbit(self):
        # check if tag is empty
        self.bitcount -= 1
        if self.bitcount < 0:
            # load next tag
            self.tag = ord(self.source.read(1))
            self.bitcount = 7

        # shift bit out of tag
        bit = self.tag >> 7 & 1
        self.tag <<= 1

        return bit

    def getgamma(self):
        result = 1

        # input gamma2-encoded bits
        while True:
            result = (result << 1) + self.getbit()
            if result > _MAX_GAMMA:
                raise IndexError('Gamma value overflow')
            if not self.getbit():
                break

        return result

    def _append_byte(self, value):
        if self.max_size is not None and len(self.destination) >= self.max_size:
            raise _MaxSizeError('Unpacked data exceeds max_size')
        self.destination.append(value)

    def _copy_match(self, offs, length):
        destination = self.destination
        if offs <= 0 or offs > len(destination):
            raise IndexError('Invalid match offset')
        if self.max_size is not None and len(destination) + length > self.max_size:
            raise _MaxSizeError('Unpacked data exceeds max_size')
        for _ in _range(length):
            destination.append(destination[-offs])

    def depack(self):
        r0 = -1
        lwm = 0
        done = False

        try:

            # first byte verbatim
            self._append_byte(ord(self.source.read(1)))

            # main decompression loop
            while not done:
                if self.getbit():
                    if self.getbit():
                        if self.getbit():
                            offs = 0
                            for _ in _range(4):
                                offs = (offs << 1) + self.getbit()

                            if offs:
                                self._copy_match(offs, 1)
                            else:
                                self._append_byte(0)

                            lwm = 0
                        else:
                            offs = ord(self.source.read(1))
                            length = 2 + (offs & 1)
                            offs >>= 1

                            if offs:
                                self._copy_match(offs, length)
                            else:
                                done = True

                            r0 = offs
                            lwm = 1
                    else:
                        offs = self.getgamma()

                        if lwm == 0 and offs == 2:
                            offs = r0
                            length = self.getgamma()
                            self._copy_match(offs, length)
                        else:
                            if lwm == 0:
                                offs -= 3
                            else:
                                offs -= 2

                            offs <<= 8
                            offs += ord(self.source.read(1))
                            length = self.getgamma()

                            if offs >= 32000:
                                length += 1
                            if offs >= 1280:
                                length += 1
                            if offs < 128:
                                length += 2

                            self._copy_match(offs, length)
                            r0 = offs

                        lwm = 1
                else:
                    self._append_byte(ord(self.source.read(1)))
                    lwm = 0

        except (TypeError, IndexError):
            # malformed or truncated input
            if self.strict:
                _raise_error('aPLib decompression error')
        except (MemoryError, OverflowError):
            # running out of resources is an error even in non-strict mode
            _raise_error('aPLib decompression error: unpacked data is too large')

        return bytes(self.destination)

    def pack(self):
        raise NotImplementedError


def decompress(data, strict=True, max_size=DEFAULT_MAX_SIZE, headered=None):
    """Decompress aPLib compressed data, and return the unpacked data as bytes.

    data -- bytes-like object containing a raw aPLib stream or AP32 headered data
    strict -- if true (the default), raise APLibError for any malformed input, and
        verify the sizes and checksums in the AP32 header; if false, return the
        data decoded before the first error in the compressed stream
    max_size -- maximum size of the unpacked data, DEFAULT_MAX_SIZE by default;
        exceeding it raises APLibError even if strict is false; the original size
        in an AP32 header limits the unpacked data too; None disables the limit,
        which should only be used for trusted input
    headered -- True if data starts with an AP32 header, False if it is a raw aPLib
        stream, or None (the default) to detect the header by its b'AP32' signature;
        an invalid header raises APLibError even if strict is false
    """
    packed_size = None
    packed_crc = None
    orig_size = None
    orig_crc = None

    data = _ensure_bytes(data)

    if headered is None:
        # a valid raw aPLib stream cannot start with b'AP32': it would decode to 'A' and '3',
        # followed by a match with offset 50, which is out of bounds
        headered = data.startswith(b'AP32') and len(data) >= 24

    if headered:
        if not data.startswith(b'AP32') or len(data) < 24:
            _raise_error('AP32 header is missing or truncated')
        header_size, packed_size, packed_crc, orig_size, orig_crc = struct.unpack_from('<IIIII', data, 4)
        if header_size < 24 or header_size > len(data):
            _raise_error('Header size is incorrect')
        data = data[header_size : header_size + packed_size]

    if strict and packed_size is not None:
        if packed_size != len(data):
            _raise_error('Packed data size is incorrect')
        if packed_crc != (crc32(data) & 0xFFFFFFFF):
            _raise_error('Packed data checksum is incorrect')

    # never unpack more data than the header says, not even in non-strict mode
    limit = max_size
    capped_by_orig_size = False
    if orig_size is not None and (limit is None or orig_size <= limit):
        limit = orig_size
        capped_by_orig_size = True

    try:
        result = APLib(data, strict=strict, max_size=limit).depack()
    except _MaxSizeError:
        if not capped_by_orig_size:
            raise
        _raise_error('Unpacked data size is incorrect')

    if strict and orig_size is not None:
        if orig_size != len(result):
            _raise_error('Unpacked data size is incorrect')
        if orig_crc != (crc32(result) & 0xFFFFFFFF):
            _raise_error('Unpacked data checksum is incorrect')

    return result


def main():
    # self-test
    data = b'T\x00he quick\xecb\x0erown\xcef\xaex\x80jumps\xed\xe4veur`t?lazy\xead\xfeg\xc0\x00'
    assert decompress(data, strict=True) == b'The quick brown fox jumps over the lazy dog'


if __name__ == '__main__':
    main()


