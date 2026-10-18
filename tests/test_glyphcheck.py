import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import glyphcheck  # noqa: E402


def run_cli(*argv):
    """Run main() and capture (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = glyphcheck.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ScanTextTests(unittest.TestCase):
    def test_zero_width_space_reported_with_line_and_column(self):
        findings = glyphcheck.scan_text("Budget​ approved")
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual((f.line, f.col), (1, 7))
        self.assertEqual((f.severity, f.category), ("warning", "invisible"))
        self.assertEqual(f.codepoint, 0x200B)

    def test_bidi_override_is_an_error(self):
        findings = glyphcheck.scan_text("ready ‮done")
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].severity, findings[0].category), ("error", "bidi"))
        self.assertEqual(findings[0].col, 7)

    def test_direction_mark_is_info_not_error(self):
        findings = glyphcheck.scan_text("a‎b")
        self.assertEqual([f.severity for f in findings], ["info"])

    def test_joiner_is_info_because_emoji_and_indic_need_it(self):
        findings = glyphcheck.scan_text("a‍b")
        self.assertEqual((findings[0].severity, findings[0].category), ("info", "joiner"))

    def test_leading_bom_is_ignored_but_mid_file_bom_is_flagged(self):
        self.assertEqual(glyphcheck.scan_text("﻿hello"), [])
        findings = glyphcheck.scan_text("hi\nx﻿y")
        self.assertEqual([(f.line, f.col, f.severity) for f in findings], [(2, 2, "warning")])

    def test_no_break_space_is_info_and_fix_turns_it_into_a_space(self):
        findings = glyphcheck.scan_text("Total: 42")
        self.assertEqual((findings[0].severity, findings[0].col), ("info", 7))
        fixed, changes = glyphcheck.fix_text("Total: 42")
        self.assertEqual((fixed, changes), ("Total: 42", 1))

    def test_control_character_is_an_error(self):
        findings = glyphcheck.scan_text("a\x07b")
        self.assertEqual((findings[0].severity, findings[0].category), ("error", "control"))

    def test_mixed_latin_and_cyrillic_word_is_flagged(self):
        findings = glyphcheck.scan_text("Owner: Marіa Lopez")
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].category, findings[0].col), ("mixed-script", 8))
        self.assertIn("LATIN", findings[0].message)
        self.assertIn("CYRILLIC", findings[0].message)

    def test_single_script_words_are_not_flagged(self):
        greek = "Ελληνικά"
        cyrillic = "Привет"
        self.assertEqual(glyphcheck.scan_text(greek + " " + cyrillic + " plain"), [])

    def test_digits_split_words_so_mixed_script_is_not_reported_across_them(self):
        self.assertEqual(glyphcheck.scan_text("x1а abc_def"), [])

    def test_nfc_check_is_opt_in(self):
        decomposed = "Café"
        self.assertEqual(glyphcheck.scan_text(decomposed), [])
        findings = glyphcheck.scan_text(decomposed, check_nfc=True)
        self.assertEqual([(f.category, f.severity, f.col) for f in findings], [("normalization", "info", 4)])

    def test_allow_list_suppresses_a_codepoint(self):
        self.assertEqual(glyphcheck.scan_text("a​b", allow=frozenset({0x200B})), [])


class FixTextTests(unittest.TestCase):
    def test_fix_removes_invisible_and_overrides_but_keeps_joiners_and_marks(self):
        text = "a​b‍c‎d‮e"
        fixed, changes = glyphcheck.fix_text(text)
        self.assertEqual(fixed, "ab‍c‎de")
        self.assertEqual(changes, 2)

    def test_fix_is_idempotent(self):
        text = "x​y z‮﻿\n"
        once, _ = glyphcheck.fix_text(text)
        twice, changes = glyphcheck.fix_text(once)
        self.assertEqual(once, twice)
        self.assertEqual(changes, 0)

    def test_fix_keeps_leading_bom_and_crlf_line_endings(self):
        fixed, _ = glyphcheck.fix_text("﻿line1\r\nline2​\r\n")
        self.assertEqual(fixed, "﻿line1\r\nline2\r\n")

    def test_fix_respects_allow_list(self):
        fixed, changes = glyphcheck.fix_text("a​b", allow=frozenset({0x200B}))
        self.assertEqual((fixed, changes), ("a​b", 0))


class FileAndPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, data):
        path = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": ""})) as fh:
            fh.write(data)
        return path

    def test_invalid_utf8_reports_encoding_error_with_position(self):
        path = self.write("bad.txt", b"ok\nbad\xff\n")
        findings, reason = glyphcheck.scan_file(path)
        self.assertIsNone(reason)
        self.assertEqual([(f.category, f.severity, f.line, f.col) for f in findings],
                         [("encoding", "error", 2, 4)])

    def test_binary_file_is_skipped(self):
        path = self.write("blob.bin", b"\x00\x01\x02")
        self.assertEqual(glyphcheck.scan_file(path), ([], "binary"))

    def test_directory_walk_skips_hidden_entries_and_filters_extensions(self):
        self.write("a.md", "x")
        self.write("b.py", "x")
        self.write("sub/d.md", "x")
        self.write(".hidden/c.md", "x")
        self.write(".secret.md", "x")
        names = {os.path.basename(p) for p in glyphcheck.iter_files([self.tmp.name], {".md"})}
        self.assertEqual(names, {"a.md", "d.md"})

    def test_missing_path_is_a_value_error(self):
        with self.assertRaises(ValueError):
            list(glyphcheck.iter_files([os.path.join(self.tmp.name, "nope")]))

    def test_parse_codepoint_accepts_prefix_and_rejects_junk(self):
        self.assertEqual(glyphcheck.parse_codepoint("U+202E"), 0x202E)
        self.assertEqual(glyphcheck.parse_codepoint("202e"), 0x202E)
        with self.assertRaises(ValueError):
            glyphcheck.parse_codepoint("zz")
        with self.assertRaises(ValueError):
            glyphcheck.parse_codepoint("110000")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def file_with(self, text, name="doc.txt"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def test_clean_file_exits_zero(self):
        code, out, _ = run_cli("scan", self.file_with("plain text\n"))
        self.assertEqual(code, 0)
        self.assertIn("no findings in 1 file(s)", out)

    def test_error_finding_exits_one_by_default(self):
        code, out, _ = run_cli("scan", self.file_with("ready ‮done\n"))
        self.assertEqual(code, 1)
        self.assertIn(": error: [bidi]", out)

    def test_fail_on_never_always_exits_zero(self):
        code, _, _ = run_cli("scan", "--fail-on", "never", self.file_with("ready ‮done\n"))
        self.assertEqual(code, 0)

    def test_warning_only_passes_default_threshold(self):
        code, _, _ = run_cli("scan", self.file_with("a​b\n"))
        self.assertEqual(code, 0)
        code, _, _ = run_cli("scan", "--fail-on", "warning", self.file_with("a​b\n"))
        self.assertEqual(code, 1)

    def test_missing_path_exits_two(self):
        code, _, err = run_cli("scan", os.path.join(self.tmp.name, "missing"))
        self.assertEqual(code, 2)
        self.assertIn("no such file or directory", err)

    def test_json_output_has_summary_and_findings(self):
        code, out, _ = run_cli("scan", "--format", "json", self.file_with("a​b\n"))
        data = json.loads(out)
        self.assertEqual(data["summary"]["findings"], 1)
        self.assertEqual(data["summary"]["by_codepoint"], {"U+200B": 1})
        self.assertEqual(data["findings"][0]["line"], 1)

    def test_min_severity_hides_lower_findings_in_output(self):
        path = self.file_with("a b‮c\n")
        _, out, _ = run_cli("scan", "--min-severity", "error", path)
        self.assertIn("[bidi]", out)
        self.assertNotIn("[space]", out)

    def test_fix_write_rewrites_file_in_place(self):
        path = self.file_with("a​b\n")
        code, _, _ = run_cli("fix", "--write", path)
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), "ab\n")

    def test_explain_names_codepoint_and_rule(self):
        code, out, _ = run_cli("explain", "U+202E")
        self.assertEqual(code, 0)
        self.assertIn("RIGHT-TO-LEFT OVERRIDE", out)
        self.assertIn("severity: error", out)

    def test_rules_lists_bidi_range(self):
        code, out, _ = run_cli("rules")
        self.assertEqual(code, 0)
        self.assertIn("U+202A-U+202E", out)


class QuickStartExampleTests(unittest.TestCase):
    """The README quick start runs `scan examples/notes.txt` against the bundled sample."""

    def test_example_reports_expected_positions(self):
        code, out, _ = run_cli("scan", os.path.join(ROOT, "examples", "notes.txt"))
        self.assertEqual(code, 1)
        self.assertIn("notes.txt:2:7: warning: [invisible]", out)
        self.assertIn("notes.txt:3:15: error: [bidi]", out)
        self.assertIn("notes.txt:4:8: warning: [mixed-script]", out)
        self.assertIn("3 finding(s) in 1 file(s)", out)


if __name__ == "__main__":
    unittest.main()
