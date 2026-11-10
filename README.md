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
data = aplib.decompress(packed, strict=False)      # keep the data decoded before the first stream error
```

Decompression errors raise `aplib.APLibError`, a subclass of `RuntimeError`.

## Untrusted input

The defaults are meant to be safe for untrusted input, such as data extracted from malware:

- `max_size` limits the unpacked data to `aplib.DEFAULT_MAX_SIZE` (64 MiB) by default. Exceeding it raises `APLibError`, even with `strict=False`. Unpacking up to 64 MiB in pure Python can still take several seconds of CPU time, so pass a smaller `max_size` when processing many untrusted inputs. `max_size=None` disables the limit; only use it for trusted input.
- If an AP32 header is parsed, the original size in the header limits the unpacked data too, even with `strict=False` (exceeding it raises `APLibError`). To salvage a stream whose header has a wrong original size, slice past the header and pass `headered=False, strict=False`.
- `strict=True` (the default) rejects malformed data, and checks the sizes and CRC32 checksums in the AP32 header. CRC32 detects accidental corruption, not deliberate tampering.
- `headered=None` (the default) treats input of at least 24 bytes starting with `AP32` as having an AP32 header; an invalid header size raises `APLibError`, even with `strict=False`. Pass `headered=True` to require a complete AP32 header (raising `APLibError` if it is missing or truncated), or `headered=False` to ignore any header.

## Changes in 0.8

- `decompress()` is strict by default (`strict=True`).
- `decompress()` and `APLib` limit the unpacked data to `DEFAULT_MAX_SIZE` by default, and exceeding `max_size` raises `APLibError` in non-strict mode too, instead of truncating the output.
- An AP32 header with an invalid header size (or a missing or truncated header when `headered=True`) raises `APLibError` in non-strict mode too, instead of being decompressed as data, and the original size in the header limits the unpacked data in both modes.
- New `headered` argument for `decompress()`.
- Input that isn't a bytes-like object, such as an integer, raises `TypeError`.
- `MemoryError` and `OverflowError` in the decompression loop raise `APLibError`, even in non-strict mode (converting the final output buffer to `bytes` can still raise `MemoryError`).
