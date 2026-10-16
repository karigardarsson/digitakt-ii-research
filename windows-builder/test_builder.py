import tempfile
import unittest
from pathlib import Path

import builder_core as core
from builder_gui import PROGRESS_BY_MESSAGE, accepted_dialog_path


TEST_WORK = Path(__file__).resolve().parent / "test-work"
TEST_WORK.mkdir(exist_ok=True)


def test_directory():
    return tempfile.TemporaryDirectory(dir=TEST_WORK)


class BuilderValidationTests(unittest.TestCase):
    def test_resource_integrity(self):
        tool, overlay, manifest = core._load_resources()
        self.assertTrue(tool.is_file())
        self.assertEqual(core.hashlib.sha256(overlay).hexdigest(), core.OVERLAY_SHA256)
        self.assertEqual(manifest["device"], "Elektron Digitakt II")

    def test_missing_input(self):
        with test_directory() as name:
            with self.assertRaisesRegex(core.BuildError, "Select an official"):
                core.build_firmware(Path(name) / "missing.syx", Path(name) / "out.syx")

    def test_wrong_extension(self):
        with test_directory() as name:
            source = Path(name) / "stock.bin"
            source.write_bytes(b"wrong")
            with self.assertRaisesRegex(core.BuildError, "must be an Elektron"):
                core.build_firmware(source, Path(name) / "out.syx")

    def test_corrupted_input(self):
        with test_directory() as name:
            source = Path(name) / "stock.syx"
            source.write_bytes(b"not firmware")
            with self.assertRaisesRegex(core.BuildError, "not the verified official"):
                core.build_firmware(source, Path(name) / "out.syx")

    def test_input_cannot_be_output(self):
        with test_directory() as name:
            source = Path(name) / "stock.syx"
            source.write_bytes(b"wrong")
            with self.assertRaisesRegex(core.BuildError, "different filename"):
                core.build_firmware(source, source)

    def test_missing_output_directory(self):
        with test_directory() as name:
            source = Path(name) / "stock.syx"
            source.write_bytes(b"wrong")
            with self.assertRaisesRegex(core.BuildError, "output folder"):
                core.build_firmware(source, Path(name) / "missing" / "out.syx")

    def test_cancelled_selection_is_no_path(self):
        self.assertIsNone(accepted_dialog_path(""))

    def test_progress_stages_are_monotonic_and_stop_before_completion(self):
        self.assertEqual(list(PROGRESS_BY_MESSAGE.values()), [10, 30, 55, 80, 95])

    def test_existing_output_is_refused(self):
        with test_directory() as name:
            source = Path(name) / "stock.syx"
            output = Path(name) / "out.syx"
            source.write_bytes(b"wrong")
            output.write_bytes(b"keep")
            with self.assertRaisesRegex(core.BuildError, "already exists"):
                core.build_firmware(source, output)
            self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
