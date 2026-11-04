import io
import struct
import unittest
import wave

from tests.audio_fixtures import RATE, interleave, list_chunk, tone, wav_bytes
from video_core.audiomix import wav
from video_core.audiomix.limits import AudioLimits
from video_core.errors import ValidationFailed


def parse(data: bytes, limits=AudioLimits()):
    return wav.parse_wav(io.BytesIO(data), len(data), limits)


def code(data: bytes) -> str:
    try:
        parse(data)
    except ValidationFailed as exc:
        return exc.issues[0].code
    return "ACCEPTED"


def chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def riff(*chunks, riff_delta=0) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) + riff_delta) + body


FMT = chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16))
DATA = chunk(b"data", b"\x01\x00\x02\x00")


class Accepts(unittest.TestCase):
    def test_mono_and_stereo(self):
        m = parse(wav_bytes(tone("square", 480, 100, 1000)))
        self.assertEqual((m.channels, m.sample_rate, m.bits, m.sample_count, m.data_offset, m.block_align), (1, RATE, 16, 100, 44, 2))
        s = parse(wav_bytes(interleave(tone("dc", 1000, 50, 5), tone("dc", 1000, 50, -5)), channels=2))
        self.assertEqual((s.channels, s.sample_count, s.block_align), (2, 50, 4))

    def test_extra_chunks_odd_padding_and_fmt18(self):
        pre, post = list_chunk(b"abc"), list_chunk(b"odd-length!")
        info = parse(wav_bytes([1, 2, 3, 4, 5], extra_before=pre, extra_after=post, fmt_size=18))
        self.assertEqual(info.sample_count, 5)
        self.assertEqual(info.data_offset % 2, 0)

    def test_header_bytes_roundtrips_through_the_stdlib_wave_module(self):
        for ch in (1, 2):
            n = 77
            raw = wav.header_bytes(ch, n) + wav.pack_samples(range(n * ch))
            with wave.open(io.BytesIO(raw)) as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()), (ch, RATE, 2, n))
            self.assertEqual(parse(raw).sample_count, n)
            self.assertEqual(raw[:44], wav_bytes(list(range(n * ch)), channels=ch)[:44], "engine header == independent fixture header")

    def test_read_samples_slices_and_bounds(self):
        data = wav_bytes(list(range(-5, 15)))
        info = parse(data)
        fh = io.BytesIO(data)
        self.assertEqual(list(wav.read_samples(fh, info, 3, 4)), [-2, -1, 0, 1])
        self.assertEqual(list(wav.read_samples(fh, info, 0, 0)), [])
        for first, count in ((-1, 1), (0, 21), (19, 2)):
            with self.assertRaises(ValueError):
                wav.read_samples(fh, info, first, count)

    def test_stereo_samples_are_interleaved(self):
        data = wav_bytes([1, -1, 2, -2, 3, -3], channels=2)
        self.assertEqual(list(wav.read_samples(io.BytesIO(data), parse(data), 1, 2)), [2, -2, 3, -3])


class Rejects(unittest.TestCase):
    def test_not_a_wav(self):
        self.assertEqual(code(b""), "wav_truncated")
        self.assertEqual(code(b"RIFF"), "wav_truncated")
        self.assertEqual(code(b"RIFX\x00\x00\x00\x00WAVE"), "wav_not_riff_wave")
        self.assertEqual(code(b"RIFF\x04\x00\x00\x00AVI "), "wav_not_riff_wave")
        self.assertEqual(code(b"ID3\x03" + b"\x00" * 60), "wav_not_riff_wave")

    def test_unsupported_formats_are_named_precisely(self):
        good = tone("square", 480, 20, 100)
        for kw in ({"tag": 3}, {"tag": 0xFFFE}, {"tag": 6}, {"bits": 8}, {"bits": 24}, {"bits": 32}, {"rate": 44100}, {"rate": 96000}, {"rate": 8000},
                   {"channels": 3}, {"channels": 0}, {"fmt_size": 18, "tag": 3}):
            self.assertEqual(code(wav_bytes(good, **kw)), "wav_unsupported_format", kw)
        extensible = riff(chunk(b"fmt ", struct.pack("<HHIIHH", 0xFFFE, 1, RATE, RATE * 2, 2, 16) + b"\x16\x00" + b"\x00" * 22), DATA)
        self.assertEqual(code(extensible), "wav_unsupported_format")
        self.assertEqual(code(riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16) + b"\x00\x00\x00\x00"), DATA)), "wav_unsupported_format")
        self.assertEqual(code(riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16) + b"\x01\x00"), DATA)), "wav_unsupported_format", "cbSize != 0")

    def test_lying_headers(self):
        good = tone("square", 480, 20, 100)
        self.assertEqual(code(wav_bytes(good, block_align=3)), "wav_header_mismatch")
        self.assertEqual(code(wav_bytes(good, byte_rate=1234)), "wav_header_mismatch")
        self.assertEqual(code(wav_bytes(good, channels=2, block_align=2)), "wav_header_mismatch")
        self.assertEqual(code(wav_bytes(good, riff_delta=-8)), "wav_riff_size_mismatch", "RIFF size smaller than the file")
        self.assertEqual(code(wav_bytes(good) + b"JUNK"), "wav_riff_size_mismatch", "trailing bytes")
        self.assertEqual(code(wav_bytes([1, 2, 3, 4, 5], channels=2)), "wav_data_not_whole_frames")

    def test_truncated(self):
        good = wav_bytes(tone("square", 480, 100, 100))
        self.assertEqual(code(good[:-1]), "wav_truncated")
        self.assertEqual(code(good[:-101]), "wav_truncated")
        self.assertEqual(code(good[:30]), "wav_truncated")
        self.assertEqual(code(wav_bytes(tone("square", 480, 100, 100), data_size_delta=4)), "wav_truncated", "data chunk claims more than remains")
        self.assertEqual(code(wav_bytes([1, 2, 3, 4], riff_delta=8)), "wav_truncated")
        self.assertEqual(code(wav_bytes([1, 2, 3, 4], extra_after=b"LIST")), "wav_truncated", "chunk header cut off")

    def test_structure(self):
        self.assertEqual(code(riff(DATA)), "wav_chunk_order")
        self.assertEqual(code(riff(DATA, FMT)), "wav_chunk_order")
        self.assertEqual(code(riff(FMT)), "wav_missing_data")
        self.assertEqual(code(riff(list_chunk(b"x"))), "wav_missing_fmt")
        self.assertEqual(code(riff(FMT, FMT, DATA)), "wav_duplicate_fmt")
        self.assertEqual(code(riff(FMT, DATA, DATA)), "wav_duplicate_data")
        self.assertEqual(code(riff(FMT, DATA) + b""), "ACCEPTED")
        stream = b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + FMT + DATA
        self.assertEqual(code(stream), "wav_streaming_size")
        self.assertEqual(code(riff(FMT, b"data" + b"\xff\xff\xff\xff")), "wav_streaming_size")

    def test_chunk_walk_is_bounded(self):
        many = [list_chunk(b"")] * 70
        self.assertEqual(code(riff(FMT, *many, DATA)), "wav_too_many_chunks")
        self.assertEqual(code(riff(FMT, *many[:20], DATA)), "ACCEPTED")
        with self.assertRaises(ValidationFailed):
            parse(riff(FMT, *many[:20], DATA), AudioLimits(max_wav_chunks=5))

    def test_zero_length_data_parses_but_has_no_samples(self):
        info = parse(riff(FMT, chunk(b"data", b"")))
        self.assertEqual(info.sample_count, 0)


if __name__ == "__main__":
    unittest.main()
