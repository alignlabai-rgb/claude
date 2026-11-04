"""Pure parsers for `ffmpeg -filters / -encoders / -demuxers` listings.

No process is launched here. Each parser accepts the raw text and returns a
`Listing`: the set of recognised names plus how many other lines were ignored.

Layouts handled (every entry line must match *all* columns, so legend/header lines,
separators and noise are ignored rather than mistaken for entries):

  filters   `<flags> <name> <io> [description]`
              flags : 2 or 3 characters from T S C .   (FFmpeg 6.1.1: 3, e.g. `..C scale`, `T.C drawtext`;
                      FFmpeg 8.1.2 as reported by the requester: 2, e.g. `.. color`, `T. drawtext`)
              io    : e.g. `V->V`, `|->V`, `N->N`, `VV->V`, `V->|`
  encoders  `<flags> <name> [description]`
              flags : exactly 6 characters, first V/A/S then F S X B D or `.`  (e.g. `V....D libx264`)
              (the 6-character layout is the only one observed so far, in FFmpeg 6.1.1 and, via the
               requester's report that libx264 was found, FFmpeg 8.1.2; any other width is rejected)
  demuxers  `<flags> <name[,alias...]> [description]`
              flags : `D` optionally followed by `E` or a space (e.g. ` D  image2`, ` DE mov,mp4,...`)

Names are matched exactly: `scale` is not satisfied by `scale2ref`, `libx264` not by `libx264rgb`.
Line endings may be LF or CRLF. Anything unrecognised is counted, never guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IO = r"[AVN|]+->[AVN|]+"
_FILTER = re.compile(rf"^\s*[TSC.]{{2,3}}\s+(?P<name>[A-Za-z0-9_]+)\s+{_IO}(?:\s.*)?$")
_ENCODER = re.compile(r"^\s*[VAS][FSXBD.]{5}\s+(?P<name>[A-Za-z0-9_.+\-]+)(?:\s.*)?$")
_DEMUXER = re.compile(r"^\s*D[E ]?\s+(?P<name>[A-Za-z0-9_.+\-]+(?:,[A-Za-z0-9_.+\-]+)*)(?:\s.*)?$")


# `-muxers`: `<flags> <name> [description]`, flags ` E` (FFmpeg 6.1.1 observed: two leading spaces then E; a muxer that can also demux shows `DE`)
_MUXER = re.compile(r"^\s*[D ]?E\s+(?P<name>[A-Za-z0-9_.+\-]+(?:,[A-Za-z0-9_.+\-]+)*)(?:\s.*)?$")


@dataclass
class Listing:
    names: set = field(default_factory=set)
    recognised: int = 0
    ignored: int = 0
    sample_ignored: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"recognised": self.recognised, "ignored_lines": self.ignored, "sample_ignored": list(self.sample_ignored)}


def _parse(text: str, pattern: re.Pattern, split_commas: bool) -> Listing:
    out = Listing()
    for line in text.splitlines():  # handles LF and CRLF
        if not line.strip():
            continue
        m = pattern.match(line)
        if m is None:
            out.ignored += 1
            if len(out.sample_ignored) < 5:
                out.sample_ignored.append(line.strip()[:80])
            continue
        out.recognised += 1
        name = m.group("name")
        out.names.update(name.split(",") if split_commas else [name])
    return out


def parse_filters(text: str) -> Listing:
    return _parse(text, _FILTER, False)


def parse_encoders(text: str) -> Listing:
    return _parse(text, _ENCODER, False)


def parse_demuxers(text: str) -> Listing:
    return _parse(text, _DEMUXER, True)


def parse_muxers(text: str) -> Listing:
    return _parse(text, _MUXER, True)


def parse_decoders(text: str) -> Listing:
    """`-decoders` uses the same six-character flag layout as `-encoders` (e.g. ` VFS..D h264`, ` A....D aac`)."""
    return _parse(text, _ENCODER, False)


PARSERS = {"-filters": parse_filters, "-encoders": parse_encoders, "-demuxers": parse_demuxers, "-muxers": parse_muxers, "-decoders": parse_decoders}
