"""Fail CI when its interpreter or runtime libraries differ from the deployed contract."""
import platform
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    expected_python = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise ValueError(f"Expected server Python {expected_python}, got {actual_python}")
    count = 0
    for line in (ROOT / "deploy/requirements-check.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        actual = version(name)
        if actual != expected:
            raise ValueError(f"Expected server dependency {name}=={expected}, got {actual}")
        count += 1
    print(f"Server runtime verified: Python {actual_python}, {count} pinned dependencies")


if __name__ == "__main__":
    main()
