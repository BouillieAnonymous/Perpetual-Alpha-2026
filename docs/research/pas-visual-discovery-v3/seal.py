"""Seal final V3 files without modifying earlier research, raw cache or phase-1 audit."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
release = HERE / "release"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


record = {
    str(p.relative_to(release)).replace("\\", "/"): sha(p)
    for p in release.rglob("*")
    if p.is_file() and p.name != "manifest.json"
}
record["source_files"] = {p.name: sha(p) for p in HERE.iterdir() if p.is_file()}
record["phase1_audit"] = {p.name: sha(p) for p in (HERE / "audit-verified").iterdir() if p.is_file()}
(release / "manifest.json").write_bytes(json.dumps(record, indent=2).encode())
print(f"Sealed {len(record) - 2} release files plus sources and Phase1 audit")
