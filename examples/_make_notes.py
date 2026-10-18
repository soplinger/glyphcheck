"""Regenerate examples/notes.txt. The planted characters are spelled out as codepoints."""

import os

ZWSP = chr(0x200B)  # zero-width space, line 2
RLO = chr(0x202E)  # right-to-left override, line 3
CYRILLIC_I = chr(0x0456)  # Cyrillic small letter byelorussian-ukrainian i, line 4

lines = [
    "Launch review notes",
    "Budget" + ZWSP + " approved on Tuesday.",
    "Status: ready " + RLO + "done",
    "Owner: Mar" + CYRILLIC_I + "a Lopez",
    "Total: 42 items",
    "Clean line with nothing to see.",
]

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "notes.txt"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(lines) + "\n")
