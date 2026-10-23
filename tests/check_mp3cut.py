"""Split an MP3 at random points and check the decoded pieces reproduce the original."""
import os
import random
import subprocess
import sys
import tempfile

import numpy as np

from mixtaper import mp3cut


def decode(path: str) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "2", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<i2").reshape(-1, 2)


def main(src: str, n_cuts: int = 8) -> None:
    s = mp3cut.scan(src)
    lame_crc_ok = None
    if s.lame:
        # recompute the original tag CRC to validate our CRC-16 implementation
        x = s.offsets[0] - s.head.size if True else 0
        frame = s.mm[x: x + s.head.size]
        L = frame.find(s.lame[:9])
        lame_crc_ok = mp3cut.crc16(frame[: L + 34]) == int.from_bytes(frame[L + 34: L + 36], "big")
    print(f"frames={len(s.offsets)} spf={s.spf} delay={s.enc_delay} pad={s.enc_padding} cbr={s.cbr} "
          f"lame={s.lame[:9] if s.lame else None} crc_ok={lame_crc_ok} out_samples={s.output_samples}")
    total = s.output_samples
    s.close()
    ref = decode(src)
    print("ffmpeg decoded samples:", len(ref), "(match)" if len(ref) == total else "(MISMATCH)")
    rnd = random.Random(1)
    cuts = sorted(rnd.sample(range(total // 50, total - total // 50), n_cuts))
    bounds = list(zip([0] + cuts, cuts + [None]))
    with tempfile.TemporaryDirectory() as td:
        dests = [os.path.join(td, f"{i:02d}.mp3") for i in range(len(bounds))]
        mp3cut.split(src, bounds, dests)
        parts = [decode(d) for d in dests]
        size = sum(os.path.getsize(d) for d in dests)
    for (a, b), p in zip(bounds, parts):
        want = (b if b is not None else total) - a
        if len(p) != want:
            print(f"  piece {a}-{b}: {len(p)} samples, expected {want}")
    cat = np.concatenate(parts)
    print(f"pieces: {len(parts)}  bytes {size} vs original {os.path.getsize(src)}  samples {len(cat)} vs {len(ref)}")
    if len(cat) == len(ref):
        diff = np.abs(cat.astype(np.int32) - ref.astype(np.int32))
        bad = np.flatnonzero(diff.max(1) > 0)
        print(f"max |diff| = {diff.max()}  differing samples = {len(bad)}")
        for c in cuts:
            near = bad[(bad > c - 3000) & (bad < c + 3000)]
            if len(near):
                print(f"  around cut {c}: {len(near)} differing, max {diff[near].max()}")


if __name__ == "__main__":
    main(sys.argv[1], *(int(x) for x in sys.argv[2:]))
