#!/usr/bin/env python2
"""
A pure Python module for decompressing aPLib compressed data.
Adapted from the original C source code from http://ibsensoftware.com/files/aPLib-1.1.1.zip
Approximately 20 times faster than the other Python implementations.
"""
import struct
from binascii import crc32
from io import BytesIO

__all__ = ['APLib', 'decompress']
__version__ = '0.5'
__author__ = 'Sandor Nemes'


class APLib(object):
    """internal data structure"""

    def __init__(self, source, strict=True):
        self.source = BytesIO(source)
        self.destination = BytesIO()
        self.tag = 0
        self.bitcount = 0
        self.strict = bool(strict)

    def read(self):
        return ord(self.source.read(1))

    def write(self, value):
        self.destination.write(chr(value))

    def copy(self, length=1):
        for _ in xrange(length):
            self.write(self.read())

    def dstcopy(self, offset, length=1):
        for _ in xrange(length):
            self.write(ord(self.destination.getvalue()[-offset]))

    def getbit(self):
        # check if tag is empty
        self.bitcount -= 1
        if self.bitcount < 0:
            # load next tag
            self.tag = self.read()
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
            if not self.getbit():
                break

        return result

    def depack(self):
        r0 = -1
        lwm = 0
        done = False

        try:

            # first byte verbatim
            self.copy()

            # main decompression loop
            while not done:
                if self.getbit():
                    if self.getbit():
                        if self.getbit():
                            offs = 0
                            for _ in xrange(4):
                                offs = (offs << 1) + self.getbit()

                            if offs:
                                self.dstcopy(offs)
                            else:
                                self.write(0)

                            lwm = 0
                        else:
                            offs = self.read()
                            length = 2 + (offs & 1)
                            offs >>= 1

                            if offs:
                                self.dstcopy(offs, length)
                            else:
                                done = True

                            r0 = offs
                            lwm = 1
                    else:
                        offs = self.getgamma()

                        if lwm == 0 and offs == 2:
                            offs = r0
                            length = self.getgamma()
                            self.dstcopy(offs, length)
                        else:
                            if lwm == 0:
                                offs -= 3
                            else:
                                offs -= 2

                            offs <<= 8
                            offs += self.read()
                            length = self.getgamma()

                            if offs >= 32000:
                                length += 1
                            if offs >= 1280:
                                length += 1
                            if offs < 128:
                                length += 2

                            self.dstcopy(offs, length)
                            r0 = offs

                        lwm = 1
                else:
                    self.copy()
                    lwm = 0

        except (TypeError, IndexError):
            if self.strict:
                raise RuntimeError('aPLib decompression error')

        return self.destination.getvalue()

    def pack(self):
        raise NotImplementedError


def decompress(data, strict=False):
    packed_size = None
    packed_crc = None
    orig_size = None
    orig_crc = None

    if data.startswith('AP32') and len(data) >= 24:
        # data has an aPLib header
        header_size, packed_size, packed_crc, orig_size, orig_crc = struct.unpack_from('=IIIII', data, 4)
        data = data[header_size : header_size + packed_size]

    if strict:
        if packed_size is not None and packed_size != len(data):
            raise RuntimeError('Packed data size is incorrect')
        if packed_crc is not None and packed_crc != crc32(data):
            raise RuntimeError('Packed data checksum is incorrect')

    result = APLib(data, strict=strict).depack()

    if strict:
        if orig_size is not None and orig_size != len(result):
            raise RuntimeError('Unpacked data size is incorrect')
        if orig_crc is not None and orig_crc != crc32(result):
            raise RuntimeError('Unpacked data checksum is incorrect')

    return result


def main():
    # self-test
    data = 'T\x00he quick\xecb\x0erown\xcef\xaex\x80jumps\xed\xe4veur`t?lazy\xead\xfeg\xc0\x00'
    assert decompress(data) == 'The quick brown fox jumps over the lazy dog'


if __name__ == '__main__':
    main()
