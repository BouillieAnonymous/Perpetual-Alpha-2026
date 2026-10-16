"""Container-local loop progress, independent of HTTP claim readiness or risk permission."""
import argparse
import json
import os
import time
from pathlib import Path

from ..journal import canonical


def publish(path, status, ready):
    target = Path(path)
    temporary = target.with_name(target.name+".tmp")
    temporary.write_text(canonical({"pid": os.getpid(), "monotonic": time.monotonic(),
                                    "observed_ms": time.time_ns()//1000000,
                                    "generation": status["generation"], "paused": status["paused"],
                                    "ready": bool(ready), "mode": "synthetic-paper"}), encoding="utf-8")
    os.replace(temporary, target)


def probe(path, max_age=15):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    age = time.monotonic()-data["monotonic"]
    if data["mode"] != "synthetic-paper" or not 0 <= age <= max_age:
        raise ValueError("Paper loop progress missing or stale")
    if type(data["pid"]) is not int or data["pid"] <= 0:
        raise ValueError("Invalid Paper process")
    os.kill(data["pid"], 0)
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", default="/tmp/paper-progress.json")
    args = p.parse_args()
    print(canonical(probe(args.path)))


if __name__ == "__main__":
    main()
