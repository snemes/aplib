import ast
import os
import struct
import unittest
from binascii import crc32

import aplib
from aplib import APLib, APLibError, decompress

if not hasattr(unittest.TestCase, 'assertRaisesRegex'):
    unittest.TestCase.assertRaisesRegex = unittest.TestCase.assertRaisesRegexp


class _APLibStreamBuilder(object):
    """Helper for assembling deterministic aPLib compressed bitstreams in tests."""

    def __init__(self, first_byte):
        self.output = bytearray([first_byte])
        self.tag_pos = None
        self.tag_value = 0
        self.bits_used = 8

    def put_bit(self, bit):
        if self.bits_used == 8:
            self.tag_pos = len(self.output)
            self.output.append(0)
            self.tag_value = 0
            self.bits_used = 0
        self.tag_value = (self.tag_value << 1) | (bit & 1)
        self.bits_used += 1
        self.output[self.tag_pos] = (self.tag_value << (8 - self.bits_used)) & 0xFF

    def put_bits(self, value, count):
        for shift in range(count - 1, -1, -1):
            self.put_bit((value >> shift) & 1)

    def put_byte(self, byte):
        self.output.append(byte & 0xFF)

    def put_gamma(self, value):
        assert value >= 2
        bits = []
        while value > 1:
            bits.append(value & 1)
            value >>= 1
        bits.reverse()
        for i, bit in enumerate(bits):
            self.put_bit(bit)
            self.put_bit(1 if i < len(bits) - 1 else 0)

    def add_literal(self, byte):
        self.put_bit(0)
        self.put_byte(byte)

    def add_short_4bit_match(self, offs):
        assert 0 <= offs <= 15
        self.put_bits(0b111, 3)
        self.put_bits(offs, 4)

    def add_short_byte_match(self, offs, length=2):
        assert 0 <= offs <= 127
        assert length in (2, 3)
        self.put_bits(0b110, 3)
        self.put_byte((offs << 1) | (length - 2))

    def add_end_marker(self):
        self.put_bits(0b110, 3)
        self.put_byte(0)

    def add_gamma_match_r0(self, length):
        self.put_bits(0b10, 2)
        self.put_gamma(2)
        self.put_gamma(length)

    def add_gamma_match(self, offs, raw_gamma_length, lwm):
        self.put_bits(0b10, 2)
        high = (offs >> 8) + (3 if lwm == 0 else 2)
        self.put_gamma(high)
        self.put_byte(offs & 0xFF)
        self.put_gamma(raw_gamma_length)

    def build(self):
        return bytes(self.output)


def _make_ap32_header(packed_data, unpacked_data, header_size=24,
                      packed_size=None, packed_crc=None,
                      orig_size=None, orig_crc=None):
    if packed_size is None:
        packed_size = len(packed_data)
    if packed_crc is None:
        packed_crc = crc32(packed_data) & 0xFFFFFFFF
    if orig_size is None:
        orig_size = len(unpacked_data)
    if orig_crc is None:
        orig_crc = crc32(unpacked_data) & 0xFFFFFFFF

    header = b'AP32' + struct.pack(
        '<IIIII', header_size, packed_size, packed_crc, orig_size, orig_crc
    )
    if header_size > 24:
        header += b'\x00' * (header_size - 24)
    return header + packed_data


class TestAPLibDecompression(unittest.TestCase):

    SAMPLE_PACKED = (
        b'T\x00he quick\xecb\x0erown\xcef\xaex\x80jumps\xed\xe4veur`t?lazy\xead\xfeg\xc0\x00'
    )
    SAMPLE_UNPACKED = b'The quick brown fox jumps over the lazy dog'

    def test_sample_vector(self):
        self.assertEqual(decompress(self.SAMPLE_PACKED), self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(self.SAMPLE_PACKED, strict=True), self.SAMPLE_UNPACKED)
        aplib.main()

    def test_input_buffer_types(self):
        self.assertEqual(decompress(bytearray(self.SAMPLE_PACKED), strict=True), self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(memoryview(self.SAMPLE_PACKED), strict=True), self.SAMPLE_UNPACKED)

        wrapped = _make_ap32_header(self.SAMPLE_PACKED, self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(bytearray(wrapped), strict=True), self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(memoryview(wrapped), strict=True), self.SAMPLE_UNPACKED)

    def test_literals_and_4bit_matches(self):
        builder = _APLibStreamBuilder(ord('A'))
        builder.add_literal(ord('B'))
        builder.add_short_4bit_match(0)   # appends 0x00
        builder.add_short_4bit_match(3)   # copies 'A' (offset 3)
        builder.add_short_4bit_match(2)   # copies 0x00 (offset 2)
        builder.add_end_marker()

        stream = builder.build()
        self.assertEqual(decompress(stream, strict=True), b'AB\x00A\x00')

    def test_short_byte_matches_and_trailing_bytes(self):
        builder = _APLibStreamBuilder(ord('X'))
        builder.add_literal(ord('Y'))
        # offs=2, length=2 -> copies 'XY'
        builder.add_short_byte_match(offs=2, length=2)
        # offs=1, length=3 -> overlapping RLE copy of 'Y' 3 times
        builder.add_short_byte_match(offs=1, length=3)
        builder.add_end_marker()

        stream = builder.build() + b'TRAILING_BYTES_IGNORED'
        self.assertEqual(decompress(stream, strict=True), b'XYXYYYY')

    def test_gamma_match_r0_reuse_and_lwm_behavior(self):
        builder = _APLibStreamBuilder(ord('A'))
        builder.add_literal(ord('B'))
        builder.add_literal(ord('C'))
        # 110 match sets r0=3, lwm=1 -> copies 'AB' (output: 'ABCAB')
        builder.add_short_byte_match(offs=3, length=2)
        # While lwm==1, gamma offs=2 does NOT reuse r0; it encodes high=0 + low_byte=1 -> offs=1
        # With offs=1 (<128), length = gamma(2) + 2 = 4 -> copies 'B' 4 times (output: 'ABCABBBBB')
        builder.add_gamma_match(offs=1, raw_gamma_length=2, lwm=1)
        # Literal resets lwm=0 while r0=1 (output: 'ABCABBBBBZ')
        builder.add_literal(ord('Z'))
        # Now lwm==0 and gamma offs=2 reuses r0=1, length=3 -> copies 'Z' 3 times
        builder.add_gamma_match_r0(length=3)
        builder.add_end_marker()

        stream = builder.build()
        self.assertEqual(decompress(stream, strict=True), b'ABCABBBBBZZZZ')

    def test_gamma_offset_thresholds(self):
        # Test all offset thresholds in the 10 branch:
        # offs < 128 (+2), 128 <= offs < 1280 (+0), 1280 <= offs < 32000 (+1), offs >= 32000 (+2)
        builder = _APLibStreamBuilder(ord('M'))
        # Expand to 32000 'M's using overlapping match of offs=1 (<128 -> length = gamma + 2)
        # Need 31999 more bytes -> raw_gamma_length = 31997
        builder.add_gamma_match(offs=1, raw_gamma_length=31997, lwm=0)
        builder.add_literal(ord('N'))  # total length is now 32001, lwm=0

        # 1. offs = 32001 (>= 32000): length = raw_gamma(2) + 1 (>=32000) + 1 (>=1280) = 4 bytes of 'M'
        builder.add_gamma_match(offs=32001, raw_gamma_length=2, lwm=0)
        # 2. offs = 1280 (>= 1280, < 32000): length = raw_gamma(2) + 1 = 3 bytes
        builder.add_gamma_match(offs=1280, raw_gamma_length=2, lwm=1)
        # 3. offs = 128 (>= 128, < 1280): length = raw_gamma(2) + 0 = 2 bytes
        builder.add_gamma_match(offs=128, raw_gamma_length=2, lwm=1)
        builder.add_end_marker()

        result = decompress(builder.build(), strict=True)
        self.assertEqual(len(result), 32000 + 1 + 4 + 3 + 2)
        self.assertEqual(result[:32000], b'M' * 32000)
        self.assertEqual(result[32000:32001], b'N')
        self.assertEqual(result[32001:32005], b'MMMM')


class TestAP32Header(unittest.TestCase):

    SAMPLE_PACKED = TestAPLibDecompression.SAMPLE_PACKED
    SAMPLE_UNPACKED = TestAPLibDecompression.SAMPLE_UNPACKED

    def test_valid_header_and_extended_header(self):
        wrapped = _make_ap32_header(self.SAMPLE_PACKED, self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(wrapped, strict=False), self.SAMPLE_UNPACKED)
        self.assertEqual(decompress(wrapped, strict=True), self.SAMPLE_UNPACKED)

        # Extended header (header_size = 36) + trailing bytes after packed_size
        extended = _make_ap32_header(self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, header_size=36) + b'EXTRA'
        self.assertEqual(decompress(extended, strict=True), self.SAMPLE_UNPACKED)

    def test_invalid_header_size(self):
        # header_size < 24
        bad_small = _make_ap32_header(self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, header_size=12)
        with self.assertRaisesRegex(APLibError, 'Header size is incorrect'):
            decompress(bad_small, strict=True)

        # header_size > len(data) with packed_size=0
        bad_large = b'AP32' + struct.pack('<IIIII', 64, 0, 0, 0, 0)
        with self.assertRaisesRegex(APLibError, 'Header size is incorrect'):
            decompress(bad_large, strict=True)

    def test_header_size_and_crc_mismatches(self):
        # Packed size mismatch (packed_size larger than available data)
        bad_packed_size = _make_ap32_header(
            self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, packed_size=len(self.SAMPLE_PACKED) + 10
        )
        with self.assertRaisesRegex(APLibError, 'Packed data size is incorrect'):
            decompress(bad_packed_size, strict=True)

        # Packed CRC mismatch
        bad_packed_crc = _make_ap32_header(
            self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, packed_crc=0x12345678
        )
        with self.assertRaisesRegex(APLibError, 'Packed data checksum is incorrect'):
            decompress(bad_packed_crc, strict=True)

        # Unpacked size mismatch (orig_size larger than actual unpacked data)
        bad_orig_size_large = _make_ap32_header(
            self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, orig_size=len(self.SAMPLE_UNPACKED) + 5
        )
        with self.assertRaisesRegex(APLibError, 'Unpacked data size is incorrect'):
            decompress(bad_orig_size_large, strict=True)

        # Unpacked size mismatch (orig_size smaller than actual unpacked data)
        bad_orig_size_small = _make_ap32_header(
            self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, orig_size=10
        )
        with self.assertRaisesRegex(APLibError, 'Unpacked data size is incorrect'):
            decompress(bad_orig_size_small, strict=True)

        # Unpacked CRC mismatch
        bad_orig_crc = _make_ap32_header(
            self.SAMPLE_PACKED, self.SAMPLE_UNPACKED, orig_crc=0xDEADBEEF
        )
        with self.assertRaisesRegex(APLibError, 'Unpacked data checksum is incorrect'):
            decompress(bad_orig_crc, strict=True)


class TestEdgeCasesAndRegressions(unittest.TestCase):

    def test_aplib_error_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(APLibError, RuntimeError))

    def test_pack_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            APLib(b'A').pack()

    def test_uninitialized_r0_rejected(self):
        # Craft stream where lwm == 0 and offs == 2 before r0 has been initialized (r0 == -1)
        builder = _APLibStreamBuilder(ord('A'))
        builder.add_literal(ord('B'))
        builder.add_gamma_match_r0(length=2)
        builder.add_end_marker()
        payload = builder.build()

        with self.assertRaises(APLibError):
            decompress(payload, strict=True)

        # In non-strict mode, must stop at the invalid r0 match and return only b'AB'
        self.assertEqual(decompress(payload, strict=False), b'AB')

    def test_zero_offset_in_gamma_match_rejected(self):
        # Craft stream with offs == 0 in 10 branch (lwm == 0, gamma == 3 -> 3 - 3 = 0, low_byte = 0)
        builder = _APLibStreamBuilder(ord('X'))
        builder.add_gamma_match(offs=0, raw_gamma_length=2, lwm=0)
        builder.add_end_marker()
        payload = builder.build()

        with self.assertRaises(APLibError):
            decompress(payload, strict=True)

        # In non-strict mode, must not copy destination[-0] == destination[0]
        self.assertEqual(decompress(payload, strict=False), b'X')

    def test_truncated_literal_at_eof_stops_immediately(self):
        # Empty input
        self.assertEqual(decompress(b'', strict=False), b'')
        with self.assertRaises(APLibError):
            decompress(b'', strict=True)

        # 1-byte input without end marker
        self.assertEqual(decompress(b'A', strict=False), b'A')
        with self.assertRaises(APLibError):
            decompress(b'A', strict=True)

        # Tag byte 0x70 = 0b0_111_0000:
        # Bit 7 is 0 (literal), but stream is at EOF.
        # Must stop at EOF on the missing literal rather than executing bits 6..0 (111_0000 -> append 0x00).
        self.assertEqual(decompress(b'A\x70', strict=False), b'A')
        with self.assertRaises(APLibError):
            decompress(b'A\x70', strict=True)

    def test_out_of_bounds_offsets_rejected(self):
        # 4-bit match with offs=5 when only 1 byte is in destination
        b1 = _APLibStreamBuilder(ord('A'))
        b1.add_short_4bit_match(5)
        b1.add_end_marker()
        with self.assertRaises(APLibError):
            decompress(b1.build(), strict=True)
        self.assertEqual(decompress(b1.build(), strict=False), b'A')

        # 110 match with offs=10 when only 1 byte is in destination
        b2 = _APLibStreamBuilder(ord('A'))
        b2.add_short_byte_match(offs=10, length=2)
        b2.add_end_marker()
        with self.assertRaises(APLibError):
            decompress(b2.build(), strict=True)
        self.assertEqual(decompress(b2.build(), strict=False), b'A')

    def test_gamma_overflow_protection(self):
        # Stream starting with 'A', tag bits '10' (gamma match), followed by all 1s (infinite gamma continuation)
        payload = b'A\xbf' + (b'\xff' * 16)
        with self.assertRaises(APLibError):
            decompress(payload, strict=True)
        self.assertEqual(decompress(payload, strict=False), b'A')

    def test_max_size_limit(self):
        sample = TestAPLibDecompression.SAMPLE_PACKED
        unpacked = TestAPLibDecompression.SAMPLE_UNPACKED

        # Exact max_size succeeds
        self.assertEqual(
            decompress(sample, strict=True, max_size=len(unpacked)),
            unpacked,
        )

        # Smaller max_size raises in strict=True and truncates in strict=False
        with self.assertRaises(APLibError):
            decompress(sample, strict=True, max_size=10)
        self.assertEqual(decompress(sample, strict=False, max_size=10), unpacked[:10])

        # Also works directly on APLib class
        self.assertEqual(APLib(sample, strict=False, max_size=5).depack(), unpacked[:5])

    def test_python2_signed_crc32_and_ast_compatibility(self):
        # Simulate Python 2's signed 32-bit crc32() return value when MSB is 1
        orig_crc32 = aplib.crc32

        def signed_py2_crc32(data):
            val = orig_crc32(data) & 0xFFFFFFFF
            return val - 0x100000000 if (val & 0x80000000) else val

        try:
            aplib.crc32 = signed_py2_crc32
            wrapped = _make_ap32_header(
                TestAPLibDecompression.SAMPLE_PACKED,
                TestAPLibDecompression.SAMPLE_UNPACKED,
            )
            self.assertEqual(
                decompress(wrapped, strict=True),
                TestAPLibDecompression.SAMPLE_UNPACKED,
            )
        finally:
            aplib.crc32 = orig_crc32

        # Verify no Python 3-only syntax constructs are used in src/aplib.py or tests/test_aplib.py
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel_path in ('src/aplib.py', 'tests/test_aplib.py'):
            full_path = os.path.join(repo_root, rel_path)
            with open(full_path, 'rb') as f:
                tree = ast.parse(f.read(), filename=rel_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Raise):
                    self.assertIsNone(
                        getattr(node, 'cause', None),
                        'Python 3-only "raise ... from ..." found in ' + rel_path,
                    )
                if isinstance(node, ast.ClassDef):
                    self.assertTrue(
                        len(node.bases) > 0,
                        'Old-style class without explicit base found in ' + rel_path + ': ' + node.name,
                    )


if __name__ == '__main__':
    unittest.main()
