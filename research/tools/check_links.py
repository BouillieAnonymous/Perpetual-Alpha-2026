"""Check maintained navigation without rewriting historical frozen documents."""

import json
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]


def main():
    paths = [ROOT / "README.md", ROOT / "docs/README.md", ROOT / "docs/research/INDEX.md",
             ROOT / "research/README.md", *sorted((ROOT / "docs/consolidation").glob("*.md"))]
    errors = []
    count = 0
    for path in paths:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            count += 1
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not (path.parent / target).exists():
                errors.append({"document": str(path.relative_to(ROOT)), "target": target})
    print(json.dumps({"status": "FAIL" if errors else "PASS", "links": count, "errors": errors}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
