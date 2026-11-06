"""Verify preserved source blobs and the unchanged production baseline."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main():
    lock = json.loads((ROOT / "docs/consolidation/source-lock.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "docs/consolidation/production-baseline.json").read_text(encoding="utf-8"))
    errors = []
    records = lock["final_files"] + baseline["phase1_frozen_files"]
    for row in records:
        path = ROOT / row["destination"]
        if not path.is_file() or blob_id(path.read_bytes()) != row["blob"]:
            errors.append(row["destination"])
    entries = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "--stage"]).decode()
    index = {line.split("\t", 1)[1]: line.split()[1] for line in entries.splitlines()}
    for path, expected in baseline["production_files"].items():
        if index.get(path) != expected:
            errors.append("production index: " + path)
    subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--exit-code", "--quiet", "--", *baseline["production_files"]],
        check=True,
    )
    report = {"status": "FAIL" if errors else "PASS", "frozen_files": len(records),
              "production_files": len(baseline["production_files"]), "errors": errors}
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
