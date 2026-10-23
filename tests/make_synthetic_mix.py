"""Build a synthetic DJ mix with known ground truth from local tracks (for testing only).

usage: python tests/make_synthetic_mix.py OUT_BASENAME SPEC.json
SPEC: [{"path": ..., "from": seconds_into_track, "len": seconds, "rate": 1.0, "loop": false}, ...]
Writes OUT.flac, OUT.mp3 (LAME 320) and OUT.truth.json with each segment's crossfade window.
"""
import json
import subprocess
import sys

FADE = 30.0


def main(out: str, spec_path: str) -> None:
    spec = json.load(open(spec_path))
    args, chains = [], []
    for i, s in enumerate(spec):
        if s.get("loop"):
            args += ["-stream_loop", "-1"]
        args += ["-ss", str(s["from"]), "-t", str(s["len"] * s.get("rate", 1.0)), "-i", s["path"]]
        f = f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo"
        if s.get("rate", 1.0) != 1.0:
            f += f",asetrate={44100 * s['rate']:.0f},aresample=44100"
        chains.append(f + f"[s{i}]")
    cur = "s0"
    for i in range(1, len(spec)):
        chains.append(f"[{cur}][s{i}]acrossfade=d={FADE}:c1=tri:c2=tri[x{i}]")
        cur = f"x{i}"
    graph = ";".join(chains)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", graph, "-map", f"[{cur}]",
                    "-c:a", "flac", out + ".flac"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out + ".flac", "-c:a", "libmp3lame", "-b:a", "320k",
                    out + ".mp3"], check=True)
    truth, pos = [], 0.0
    for i, s in enumerate(spec):
        start = pos if i == 0 else pos - FADE
        truth.append({"name": s.get("name", s["path"].split("/")[-1]), "fade_start": start,
                      "fade_end": start + (FADE if i else 0)})
        pos = start + s["len"]
    json.dump(truth, open(out + ".truth.json", "w"), indent=1)
    print(json.dumps(truth, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
