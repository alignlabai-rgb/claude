Panel order A/C: NOM, E01, E02, E03, E04, E05, E06, E07, E08, E12 (3 columns, row-major).  B/D: same order without NOM.
A,B decode the stored raw yuv420p with the exact-rational BT.709 limited oracle (nearest chroma); C,D are the stored ffmpeg rgb24 readbacks of the mp4.
B/D: grey level = 40 x max channel error (255 = error >= 7). E: flat 256x144 #30506a, baseline (E09) vs +format=gbrp (E10).
IMPORTANT: E01-E05, E08, E12 build the chart from 32 separately converted flat sources, so their patch seams are unrealistically clean;
only E06/E07 (PNG through swscale) show real chroma-subsampling smear at seams.  Not a perceptual quality verdict.
