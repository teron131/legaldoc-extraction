"""Normalize OCR punctuation and layout noise before joining chunks and recording their boundary lines."""

import re

_PRIVATE_USE_RE = re.compile(r"[\uf000-\uf8ff]")
_BULLET_RE = re.compile(
    r"[\u2022\u25cf\u25cb\u25aa\u25ab\u25e6\u25a0\u25a1\u2043\u2219]"
)
_MULTI_DASH_RE = re.compile(r"-  +")
_DASHES_RE = re.compile(r"-{2,}[ \t]*")
_TABLE_DELIMITER_RE = re.compile(r"(?=.*\|)[ \t|:-]+")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_PAGE_BREAK_RE = re.compile(r"(?m)^(?:-\s*){3,}-?\s*$")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_CHAR_REPLACEMENTS = {
    "&amp;": "&",
    "\u2019": "'",
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u200b": "",
    "\u00ad": "",
}


def clean_markdown(content: str) -> str:
    """Normalize OCR glyphs and spacing: curly quotes become straight, `"• Fee — due"` → `"- Fee - due"`, dash-only lines such as `"---"` or `"- - - -"` are dropped as page breaks while table delimiter rows such as `"|:---:|---|"` are kept, and runs of blank lines collapse to one."""
    if not content:
        return ""

    cleaned_content = _PRIVATE_USE_RE.sub("- ", content)
    cleaned_content = _BULLET_RE.sub("- ", cleaned_content)
    for old, new in _CHAR_REPLACEMENTS.items():
        cleaned_content = cleaned_content.replace(old, new)

    cleaned_content = _MULTI_DASH_RE.sub("- ", cleaned_content)
    # Page breaks go first so dash collapsing cannot turn a dash-only line into a bullet.
    cleaned_content = _PAGE_BREAK_RE.sub("", cleaned_content)
    cleaned_content = "\n".join(
        line if _TABLE_DELIMITER_RE.fullmatch(line) else _DASHES_RE.sub("- ", line)
        for line in cleaned_content.split("\n")
    )
    cleaned_content = _MULTI_SPACE_RE.sub(" ", cleaned_content)
    cleaned_content = _MULTI_NEWLINE_RE.sub("\n\n", cleaned_content)
    return cleaned_content.strip()
