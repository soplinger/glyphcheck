#!/usr/bin/env python3
"""glyphcheck: find invisible, bidirectional and look-alike Unicode in text files.

Standard library only. Run `python3 glyphcheck.py --help` for the command list.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache

SEVERITIES = ("info", "warning", "error")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
FAIL_ON_CHOICES = ("error", "warning", "info", "never")
CONFUSABLE_SCRIPTS = frozenset({"LATIN", "GREEK", "CYRILLIC"})
WORD_RE = re.compile(r"[^\W\d_]+")
BOM = "﻿"

# (first codepoint, last codepoint, category, severity, reason)
RULES = (
    (0x0000, 0x0008, "control", "error", "C0 control character, invisible and rarely intended"),
    (0x000B, 0x000C, "control", "error", "vertical tab or form feed, breaks line-based tools"),
    (0x000E, 0x001F, "control", "error", "C0 control character, invisible and rarely intended"),
    (0x007F, 0x009F, "control", "error", "DEL or C1 control character"),
    (0x00A0, 0x00A0, "space", "info", "no-break space that looks like a normal space"),
    (0x00AD, 0x00AD, "invisible", "warning", "soft hyphen, invisible unless a line breaks there"),
    (0x034F, 0x034F, "invisible", "info", "combining grapheme joiner"),
    (0x061C, 0x061C, "bidi", "info", "Arabic letter mark, changes text direction"),
    (0x180E, 0x180E, "invisible", "warning", "Mongolian vowel separator, often a stray zero-width space"),
    (0x1680, 0x1680, "space", "info", "Ogham space mark that looks like a normal space"),
    (0x2000, 0x200A, "space", "info", "typographic space that looks like a normal space"),
    (0x200B, 0x200B, "invisible", "warning", "zero-width space, can split words or hide text"),
    (0x200C, 0x200D, "joiner", "info", "zero-width joiner or non-joiner, needed in emoji and Indic scripts"),
    (0x200E, 0x200F, "bidi", "info", "left-to-right or right-to-left mark"),
    (0x2028, 0x2029, "space", "warning", "line or paragraph separator, breaks line-based tools"),
    (0x202A, 0x202E, "bidi", "error", "bidirectional embedding or override, can make code read differently from how it runs"),
    (0x202F, 0x202F, "space", "info", "narrow no-break space that looks like a normal space"),
    (0x205F, 0x205F, "space", "info", "medium mathematical space"),
    (0x2060, 0x2064, "invisible", "warning", "word joiner or invisible operator"),
    (0x2066, 0x2069, "bidi", "error", "bidirectional isolate, can reorder code on screen (Trojan Source)"),
    (0x3000, 0x3000, "space", "info", "ideographic space that looks like a normal space"),
    (0xFEFF, 0xFEFF, "invisible", "warning", "byte order mark or zero-width no-break space inside a file"),
    (0xFFFD, 0xFFFD, "replacement", "warning", "replacement character, bytes were lost in an earlier decode"),
    (0xE0000, 0xE007F, "invisible", "error", "tag character, can hide an ASCII message"),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    col: int
    severity: str
    category: str
    codepoint: int | None
    message: str


def classify(codepoint):
    """Return (category, severity, reason) for a flagged codepoint, or None."""
    if 0x20 <= codepoint <= 0x7E or codepoint in (0x09, 0x0A, 0x0D):
        return None
    for first, last, category, severity, reason in RULES:
        if first <= codepoint <= last:
            return category, severity, reason
    return None


def char_name(ch):
    return unicodedata.name(ch, "<unnamed>")


@lru_cache(maxsize=None)
def script_of(ch):
    """Rough script label taken from the Unicode character name, e.g. LATIN."""
    return unicodedata.name(ch, "").split(" ", 1)[0]


def scan_text(text, path="<text>", *, check_nfc=False, allow=frozenset()):
    """Return every finding in text, ordered by line and column."""
    findings = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for col, ch in enumerate(line, start=1):
            cp = ord(ch)
            if cp in allow:
                continue
            if lineno == 1 and col == 1 and ch == BOM:
                continue  # a leading BOM is normal in many editors' output
            rule = classify(cp)
            if rule is None:
                continue
            category, severity, reason = rule
            message = f"U+{cp:04X} {char_name(ch)}: {reason}"
            findings.append(Finding(path, lineno, col, severity, category, cp, message))

        for match in WORD_RE.finditer(line):
            word = match.group()
            scripts = {script_of(c) for c in word} & CONFUSABLE_SCRIPTS
            if len(scripts) > 1:
                message = f"word {ascii(word)} mixes {', '.join(sorted(scripts))} letters, a possible look-alike"
                findings.append(Finding(path, lineno, match.start() + 1, "warning", "mixed-script", None, message))

        if check_nfc and not unicodedata.is_normalized("NFC", line):
            nfc = unicodedata.normalize("NFC", line)
            diff = next((i for i, (a, b) in enumerate(zip(line, nfc)) if a != b), min(len(line), len(nfc)))
            findings.append(Finding(path, lineno, diff + 1, "info", "normalization", None,
                                    "text is not in NFC form, so it may compare unequal to the same text typed differently"))

    findings.sort(key=lambda f: (f.line, f.col))
    return findings


def scan_file(path, *, check_nfc=False, allow=frozenset()):
    """Return (findings, skip_reason). skip_reason is set for binary files."""
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\0" in data:
        return [], "binary"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as err:
        line = data.count(b"\n", 0, err.start) + 1
        col = err.start - (data.rfind(b"\n", 0, err.start) + 1) + 1
        message = f"invalid UTF-8 byte 0x{data[err.start]:02X}, the rest of this file was not checked"
        return [Finding(path, line, col, "error", "encoding", None, message)], None
    return scan_text(text, path, check_nfc=check_nfc, allow=allow), None


def fix_text(text, allow=frozenset()):
    """Remove invisible and override characters and turn odd spaces into plain spaces.

    Joiners (ZWJ/ZWNJ), direction marks (LRM/RLM) and anything in `allow` are kept,
    because removing them can change emoji or right-to-left text. Returns (fixed, changes).
    """
    prefix = BOM if text.startswith(BOM) else ""
    out = [prefix]
    changes = 0
    for ch in text[len(prefix):]:
        cp = ord(ch)
        rule = None if cp in allow else classify(cp)
        if rule is None:
            out.append(ch)
            continue
        category, severity, _ = rule
        if category == "space" and severity == "info":
            out.append(" ")
            changes += 1
        elif category in ("invisible", "control") or (category == "bidi" and severity == "error"):
            changes += 1
        else:
            out.append(ch)
    return "".join(out), changes


def parse_codepoint(text):
    cleaned = text.strip()
    if cleaned[:2].upper() == "U+":
        cleaned = cleaned[2:]
    if not re.fullmatch(r"[0-9A-Fa-f]{1,6}", cleaned):
        raise ValueError(f"not a hex codepoint: {text!r}")
    value = int(cleaned, 16)
    if value > 0x10FFFF:
        raise ValueError(f"codepoint out of range: {text!r}")
    return value


def parse_allow(values):
    allowed = set()
    for value in values:
        for item in value.split(","):
            if item.strip():
                allowed.add(parse_codepoint(item))
    return frozenset(allowed)


def parse_extensions(text):
    exts = set()
    for item in text.split(","):
        item = item.strip().lower()
        if item:
            exts.add(item if item.startswith(".") else "." + item)
    return exts or None


def iter_files(paths, extensions=None):
    """Yield files to scan. Directories are walked in sorted order, skipping hidden entries."""
    for root in paths:
        if os.path.isdir(root):
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
                for name in sorted(filenames):
                    if name.startswith("."):
                        continue
                    if extensions and os.path.splitext(name)[1].lower() not in extensions:
                        continue
                    yield os.path.join(dirpath, name)
        elif os.path.isfile(root):
            yield root
        else:
            raise ValueError(f"no such file or directory: {root}")


def summarize(findings, scanned, skipped):
    severities = Counter(f.severity for f in findings)
    categories = Counter(f.category for f in findings)
    codepoints = Counter(f"U+{f.codepoint:04X}" for f in findings if f.codepoint is not None)
    return {
        "files_scanned": scanned,
        "files_skipped": [{"path": p, "reason": r} for p, r in skipped],
        "findings": len(findings),
        "by_severity": {s: severities.get(s, 0) for s in SEVERITIES},
        "by_category": dict(sorted(categories.items())),
        "by_codepoint": dict(codepoints.most_common()),
    }


def format_finding(f):
    return f"{f.path}:{f.line}:{f.col}: {f.severity}: [{f.category}] {f.message}"


def print_summary(summary):
    print("summary:")
    print(f"  files scanned: {summary['files_scanned']}")
    for skipped in summary["files_skipped"]:
        print(f"  skipped ({skipped['reason']}): {skipped['path']}")
    by_sev = summary["by_severity"]
    print("  severity: " + ", ".join(f"{by_sev[s]} {s}" for s in reversed(SEVERITIES)))
    if summary["by_category"]:
        print("  by category: " + ", ".join(f"{k} {v}" for k, v in summary["by_category"].items()))
    if summary["by_codepoint"]:
        print("  by codepoint: " + ", ".join(f"{k} x{v}" for k, v in summary["by_codepoint"].items()))


def cmd_scan(args):
    allow = parse_allow(args.allow)
    extensions = parse_extensions(args.ext)
    findings, skipped, scanned = [], [], 0
    for path in iter_files(args.paths, extensions):
        file_findings, reason = scan_file(path, check_nfc=args.nfc, allow=allow)
        if reason:
            skipped.append((path, reason))
            continue
        scanned += 1
        findings.extend(file_findings)

    floor = SEVERITY_RANK[args.min_severity]
    shown = [f for f in findings if SEVERITY_RANK[f.severity] >= floor]
    summary = summarize(shown, scanned, skipped)

    if args.format == "json":
        print(json.dumps({"summary": summary, "findings": [asdict(f) for f in shown]}, indent=2))
    else:
        for f in shown:
            print(format_finding(f))
        if args.summary:
            print_summary(summary)
        if shown:
            print(f"{len(shown)} finding(s) in {scanned} file(s)")
        else:
            print(f"no findings in {scanned} file(s)")

    if args.fail_on != "never":
        threshold = SEVERITY_RANK[args.fail_on]
        if any(SEVERITY_RANK[f.severity] >= threshold for f in findings):
            return 1
    return 0


def cmd_fix(args):
    allow = parse_allow(args.allow)
    with open(args.file, "rb") as fh:
        data = fh.read()
    if b"\0" in data:
        raise ValueError(f"{args.file} looks binary, refusing to fix it")
    fixed, changes = fix_text(data.decode("utf-8"), allow)
    if args.write:
        if changes:
            with open(args.file, "w", encoding="utf-8", newline="") as fh:
                fh.write(fixed)
        print(f"{args.file}: {changes} character(s) fixed", file=sys.stderr)
    else:
        sys.stdout.write(fixed)
        print(f"{changes} character(s) would be removed or replaced", file=sys.stderr)
    return 0


def cmd_explain(args):
    for raw in args.codepoints:
        cp = parse_codepoint(raw)
        print(f"U+{cp:04X} {char_name(chr(cp))}")
        rule = classify(cp)
        if rule is None:
            print("  not flagged by glyphcheck")
        else:
            category, severity, reason = rule
            print(f"  category: {category}")
            print(f"  severity: {severity}")
            print(f"  why: {reason}")
    return 0


def cmd_rules(_args):
    for first, last, category, severity, reason in RULES:
        span = f"U+{first:04X}" if first == last else f"U+{first:04X}-U+{last:04X}"
        print(f"{span:<18} {severity:<8} {category:<12} {reason}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="glyphcheck",
        description="Find invisible, bidirectional and look-alike Unicode in text files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan files and directories")
    scan.add_argument("paths", nargs="*", default=["."], help="files or directories (default: .)")
    scan.add_argument("--ext", default="", help="only scan these extensions inside directories, e.g. .py,.md")
    scan.add_argument("--allow", action="append", default=[], metavar="U+XXXX",
                      help="codepoint to ignore; repeatable or comma-separated")
    scan.add_argument("--nfc", action="store_true", help="also report text that is not in NFC form")
    scan.add_argument("--min-severity", choices=SEVERITIES, default="info",
                      help="hide findings below this level (default: info)")
    scan.add_argument("--fail-on", choices=FAIL_ON_CHOICES, default="error",
                      help="exit 1 when a finding reaches this level (default: error)")
    scan.add_argument("--format", choices=("text", "json"), default="text")
    scan.add_argument("--summary", action="store_true", help="print counts by severity, category and codepoint")
    scan.set_defaults(handler=cmd_scan)

    fix = sub.add_parser("fix", help="remove invisible and override characters from one file")
    fix.add_argument("file")
    fix.add_argument("--allow", action="append", default=[], metavar="U+XXXX",
                     help="codepoint to keep; repeatable or comma-separated")
    fix.add_argument("--write", action="store_true", help="rewrite the file instead of printing the result")
    fix.set_defaults(handler=cmd_fix)

    explain = sub.add_parser("explain", help="explain one or more codepoints")
    explain.add_argument("codepoints", nargs="+", metavar="U+XXXX")
    explain.set_defaults(handler=cmd_explain)

    rules = sub.add_parser("rules", help="list every rule")
    rules.set_defaults(handler=cmd_rules)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError) as err:
        print(f"glyphcheck: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
