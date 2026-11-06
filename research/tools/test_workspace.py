"""Regression checks for the disposable layout's isolation boundary."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace import materialize


def archive(files):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as stream:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    return result.getvalue()


class WorkspaceIsolationTests(unittest.TestCase):
    def test_research_modules_are_only_overlaid_in_new_export(self):
        files = {"src/perpetual_alpha/__init__.py": b"", "research/legacy/src/perpetual_alpha/offline.py": b"x=1"}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            with patch("workspace.subprocess.check_output", return_value=archive(files)):
                target = materialize(source, Path(directory) / "export")
            self.assertEqual((target / "src/perpetual_alpha/offline.py").read_bytes(), b"x=1")
            self.assertFalse((source / "src").exists())

    def test_different_existing_runtime_module_cannot_be_replaced(self):
        files = {"src/perpetual_alpha/risk.py": b"production", "research/legacy/src/perpetual_alpha/risk.py": b"old"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("workspace.subprocess.check_output", return_value=archive(files)):
                with self.assertRaisesRegex(ValueError, "conflicts with baseline"):
                    materialize(Path(directory) / "source", Path(directory) / "export")
            self.assertEqual((Path(directory) / "export/src/perpetual_alpha/risk.py").read_bytes(), b"production")

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "new directory"):
                materialize(Path(directory) / "source", Path(directory))

    def test_archive_cannot_escape_target(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("workspace.subprocess.check_output", return_value=archive({"../escape": b"x"})):
                with self.assertRaises(tarfile.FilterError):
                    materialize(Path(directory) / "source", Path(directory) / "export")
            self.assertFalse((Path(directory) / "escape").exists())

    def test_credentials_in_tracked_export_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("workspace.subprocess.check_output", return_value=archive({".env": b"FAKE=fixture"})):
                with self.assertRaisesRegex(ValueError, "credentials"):
                    materialize(Path(directory) / "source", Path(directory) / "export")


if __name__ == "__main__":
    unittest.main()
