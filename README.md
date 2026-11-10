# aplib
A pure Python module for decompressing aPLib compressed data

Adapted from the original C source code from http://ibsensoftware.com/files/aPLib-1.1.1.zip  
Approximately 20 times faster than other Python implementations.  
Compatible with both Python 2 and 3.  

## Usage

```python
import aplib

data = aplib.decompress(packed)                    # raw aPLib stream or AP32 headered data
data = aplib.decompress(packed, max_size=1 << 20)  # lower the limit for the unpacked size
data = aplib.decompress(packed, headered=False)    # never parse an AP32 header
data = aplib.decompress(packed, strict=False)      # keep the data decoded before the first error
```

Errors raise `aplib.APLibError`, a subclass of `RuntimeError`.

## Untrusted input

The defaults are meant to be safe for untrusted input, such as data extracted from malware:

- `max_size` limits the unpacked data to `aplib.DEFAULT_MAX_SIZE` (64 MiB) by default. Exceeding it raises `APLibError`, even with `strict=False`. `max_size=None` disables the limit; only use it for trusted input.
- If the data has an AP32 header, the original size in the header limits the unpacked data too.
- `strict=True` (the default) rejects malformed data, and checks the sizes and CRC32 checksums in the AP32 header. CRC32 detects accidental corruption, not deliberate tampering.
- `headered=None` (the default) detects an AP32 header by its `AP32` signature; an invalid header raises `APLibError`. Pass `headered=True` or `headered=False` to require or to ignore the header.

## Changes in 0.8

- `decompress()` is strict by default (`strict=True`).
- `decompress()` and `APLib` limit the unpacked data to `DEFAULT_MAX_SIZE` by default, and exceeding `max_size` raises `APLibError` in non-strict mode too, instead of truncating the output.
- An invalid AP32 header raises `APLibError` in non-strict mode too, instead of being decompressed as data, and the original size in the header limits the unpacked data in both modes.
- New `headered` argument for `decompress()`.
- Input that isn't a bytes-like object, such as an integer, raises `TypeError`.
- `MemoryError` and `OverflowError` during decompression raise `APLibError`, even in non-strict mode.
