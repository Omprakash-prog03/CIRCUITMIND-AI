import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from circuitmind.integrations import run_kicad_erc


class KiCadIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "user_source.kicad_sch").write_text('(kicad_sch (version 20250101))\n', "utf-8")
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fake_cli(self, report='ERC report\n** ERC messages: 0 **\n', exit_code=0, sleep_seconds=0, stdout_payload="captured CLI output"):
        executable = self.bin / "kicad-cli"
        script = f'''#!{os.sys.executable}
import pathlib, sys, time
if {sleep_seconds!r}: time.sleep({sleep_seconds!r})
report_path = pathlib.Path(sys.argv[4])
report_path.write_text({report!r}, encoding="utf-8")
print({stdout_payload!r})
sys.exit({exit_code})
'''
        executable.write_text(script, "utf-8")
        executable.chmod(0o755)
        return executable

    def test_runs_fixed_cli_command_and_records_zero_message_summary(self):
        self.fake_cli()
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_erc(self.project)
        self.assertEqual(result["status"], "completed-no-messages")
        self.assertEqual(result["erc_message_count"], 0)
        self.assertEqual(result["command"][1:3], ["sch", "erc"])
        self.assertTrue((self.project / result["report_file"]).exists())

    def test_exit_code_and_report_messages_are_not_misrepresented(self):
        self.fake_cli("ERC report\n** ERC messages: 3 **\n", exit_code=1)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_erc(self.project)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["erc_message_count"], 3)

    def test_subprocess_output_is_bounded_to_the_tail(self):
        self.fake_cli(stdout_payload="Z" * 200_000)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_erc(self.project)
        self.assertEqual(result["status"], "completed-no-messages")
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 64_000)
        self.assertTrue(result["stdout"].rstrip().endswith("Z"))

    def test_zero_exit_without_recognized_summary_is_not_called_clean(self):
        self.fake_cli("No structured summary emitted\n")
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_erc(self.project)
        self.assertEqual(result["status"], "completed-unparsed")
        self.assertIsNone(result["erc_message_count"])

    def test_unavailable_bad_input_and_timeout_are_distinct(self):
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            unavailable = run_kicad_erc(self.project)
        self.assertEqual(unavailable["status"], "unavailable")
        self.fake_cli(sleep_seconds=.2)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            timeout = run_kicad_erc(self.project, timeout_seconds=.02)
        self.assertEqual(timeout["status"], "timeout")
        (self.project / "user_source.kicad_sch").write_text("not a schematic", "utf-8")
        malformed = run_kicad_erc(self.project)
        self.assertEqual(malformed["status"], "not-run")
        self.assertIn("header", malformed["reason"])


if __name__ == "__main__":
    unittest.main()
