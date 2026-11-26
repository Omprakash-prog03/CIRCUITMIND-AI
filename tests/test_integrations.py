import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from circuitmind.integrations import run_kicad_drc, run_kicad_erc, run_ngspice
from circuitmind.spice import validate_spice_deck


class SpiceDeckValidationTests(unittest.TestCase):
    def test_conventional_transient_deck_is_accepted(self):
        deck = """RC step response
V1 in 0 PULSE(0 5 0 1n 1n 1m 2m)
R1 in out 1k
C1 out 0 1u
.tran 1u 5m
.end
"""
        result = validate_spice_deck(deck)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["supported_analyses"], [".tran"])

    def test_control_file_access_unknown_directive_and_shell_escape_are_rejected(self):
        for line in (".control\n shell echo unsafe\n.endc", ".include /tmp/model.lib", ".mystery x", "R1 a 0 1k ! unsafe"):
            with self.subTest(line=line):
                deck = f"Unsafe test\n{line}\n.tran 1u 1m\n.end\n"
                result = validate_spice_deck(deck)
                self.assertFalse(result["accepted"])
                self.assertTrue(result["errors"])


class NgspiceIntegrationTests(unittest.TestCase):
    DECK = """RC step response
V1 in 0 PULSE(0 5 0 1n 1n 1m 2m)
R1 in out 1k
C1 out 0 1u
.tran 1u 5m
.end
"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "user_source.cir").write_text(self.DECK, "utf-8")
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fake_ngspice(self, output="No. of Data Rows : 101\n", exit_code=0, sleep_seconds=0):
        executable = self.bin / "ngspice"
        script = f'''#!{os.sys.executable}
import pathlib, sys, time
if {sleep_seconds!r}: time.sleep({sleep_seconds!r})
pathlib.Path(sys.argv[3]).write_bytes(b"raw-data")
print({output!r})
sys.exit({exit_code})
'''
        executable.write_text(script, "utf-8")
        executable.chmod(0o755)
        return executable

    def test_batch_adapter_captures_raw_and_requires_data_rows(self):
        self.fake_ngspice()
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_ngspice(self.project)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data_rows"], 101)
        self.assertEqual((self.project / result["raw_file"]).read_bytes(), b"raw-data")

    def test_missing_tool_timeout_and_error_outputs_are_not_success(self):
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(run_ngspice(self.project)["status"], "unavailable")
        self.fake_ngspice(sleep_seconds=.2)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            timeout = run_ngspice(self.project, timeout_seconds=.02)
        self.assertEqual(timeout["status"], "timeout")
        self.fake_ngspice("Error: singular matrix\nNo. of Data Rows : 0", exit_code=0)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            failed = run_ngspice(self.project)
        self.assertEqual(failed["status"], "failed")

    def test_unsafe_deck_is_rejected_before_invoking_tool(self):
        (self.project / "user_source.cir").write_text("unsafe\n.control\n shell echo bad\n.endc\n.tran 1u 1m\n.end\n", "utf-8")
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_ngspice(self.project)
        self.assertEqual(result["status"], "rejected")


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


class KiCadDrcIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "user_source.kicad_pcb").write_text('(kicad_pcb (version 20250101))\n', "utf-8")
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fake_cli(self, report="Found 0 DRC violations\nUnconnected pads: 0\n", exit_code=0, sleep_seconds=0):
        executable = self.bin / "kicad-cli"
        script = f'''#!{os.sys.executable}
import pathlib, sys, time
if {sleep_seconds!r}: time.sleep({sleep_seconds!r})
pathlib.Path(sys.argv[4]).write_text({report!r}, encoding="utf-8")
sys.exit({exit_code})
'''
        executable.write_text(script, "utf-8")
        executable.chmod(0o755)
        return executable

    def test_zero_counts_required_and_fixed_command_recorded(self):
        self.fake_cli()
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_drc(self.project)
        self.assertEqual(result["status"], "completed-no-reported-violations")
        self.assertEqual(result["drc_violation_count"], 0)
        self.assertEqual(result["unconnected_pad_count"], 0)
        self.assertEqual(len(result["board_sha256"]), 64)
        self.assertEqual(result["command"][1:3], ["pcb", "drc"])
        self.assertTrue((self.project / result["report_file"]).exists())

    def test_violations_nonzero_exit_and_unparsed_zero_exit_remain_distinct(self):
        self.fake_cli("Found 2 DRC violations\nUnconnected pads: 1\n")
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = run_kicad_drc(self.project)
        self.assertEqual(result["status"], "completed-with-violations")
        self.assertEqual(result["drc_violation_count"], 2)
        self.assertEqual(result["unconnected_pad_count"], 1)
        self.fake_cli("No structured summary\n", exit_code=0)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            unparsed = run_kicad_drc(self.project)
        self.assertEqual(unparsed["status"], "completed-unparsed")
        self.assertIsNone(unparsed["drc_violation_count"])
        self.fake_cli("Found 0 DRC violations\nUnconnected pads: 0\n", exit_code=1)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            failed = run_kicad_drc(self.project)
        self.assertEqual(failed["status"], "failed")

    def test_missing_tool_missing_board_bad_header_and_timeout(self):
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(run_kicad_drc(self.project)["status"], "unavailable")
        (self.project / "user_source.kicad_pcb").unlink()
        self.assertEqual(run_kicad_drc(self.project)["status"], "not-run")
        (self.project / "user_source.kicad_pcb").write_text("not a board", "utf-8")
        malformed = run_kicad_drc(self.project)
        self.assertEqual(malformed["status"], "not-run")
        self.assertIn("header", malformed["reason"])
        (self.project / "user_source.kicad_pcb").write_text("(kicad_pcb)", "utf-8")
        self.fake_cli(sleep_seconds=.2)
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            timeout = run_kicad_drc(self.project, timeout_seconds=.02)
        self.assertEqual(timeout["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
