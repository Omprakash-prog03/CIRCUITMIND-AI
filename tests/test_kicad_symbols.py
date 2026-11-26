import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from circuitmind.kicad_symbols import _parse_sexpr, resolve_kicad_symbol


SYMBOL_LIBRARY = '''(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
  (symbol "R"
    (property "Reference" "R")
    (property "Value" "R")
    (symbol "R_0_1" (rectangle (start -1 -1) (end 1 1)))
    (symbol "R_1_1"
      (pin passive line (at -5 0 0) (length 2.54)
        (name "A") (number "1"))
      (pin passive line (at 5 0 180) (length 2.54)
        (name "B") (number "2")))
  )
)'''


class KiCadSymbolLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.symbols = self.root / "symbols"
        self.symbols.mkdir()
        (self.symbols / "Device.kicad_sym").write_text(SYMBOL_LIBRARY, "utf-8")
        self.table = self.root / "sym-lib-table"
        self.table.write_text('''(sym_lib_table (version 7)
  (lib (name "Device") (type "KiCad")
    (uri "${KICAD_SYMBOL_DIR}/Device.kicad_sym")
    (options "") (descr "test symbols")))''', "utf-8")
        self.env = patch.dict(os.environ, {"KICAD_SYMBOL_LIB_TABLE": str(self.table), "KICAD_SYMBOL_DIR": str(self.symbols)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_resolves_exact_symbol_and_pins_with_source_hash(self):
        result = resolve_kicad_symbol("Device", "R")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["properties"]["Reference"], "R")
        self.assertEqual([(pin["number"], pin["name"], pin["type"]) for pin in result["pins"]],
                         [("1", "A", "passive"), ("2", "B", "passive")])
        self.assertTrue(result["pin_manifest_complete"])
        self.assertEqual(len(result["library_sha256"]), 64)

    def test_missing_library_and_symbol_are_not_invented(self):
        missing_library = resolve_kicad_symbol("Missing", "U")
        self.assertEqual(missing_library["status"], "unavailable")
        missing_symbol = resolve_kicad_symbol("Device", "U")
        self.assertEqual(missing_symbol["status"], "not-found")
        self.assertNotIn("pins", missing_symbol)

    def test_inherited_symbols_are_marked_incomplete(self):
        content = '(kicad_symbol_lib (symbol "Child" (extends "Parent") (symbol "Child_0_1" (pin input line (name "IN") (number "1")))))'
        (self.symbols / "Device.kicad_sym").write_text(content, "utf-8")
        result = resolve_kicad_symbol("Device", "Child")
        self.assertEqual(result["status"], "resolved-inheritance-unexpanded")
        self.assertFalse(result["pin_manifest_complete"])
        self.assertTrue(result["notes"])

    def test_parser_rejects_malformed_and_overdeep_expressions(self):
        with self.assertRaisesRegex(ValueError, "unterminated"):
            _parse_sexpr("(root (child)")
        with self.assertRaisesRegex(ValueError, "closing"):
            _parse_sexpr(")")
        with patch("circuitmind.kicad_symbols._MAX_DEPTH", 2):
            with self.assertRaisesRegex(ValueError, "nesting"):
                _parse_sexpr("(a (b (c)))")

    def test_invalid_identifiers_fail_closed(self):
        self.assertEqual(resolve_kicad_symbol("../Device", "R")["status"], "invalid-id")
        self.assertEqual(resolve_kicad_symbol("Device", "R:Variant")["status"], "invalid-id")


if __name__ == "__main__":
    unittest.main()
