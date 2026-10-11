"""Soak test: push many randomly mutated specs through the whole pipeline and report what happens.

    python scripts/soak.py --n 20000 --seed 1

Outcomes that are fine: the spec is rejected (ValidationError) or processed (gate pass/fail). Anything else is a bug.
Also reports the slowest single run and the growth of the process's memory over the run.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import string
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storymap_gen.api import build, load_store  # noqa: E402


def rand_json(rng: random.Random, depth: int = 0):
    kind = rng.choice(["none", "bool", "int", "float", "str", "list", "dict", "bigstr", "weird"] if depth < 3 else ["none", "int", "str"])
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.choice([0, 1, -1, 2**31, -(2**63), 10**30, rng.randint(-1000, 1000)])
    if kind == "float":
        return rng.choice([0.0, -0.0, 1e308, 1e-308, float("nan"), float("inf"), -float("inf"), rng.random() * 1000])
    if kind == "str":
        return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 30)))
    if kind == "bigstr":
        return "x" * rng.choice([300, 5001, 10_001, 100_000])
    if kind == "weird":
        return rng.choice(["\u0000", "😀", "‮", "../../etc/passwd", "<script>", "javascript:alert(1)", "__import__('os')", "(a+)+$"])
    if kind == "list":
        return [rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rand_json(rng, 3) if isinstance(rand_json(rng, 3), str) else "k": rand_json(rng, depth + 1) for _ in range(rng.randint(0, 3))}


def paths(obj, prefix=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from paths(v, prefix + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from paths(v, prefix + (i,))
    else:
        yield prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    fx = ROOT / "fixtures"
    store = load_store({"streets_centerline": fx / "streets_centerline.geojson", "parks_": fx / "parks.geojson",
                        "subway_stations_": fx / "subway_stations.geojson", "eixample": fx / "eixample_streets.geojson",
                        **{n: fx / "gracia" / f"{n}.geojson" for n in ("streets", "parks", "transit", "schools")}}, root=ROOT)
    seeds = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "examples").glob("*.json")]
    outcomes: Counter[str] = Counter()
    bad: list[str] = []
    slowest = 0.0
    tracemalloc.start()
    base = None
    for i in range(args.n):
        spec = copy.deepcopy(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            ps = list(paths(spec))
            if not ps:
                break
            target = rng.choice(ps)
            node = spec
            for key in target[:-1]:
                node = node[key]
            if target:
                node[target[-1]] = rand_json(rng)
        t0 = time.perf_counter()
        try:
            r = build(spec, store)
            outcomes["accepted:" + ("gate-pass" if r.passed else "gate-fail")] += 1
        except ValidationError:
            outcomes["rejected:validation"] += 1
        except Exception as e:  # noqa: BLE001 - this is exactly what the soak test is looking for
            outcomes["CRASH:" + type(e).__name__] += 1
            bad.append(f"#{i} {type(e).__name__}: {str(e)[:120]}")
        slowest = max(slowest, time.perf_counter() - t0)
        if i == 500:
            base = tracemalloc.get_traced_memory()[0]
    cur = tracemalloc.get_traced_memory()[0]
    print(f"runs: {args.n}  seed: {args.seed}")
    for k, v in sorted(outcomes.items()):
        print(f"  {k:32s} {v}")
    print(f"slowest single run: {slowest * 1000:.0f} ms")
    if base is not None:
        print(f"python memory growth after warm-up: {(cur - base) / 1024:.0f} KiB over {args.n - 500} runs")
    for line in bad[:10]:
        print("  CRASH", line)
    return 1 if any(k.startswith("CRASH") for k in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
