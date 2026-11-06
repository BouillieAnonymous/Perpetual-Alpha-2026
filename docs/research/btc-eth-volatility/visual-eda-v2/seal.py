"""Seal current V2 artifacts after validation; never alters V1 or source caches."""

import json
import sys
from pathlib import Path

import build_v2 as b

out = Path(sys.argv[1]) if len(sys.argv) > 1 else b.HERE / "release"
manifest = {
    str(p.relative_to(out)).replace("\\", "/"): b.base.filehash(p)
    for p in out.rglob("*")
    if p.is_file() and p.name != "manifest.json"
}
# Include the nested V1 manifest too (the exclusion above applies by basename).
manifest["v1-frozen/manifest.json"] = b.base.filehash(out / "v1-frozen/manifest.json")
manifest["sources"] = {p.name: b.base.filehash(p) for p in b.HERE.iterdir() if p.is_file()}
b.write(out / "manifest.json", json.dumps(manifest, indent=2))
print(f"Sealed {len(manifest) - 1} artifacts; browser status stays explicit")
