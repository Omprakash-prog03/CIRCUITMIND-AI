import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from circuitmind.integrations import run_pdftotext


class PdfTextExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.pdf = b"%PDF-1.4\ncontrolled parser test\n%%EOF\n"
        self.digest = hashlib.sha256(self.pdf).hexdigest()
        (self.project / f"datasheet_{self.digest}.pdf").write_bytes(self.pdf)
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fake_pdftotext(self, output="page one text\fpage two absolute maximum rating\f", exit_code=0, sleep_seconds=0):
        executable = self.bin / "pdftotext"
        script = f'''#!{os.sys.executable}
import pathlib, sys, time
if {sleep_seconds!r}: time.sleep({sleep_seconds!r})
pathlib.Path(sys.argv[-1]).write_text({output!r}, encoding="utf-8")
print("controlled pdftotext fixture")
sys.exit({exit_code})
'''
        executable.write_text(script, "utf-8")
        executable.chmod(0o755)
        return executable

    def test_fixed_command_extracts_page_delimited_raw_text_and_source_hash(self):
        self.fake_pdftotext()
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_pdftotext(self.project, self.digest)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["source_sha256"], self.digest)
        self.assertEqual(result["command"][1:4], ["-layout", "-enc", "UTF-8"])
        self.assertIn("SOURCE PDF PAGE 1", result["extracted_text"])
        self.assertIn("SOURCE PDF PAGE 2", result["extracted_text"])
        self.assertIn("not been interpreted", result["interpretation"])

    def test_unavailable_timeout_missing_source_and_digest_mismatch_are_distinct(self):
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(run_pdftotext(self.project, self.digest)["status"], "unavailable")
        self.fake_pdftotext(sleep_seconds=.2)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            timeout = run_pdftotext(self.project, self.digest, timeout_seconds=.02)
        self.assertEqual(timeout["status"], "timeout")
        self.assertNotIn("extracted_text", timeout)
        self.assertEqual(run_pdftotext(self.project, "0" * 64)["status"], "not-run")
        (self.project / f"datasheet_{self.digest}.pdf").write_bytes(b"%PDF-1.4 tampered")
        mismatch = run_pdftotext(self.project, self.digest)
        self.assertEqual(mismatch["status"], "not-run")
        self.assertIn("SHA-256", mismatch["reason"])


if __name__ == "__main__":
    unittest.main()
