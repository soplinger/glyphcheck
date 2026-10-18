# glyphcheck

**Find the invisible, the backwards and the look-alike characters hiding in your text files.**

glyphcheck is an offline Unicode auditor. It scans files for zero-width characters, bidirectional overrides (the "Trojan Source" trick), control codes, odd spaces and words that mix Latin with Cyrillic or Greek letters. Every finding comes with `file:line:col`, a severity, and a plain-English reason. It can also strip the safe-to-remove characters and gate a CI build on what it finds. It is a single Python file using only the standard library. It makes no network calls.

## Features

- **Invisible characters**: flags zero-width spaces, word joiners, soft hyphens, stray byte order marks in the middle of a file and Unicode tag characters.
- **Bidirectional attacks**: flags embedding, override and isolate controls as errors, and direction marks as info.
- **Control and replacement characters**: flags C0/C1 control codes, line and paragraph separators and U+FFFD.
- **Look-alike spaces**: flags no-break, narrow and typographic spaces that render like ordinary spaces.
- **Mixed-script words**: flags words that combine Latin with Cyrillic or Greek letters, a common spoofing pattern.
- **Encoding checks**: reports invalid UTF-8 with the exact line and column, and skips binary files.
- **Normalization check**: `--nfc` reports text that is not in NFC form.
- **Directory scanning**: walks folders in sorted order, skips hidden files and folders, and filters by extension with `--ext`.
- **Severity levels**: `--min-severity` controls what is shown, and `--fail-on` chooses which level makes the exit code 1.
- **Text or JSON output**: `--format json` gives a machine-readable report with a summary.
- **Summary view**: `--summary` breaks findings down by severity, category and codepoint.
- **Allow list**: `--allow U+00A0` ignores codepoints you use on purpose.
- **Safe fixer**: `fix` removes invisible and override characters and turns odd spaces into plain spaces. It keeps the BOM, CRLF line endings, emoji joiners and direction marks. `--write` edits the file in place.
- **Explain and rules**: `explain U+XXXX` says why a codepoint is flagged, and `rules` lists every rule.

## Quick start

Requires Python 3.9 or newer. No install step.

```console
$ python3 glyphcheck.py scan examples/notes.txt
examples/notes.txt:2:7: warning: [invisible] U+200B ZERO WIDTH SPACE: zero-width space, can split words or hide text
examples/notes.txt:3:15: error: [bidi] U+202E RIGHT-TO-LEFT OVERRIDE: bidirectional embedding or override, can make code read differently from how it runs
examples/notes.txt:4:8: warning: [mixed-script] word 'Marіa' mixes CYRILLIC, LATIN letters, a possible look-alike
3 finding(s) in 1 file(s)
$ echo $?
1
```

The sample file contains a zero-width space, a right-to-left override and a Cyrillic letter inside "Maria". Now see a summary and a cleaned copy:

```console
$ python3 glyphcheck.py scan examples/notes.txt --summary
...
summary:
  files scanned: 1
  severity: 1 error, 2 warning, 0 info
  by category: bidi 1, invisible 1, mixed-script 1
  by codepoint: U+200B x1, U+202E x1
...
$ python3 glyphcheck.py fix examples/notes.txt
Launch review notes
Budget approved on Tuesday.
Status: ready done
Owner: Marіa Lopez
Total: 42 items
Clean line with nothing to see.
2 character(s) would be removed or replaced
```

Explain a codepoint:

```console
$ python3 glyphcheck.py explain U+202E
U+202E RIGHT-TO-LEFT OVERRIDE
  category: bidi
  severity: error
  why: bidirectional embedding or override, can make code read differently from how it runs
```

## Usage

```text
glyphcheck scan [PATH ...] [--ext .py,.md] [--allow U+XXXX] [--nfc]
                [--min-severity {info,warning,error}]
                [--fail-on {error,warning,info,never}]
                [--format {text,json}] [--summary]
glyphcheck fix FILE [--allow U+XXXX] [--write]
glyphcheck explain U+XXXX [U+XXXX ...]
glyphcheck rules
```

Exit codes: `0` nothing at or above the `--fail-on` level, `1` a finding reached that level, `2` a usage or file error.

For CI, run `python3 glyphcheck.py scan . --fail-on warning` in a job step. The step fails on any warning or error.

## Rules

Run `python3 glyphcheck.py rules` for the full table. The short version:

| Category | Severity | Examples |
| --- | --- | --- |
| bidi | error | U+202A–U+202E, U+2066–U+2069 |
| control | error | U+0000–U+0008, U+007F–U+009F |
| invisible | warning / error | U+200B, U+2060–U+2064, mid-file U+FEFF, tag characters (error) |
| space | info / warning | U+00A0, U+2000–U+200A, U+3000, line separators (warning) |
| replacement | warning | U+FFFD |
| mixed-script | warning | Latin mixed with Cyrillic or Greek inside one word |
| encoding | error | invalid UTF-8 |
| normalization | info | not NFC (only with `--nfc`) |

Joiners (U+200C, U+200D) and direction marks (U+200E, U+200F, U+061C) are info only, because emoji and right-to-left text need them.

## Testing

The tests use `unittest`, so they run with either tool:

```console
$ python3 -m unittest discover -s tests -v
$ python3 -m pytest tests
```

The suite covers classification and positions, the fixer (including BOM, CRLF and joiner handling), encoding and binary handling, directory walking, every CLI exit code, JSON output, and the quick-start sample.

## Project layout

```text
glyphcheck.py          the whole tool: scanner, fixer, CLI
examples/notes.txt     a sample with three planted problems
examples/_make_notes.py regenerates the sample; the planted characters are spelled out as codepoints
tests/test_glyphcheck.py
```

## Limits

glyphcheck is a heuristic. Script detection uses Unicode character names, so it only compares Latin, Greek and Cyrillic. It does not detect every confusable pair, and it does not check identifiers against a confusables table. It reports what it sees, and the `--allow` list is how you silence intentional uses.
