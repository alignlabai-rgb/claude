"""Strict PCM WAV reader/writer: 48000 Hz, signed 16-bit little-endian, mono or stereo.

Accepted layout: `RIFF <size> WAVE`, then chunks in sequence. Exactly one `fmt ` chunk (16 bytes, or 18 with cbSize 0,
format tag 1 = PCM) before exactly one `data` chunk; other chunks (e.g. `LIST`) are skipped; odd-sized chunks carry a pad byte.

Rejected precisely (never "repaired" or guessed): wrong magic, other format tags (float, extensible, A-law...), other
rates/bit depths/channel counts, `block_align`/`byte_rate` that disagree with the format, chunks running past the end of the
file (truncated), a `data` size that is not a whole number of frames, a RIFF size that disagrees with the real file size
(lying or trailing bytes), missing/duplicate/misordered chunks, 0xFFFFFFFF streaming sizes, too many chunks.
Only chunk headers are read while parsing; PCM is read later in bounded slices.
"""

from __future__ import annotations

import struct
import sys
from array import array
from dataclasses import dataclass

from ..errors import Issue, ValidationFailed
from .limits import SAMPLE_RATE, AudioLimits

HEADER_BYTES = 44


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits: int
    data_offset: int
    data_size: int
    file_size: int

    @property
    def block_align(self) -> int:
        return self.channels * self.bits // 8

    @property
    def sample_count(self) -> int:  # frames per channel
        return self.data_size // self.block_align

    def to_dict(self) -> dict:
        return {"sample_rate": self.sample_rate, "channels": self.channels, "bits": self.bits, "data_offset": self.data_offset,
                "data_bytes": self.data_size, "sample_count": self.sample_count, "file_bytes": self.file_size}


def _reject(code: str, message: str, ptr: str = "wav"):
    raise ValidationFailed(message, [Issue(code, ptr, message)])


def parse_wav(fh, file_size: int, limits: AudioLimits = AudioLimits(), ptr: str = "wav") -> WavInfo:
    """Parse and validate the header of an open binary file of known size. Raises ValidationFailed."""
    def rej(code, msg):
        _reject(code, msg, ptr)

    fh.seek(0)
    head = fh.read(12)
    if len(head) < 12:
        rej("wav_truncated", "file is shorter than a RIFF header")
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        rej("wav_not_riff_wave", "not a RIFF/WAVE file")
    riff_size = struct.unpack("<I", head[4:8])[0]
    if riff_size == 0xFFFFFFFF:
        rej("wav_streaming_size", "streaming (0xFFFFFFFF) RIFF size is not supported")
    if file_size < riff_size + 8:
        rej("wav_truncated", f"RIFF size says {riff_size + 8} bytes but the file has only {file_size}")
    if file_size > riff_size + 8:
        rej("wav_riff_size_mismatch", f"RIFF size says {riff_size + 8} bytes but the file has {file_size} (lying header or trailing bytes)")
    pos, fmt, data, chunks, examined = 12, None, None, 0, 12
    while pos < file_size:
        chunks += 1
        examined += 8
        if chunks > limits.max_wav_chunks or examined > limits.max_wav_header_bytes:
            rej("wav_too_many_chunks", "too many chunks")
        if pos + 8 > file_size:
            rej("wav_truncated", "chunk header runs past the end of the file")
        fh.seek(pos)
        ch = fh.read(8)
        if len(ch) < 8:
            rej("wav_truncated", "chunk header runs past the end of the file")
        tag, size = ch[:4], struct.unpack("<I", ch[4:8])[0]
        body = pos + 8
        if size == 0xFFFFFFFF:
            rej("wav_streaming_size", "streaming (0xFFFFFFFF) chunk size is not supported")
        if body + size > file_size:
            rej("wav_truncated", f"chunk {tag!r} claims {size} bytes but only {file_size - body} remain")
        if tag == b"fmt ":
            if fmt is not None:
                rej("wav_duplicate_fmt", "more than one fmt chunk")
            if data is not None:
                rej("wav_chunk_order", "fmt chunk after data chunk")
            if size not in (16, 18):
                rej("wav_unsupported_format", f"fmt chunk of {size} bytes (only plain 16/18-byte PCM is supported; extensible is not)")
            fh.seek(body)
            raw = fh.read(size)
            tag_, channels, rate, byte_rate, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
            if size == 18 and struct.unpack("<H", raw[16:18])[0] != 0:
                rej("wav_unsupported_format", "fmt cbSize is not 0")
            if tag_ != 1:
                rej("wav_unsupported_format", f"format tag {tag_} is not integer PCM (1)")
            if bits != 16:
                rej("wav_unsupported_format", f"{bits}-bit samples are not supported (16 only)")
            if channels not in (1, 2):
                rej("wav_unsupported_format", f"{channels} channels are not supported (1 or 2)")
            if rate != SAMPLE_RATE:
                rej("wav_unsupported_format", f"sample rate {rate} Hz is not supported ({SAMPLE_RATE} only; no resampling)")
            if block_align != channels * 2:
                rej("wav_header_mismatch", f"block_align {block_align} disagrees with {channels} channel(s) x 16 bit")
            if byte_rate != rate * block_align:
                rej("wav_header_mismatch", f"byte_rate {byte_rate} disagrees with rate x block_align = {rate * block_align}")
            fmt = (rate, channels, bits)
        elif tag == b"data":
            if fmt is None:
                rej("wav_chunk_order", "data chunk before fmt chunk")
            if data is not None:
                rej("wav_duplicate_data", "more than one data chunk")
            if size % (fmt[1] * 2):
                rej("wav_data_not_whole_frames", f"data size {size} is not a multiple of the {fmt[1] * 2}-byte frame")
            data = (body, size)
        pos = body + size + (size & 1)  # chunks are word aligned: an odd size is followed by one pad byte
        if pos > file_size:
            rej("wav_truncated", f"chunk {tag!r} padding runs past the end of the file")
    if fmt is None:
        rej("wav_missing_fmt", "no fmt chunk")
    if data is None:
        rej("wav_missing_data", "no data chunk")
    return WavInfo(sample_rate=fmt[0], channels=fmt[1], bits=fmt[2], data_offset=data[0], data_size=data[1], file_size=file_size)


def header_bytes(channels: int, sample_count: int) -> bytes:
    """Canonical 44-byte header for `sample_count` frames of 48 kHz / 16-bit / `channels`."""
    block = channels * 2
    data = sample_count * block
    return b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, SAMPLE_RATE, SAMPLE_RATE * block, block, 16) + b"data" + struct.pack("<I", data)


def read_samples(fh, info: WavInfo, first_sample: int, count: int) -> array:
    """Read `count` frames (count*channels int16 values, interleaved) starting at frame `first_sample`."""
    if first_sample < 0 or count < 0 or first_sample + count > info.sample_count:
        raise ValueError("read outside the data chunk")
    fh.seek(info.data_offset + first_sample * info.block_align)
    raw = fh.read(count * info.block_align)
    if len(raw) != count * info.block_align:
        raise OSError("short read: the file shrank while reading")
    out = array("h")
    out.frombytes(raw)
    if sys.byteorder == "big":
        out.byteswap()
    return out


def pack_samples(values) -> bytes:
    arr = array("h", values)
    if sys.byteorder == "big":
        arr.byteswap()
    return arr.tobytes()
