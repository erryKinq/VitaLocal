#!/usr/bin/env python3
"""Converts MuscleMap's body outlines (Swift sources) into the JSON the WebUI and the app draw.

MuscleMap by Melih Colpan, MIT License: https://github.com/melihcolpan/MuscleMap
Usage: tools/musclemap_to_json.py <MuscleMap checkout>

Writes server/src/vitalocal/web/body-paths.json and android/app/src/main/assets/body-paths.json:
per figure (male/female) and side (front/back) the viewBox and the shapes, each with the
VitaLocal muscle it shows (null: silhouette only). MuscleMap's sub-groups that are muscles of their own
here (upper/lower chest, front/rear delts, upper/lower traps, rhomboids, serratus, hip flexors,
adductors) are drawn on top of their parents; the parent shows what is left (the outer deltoid in front:
the side delts). The others (upper/lower abs, inner/outer quad) are dropped – their parent covers them.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = [ROOT / "server/src/vitalocal/web/body-paths.json", ROOT / "android/app/src/main/assets/body-paths.json"]

MUSCLE = {
    "chest": "chest_lower", "abs": "abs", "biceps": "biceps", "triceps": "triceps", "deltoids": "delts_side",
    "obliques": "obliques", "quadriceps": "quads", "calves": "calves", "trapezius": "traps",
    "forearm": "forearms", "tibialis": "tibialis", "upperBack": "lats", "rotatorCuff": "rotator_cuff",
    "lowerBack": "lower_back", "gluteal": "glutes", "hamstring": "hamstrings", "neck": "neck",
    # sub-groups that are muscles of their own: drawn after (on top of) everything else
    "upperChest": "chest_upper", "lowerChest": "chest_lower", "frontDeltoid": "delts_front",
    "rearDeltoid": "delts_rear", "upperTrapezius": "traps", "lowerTrapezius": "upper_back",
    "rhomboids": "upper_back", "serratus": "serratus", "hipFlexors": "hip_flexors", "adductors": "adductors",
}  # fmt: skip
# on the back the trapezius shows through between its parts: the upper back
BACK_MUSCLE = {"trapezius": "upper_back", "deltoids": "delts_rear"}
ON_TOP = {"upperChest", "lowerChest", "frontDeltoid", "rearDeltoid", "upperTrapezius", "lowerTrapezius", "rhomboids",
          "serratus", "hipFlexors", "adductors"}  # fmt: skip
INERT = {"hands", "knees", "ankles", "feet", "head", "hair"}
DROPPED = {"innerQuad", "outerQuad", "upperAbs", "lowerAbs"}
FILES = {("male", "front"): "MaleFrontPaths", ("male", "back"): "MaleBackPaths",
         ("female", "front"): "FemaleFrontPaths", ("female", "back"): "FemaleBackPaths"}  # fmt: skip
BLOCK = re.compile(r"BodyPartPathData\(\s*slug:\s*\.(\w+)(.*?)\n        \)", re.S)
VIEWBOX = re.compile(r"static let (\w+) = BodyViewBox\(\s*origin: CGPoint\(x: ([\d.]+), y: ([\d.]+)\),\s*"
                     r"size: CGSize\(width: ([\d.]+), height: ([\d.]+)\)", re.S)  # fmt: skip


NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def normalize(d: str) -> str:
    """Path data with every number separated: MuscleMap writes arc flags packed ("a.2.2 0 01-.19-.21"),
    which browsers read but Android's PathParser does not."""
    out, i, cmd, n = [], 0, None, 0
    while i < len(d):
        c = d[i]
        if c.isspace() or c == ",":
            i += 1
        elif c.isalpha():
            cmd, n = c, 0
            out.append(c)
            i += 1
        elif cmd in "aA" and n % 7 in (3, 4):  # a flag: one character, 0 or 1
            if c not in "01":
                raise ValueError(f"bad arc flag at {i}: {d[i:i + 10]!r}")
            out.append(c)
            n += 1
            i += 1
        else:
            m = NUMBER.match(d, i)
            if not m:
                raise ValueError(f"cannot read path data at {i}: {d[i:i + 10]!r}")
            out.append(m.group())
            n += 1
            i = m.end()
    return " ".join(out)


def main(src: Path) -> None:
    data = src / "Sources/MuscleMap/Data"
    boxes = {m[0]: [float(v) for v in m[1:]] for m in VIEWBOX.findall((data / "BodyPathData.swift").read_text())}
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    out = {
        "source": f"MuscleMap by Melih Colpan ({rev.stdout.strip() or 'unknown revision'}), MIT License, "
        "https://github.com/melihcolpan/MuscleMap",
        "license": (src / "LICENSE").read_text().strip(),
        "figures": {},
    }
    for (figure, side), name in FILES.items():
        shapes, top = [], []
        for slug, body in BLOCK.findall((data / f"{name}.swift").read_text()):
            if slug in DROPPED:
                continue
            if slug not in MUSCLE and slug not in INERT:
                raise SystemExit(f"{name}: unknown slug {slug}")
            quoted = re.findall(r'"([^"]*)"', body)
            if any(not q.lstrip().startswith(("M", "m")) for q in quoted):
                raise SystemExit(f"{name}/{slug}: path data that does not start with a move command")
            paths = [normalize(q) for q in quoted]
            muscle = (BACK_MUSCLE.get(slug) if side == "back" else None) or MUSCLE.get(slug)
            (top if slug in ON_TOP else shapes).append({"muscle": muscle, "d": paths})
        box = boxes[f"{figure}{side.capitalize()}"]
        out["figures"].setdefault(figure, {})[side] = {"viewBox": box, "shapes": shapes + top}
    text = json.dumps(out, separators=(",", ":"))
    for p in OUT:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
        print(f"{p.relative_to(ROOT)}: {len(text) // 1024} KiB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
