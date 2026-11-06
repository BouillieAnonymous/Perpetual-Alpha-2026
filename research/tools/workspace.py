"""Create a credential-free, disposable historical research workspace from Git."""

import argparse
import io
import shutil
import subprocess
import tarfile
from pathlib import Path


def materialize(source, target, experimental=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or target == source or target in source.parents:
        raise ValueError("Target must be a new directory outside source ancestry")
    archive = subprocess.check_output(["git", "-C", str(source), "archive", "--format=tar", "HEAD"])
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(target, filter="data")
    overlays = [target / "research/legacy"]
    if experimental:
        overlays.append(target / "research/experimental/websocket")
    for overlay in overlays:
        for name in ("src", "scripts", "tests", "config"):
            folder = overlay / name
            if not folder.exists():
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                destination = target / path.relative_to(overlay)
                if destination.exists() and destination.read_bytes() != path.read_bytes():
                    raise ValueError(f"Research overlay conflicts with baseline: {destination.relative_to(target)}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
    if (target / ".env").exists() or (target / ".git").exists():
        raise ValueError("Unexpected credentials or repository metadata in export")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--experimental", action="store_true")
    args = parser.parse_args()
    print(materialize(Path(__file__).resolve().parents[2], args.target, args.experimental))


if __name__ == "__main__":
    main()
