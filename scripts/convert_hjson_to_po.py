#!/usr/bin/env python3
"""Convert a VanillaTranslations .hjson template into a gettext .po file.

The msgid for every entry comes from the English Source/*.json files (looked
up by the dotted section.key path). The msgstr is the current value from the
.hjson file whenever it differs from the English msgid (i.e. it is an actual
translation); strings that are still untranslated get a blank msgstr, which is
the gettext marker translators use for "not yet translated". Translations from
pl-PL, ru-RU and uk-UA source files are added as "# [xx]" comment lines so a
translator can see how related languages handled the same string.

Usage:
    python3 scripts/convert_hjson_to_po.py --input Localization/VanillaTranslations/be-BY.hjson

Output is written to Localization/be-BY.po unless --output is given.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

try:
    import hjson
except ImportError:
    sys.stderr.write(
        "The `hjson` package is required to parse the input files."
        " Install it with:  pip install hjson\n"
    )
    sys.exit(1)


_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _strip_quotes(token: str) -> str:
    """Remove surrounding double quotes from an hjson value token."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def _strip_indent(line: str, n: int) -> str:
    """Remove up to `n` leading whitespace characters from a line."""
    removed = 0
    i = 0
    while i < len(line) and removed < n and line[i] in " \t":
        i += 1
        removed += 1
    return line[i:]


def _parse_hjson_comment_blocks(text: str) -> dict:
    """Extract values stored as commented /* Key: '''...''' */ blocks.

    Walks the file tracking section nesting so each block gets its full dotted
    path, and strips Hjson-style leading indentation (matching the closing
    ''' delimiter) from the body. Used to recover multi-line values that
    hjson.load would otherwise ignore.
    """
    result = {}
    path: list[str] = []
    depth = 0
    lines = text.split("\n")
    n = len(lines)
    i = 0

    # Key followed by '{' on the same line -> opening of a nested section.
    section_open = re.compile(
        r"^\s*(?:(?P<q>\"[^\"]*\")|(?P<b>[A-Za-z0-9_.\-]+)):\s*\{\s*$"
    )
    block_start = re.compile(
        r"^\s*/\*\s*(?:(?P<q>\"[^\"]*\")|(?P<b>[A-Za-z0-9_.\-]+)):\s*(?:'''\s*)?$"
    )
    block_open = re.compile(r"^\s*'''\s*$")
    block_end = re.compile(r"^(\s*)'''\s*\*/\s*$")

    while i < n:
        line = lines[i]

        m_open = section_open.match(line)
        if m_open:
            key = m_open.group("q") or m_open.group("b")
            path.append(_strip_quotes(key))
            depth += 1
            i += 1
            continue

        if depth > 0 and line.strip() == "}":
            depth -= 1
            if path:
                path.pop()
            i += 1
            continue

        m_start = block_start.match(line)
        if m_start:
            key = m_start.group("q") or m_start.group("b")
            key = _strip_quotes(key)
            full_key = ".".join(path + [key]) if path else key
            body = []
            i += 1
            # Skip the opening ''' line if the key was on its own line.
            if block_open.match(lines[i]) if i < n else False:
                i += 1
            closing_indent = ""
            while i < n:
                m_end = block_end.match(lines[i])
                if m_end:
                    closing_indent = m_end.group(1)
                    break
                body.append(lines[i])
                i += 1
            strip_len = len(closing_indent)
            stripped = [_strip_indent(ln, strip_len) for ln in body]
            result[full_key] = "\n".join(stripped)
            i += 1
            continue

        i += 1

    return result


def flatten_dict(obj):
    """Flatten a parsed dict into {dotted.path: scalar value}."""
    out = {}

    def walk(o, prefix=""):
        if isinstance(o, dict):
            for k, v in o.items():
                nk = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    walk(v, nk)
                else:
                    out[nk] = v
        else:
            out[prefix] = o

    walk(obj)
    return out


def load_part_json(directory: str, lang: str):
    """Load every Source/<lang>*.json file and return {path: value}."""
    merged = {}
    files = sorted(glob.glob(os.path.join(directory, f"{lang}*.json")))
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            data = hjson.load(f)
        merged.update(flatten_dict(data))
    return merged


def po_escape(value: str) -> str:
    """Escape a string for use inside a double-quoted .po string."""
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", "\\n")
    value = value.replace("\r", "\\r")
    value = value.replace("\t", "\\t")
    return value


def emit_po_string(prefix: str, value: str, out: list) -> None:
    """Emit `prefix "escaped"` handling embedded newlines via \\n."""
    out.append(f'{prefix} "{po_escape(value)}"')


def build_po(input_path: str, source_dir: str, comment_langs):
    """Assemble the full .po text."""
    with open(input_path, "r", encoding="utf-8") as f:
        input_text = f.read()

    # Parse the input hjson: active keys via hjson, multiline blocks manually.
    input_data = hjson.loads(input_text)
    input_flat = flatten_dict(input_data)
    for k, v in _parse_hjson_comment_blocks(input_text).items():
        input_flat.setdefault(k, v)

    english = load_part_json(source_dir, "en-US")

    # Reference translations for comments: {lang: {path: value}}.
    refs = {}
    for lang in comment_langs:
        if lang.lower() == "ru":
            refs["ru"] = load_part_json(source_dir, "ru-RU")
        elif lang.lower() == "pl":
            refs["pl"] = load_part_json(source_dir, "pl-PL")
        elif lang.lower() == "uk":
            uk_path = os.path.join(source_dir, "uk-UA.hjson")
            if os.path.exists(uk_path):
                with open(uk_path, "r", encoding="utf-8") as f:
                    uk_text = f.read()
                uk = flatten_dict(hjson.loads(uk_text))
                for k, v in _parse_hjson_comment_blocks(uk_text).items():
                    uk.setdefault(k, v)
                refs["uk"] = uk

    out: list[str] = []

    # Header entry.
    out.append('msgid ""')
    out.append('msgstr ""')
    out.append('"Content-Type: text/plain; charset=UTF-8\\n"')
    out.append('"Content-Transfer-Encoding: 8bit\\n"')
    if input_flat:
        out.append(f'"Language: be-BY\\n"')
    out.append("")

    # Entries in the order keys appear in the flattened input dict.
    for path, be_value in input_flat.items():
        msgid = english.get(path, be_value)
        # A string is only a real translation if it differs from the English
        # msgid; untranslated strings keep a blank msgstr (the gettext marker
        # for "not yet translated").
        msgstr = "" if str(be_value) == str(msgid) else str(be_value)

        out.append(f"#. {path}")

        for lang in refs:
            val = refs[lang].get(path)
            if val is not None and str(val) != "":
                out.append(f'# [{lang}] "{po_escape(str(val))}"')

        emit_po_string("msgctxt", path, out)
        emit_po_string("msgid", str(msgid), out)
        emit_po_string("msgstr", str(msgstr), out)
        out.append("")

    return "\n".join(out)


def count_msgids(text: str) -> int:
    return len(re.findall(r"(?m)^msgid ", text))


def main():
    parser = argparse.ArgumentParser(
        description="Convert a VanillaTranslations .hjson template into a .po file."
    )
    parser.add_argument("--input", default=None,
                        help="Input .hjson file (default: be-BY.hjson in VanillaTranslations).")
    parser.add_argument("--output", default=None,
                        help="Output .po file path (default: Localization/be-BY.po).")
    parser.add_argument("--source-dir", default=None,
                        help="Folder containing the Source/en-US*, ru-RU*, pl-PL* and uk-UA.hjson files.")
    parser.add_argument("--langs", default="pl,ru,uk",
                        help="Comma-separated languages to add as [xx] reference comments.")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_dir = args.source_dir or os.path.join(root, "Source")
    local_dir = os.path.join(root, "Localization")

    input_path = args.input or os.path.join(root, "Localization", "VanillaTranslations", "be-BY.hjson")
    output = args.output or os.path.join(local_dir, "be-BY.po")

    comment_langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]

    text = build_po(input_path, source_dir, comment_langs)

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)

    n = count_msgids(text)
    if n < 2:
        sys.stderr.write(f"WARNING: only {n} msgids produced; check the input.\n")
    print(f"Wrote {output} ({n - 1} entries).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
