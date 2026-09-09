#!/usr/bin/env python3
"""Convert the vanilla English Source/*.json files into an .hjson file
formatted exactly like the files in Localization/VanillaTranslations/.

The generated file is a translation template for the requested culture: the
vanilla sections come from the Source files (English values), and the
tModLoader-only sections (tModLoader/Conditions/Config/Mods) are copied
verbatim from an existing culture file (--base) since those strings are not
part of the vanilla Source.

Usage:
    python3 scripts/convert_source_to_hjson.py --culture be-BY

Output is written to Localization/VanillaTranslations/<culture>.hjson
"""

from __future__ import annotations

import argparse
import os
import re
import sys

try:
    import hjson
except ImportError:
    sys.stderr.write(
        "The `hjson` package is required to parse the Source files."
        " Install it with:  pip install hjson\n"
    )
    sys.exit(1)


# Hjson "first character" characters that force a value to be double quoted,
# matching tModLoader's Hjson serializer output in be-BY.hjson.
_FORBIDDEN_FIRST = frozenset('{["\'#')


def needs_quotes(value: str) -> bool:
    """Decide whether a single-line string value must be emitted double-quoted.

    Matches the convention used in be-BY.hjson:
      * empty strings are quoted
      * strings with leading/trailing whitespace are quoted
      * strings whose first char is one of { [ " ' # are quoted
    """
    if value != value.strip():
        return True
    if value == "":
        return True
    if value[0] in _FORBIDDEN_FIRST:
        return True
    return False


def is_multiline(value: str) -> bool:
    return "\n" in value


def _quote_value(value: str) -> str:
    """Emit a single-line value as a string token (bare or double-quoted)."""
    if needs_quotes(value):
        # Escape double quotes and backslashes for a quoted Hjson string.
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def flatten_section(section_value):
    """Recursively transform a parsed JSON section into the emittable form.

    Returns a list of items. Each item is either:
      ("block", key, [children])   -> a nested section ending in { }
      ("entry", key, value_str)    -> a single value line
      ("multiline", key, text)     -> a commented '''...''' block
    """
    items = []
    if not isinstance(section_value, dict):
        return [("entry", "", _quote_value(str(section_value)))]

    for key, val in section_value.items():
        if isinstance(val, dict):
            children = flatten_section(val)
            # A section with exactly one sub-key that is itself a scalar gets
            # flattened to a dotted key (e.g. SpecialWorldName.TheConstant).
            if len(val) == 1:
                only_key, only_val = next(iter(val.items()))
                if not isinstance(only_val, dict):
                    flat_key = f"{key}.{only_key}"
                    items.extend(flatten_scalar(flat_key, only_val))
                    continue
            items.append(("block", key, children))
        else:
            items.extend(flatten_scalar(key, val))
    return items


def flatten_scalar(key: str, val):
    if isinstance(val, bool):
        return [("entry", key, "true" if val else "false")]
    if isinstance(val, (int, float)):
        return [("entry", key, str(val))]
    val = str(val)
    if is_multiline(val):
        return [("multiline", key, val)]
    return [("entry", key, _quote_value(val))]


_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _quote_key(key: str) -> str:
    """Quote a key if it is not a valid bare Hjson key.

    tModLoader's Hjson serializer keeps keys bare when they consist only of
    alphanumerics, underscore and dot; keys with spaces (e.g. NPC/changeable
    names like "Red Beard") must be double quoted.
    """
    if _BARE_KEY_RE.match(key):
        return key
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit(items, indent=0, out=None):
    """Serialize flattened items into Hjson text matching be-BY.hjson style."""
    tab = "\t" * indent
    for item in items:
        kind = item[0]
        if kind == "block":
            _, key, children = item
            out.append(f"{tab}{_quote_key(key)}: {{")
            emit(children, indent + 1, out)
            out.append(f"{tab}}}")
        elif kind == "entry":
            _, key, val = item
            out.append(f"{tab}{_quote_key(key)}: {val}")
        elif kind == "multiline":
            _, key, text = item
            out.append(f"{tab}/* {_quote_key(key)}:")
            out.append(f"{tab}\t'''")
            for line in text.split("\n"):
                out.append(f"{tab}\t{line}")
            out.append(f"{tab}\t''' */")
    return out


_BASE_SECTIONS = ("tModLoader", "Conditions", "Config", "Mods")


def split_base_sections(base_text: str):
    """Extract the non-vanilla sections (tModLoader/Config/Mods) verbatim.

    Returns an ordered dict of {section_name: [lines]}. Other top-level
    content is ignored.
    """
    sections = {}
    current = None
    buffer = []
    depth = 0

    for raw in base_text.splitlines(keepends=True):
        line = raw.rstrip("\n")

        if depth == 0:
            m = re.match(r"^([A-Za-z0-9_.]+):\s*\{\s*$", line)
            if m and m.group(1) in _BASE_SECTIONS:
                current = m.group(1)
                buffer = [line]
                depth = 1
                continue
            # Not inside a section we care about; skip this line but keep
            # scanning (brace counting only matters once inside a section).
            continue

        buffer.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            sections[current] = buffer
            current = None
            buffer = []

    if current is not None and buffer:
        sections[current] = buffer
    return sections


def parse_base(base_path: str):
    """Extract the non-vanilla sections to copy verbatim from the base file."""
    with open(base_path, "r", encoding="utf-8") as f:
        text = f.read()
    sections = split_base_sections(text)
    return None, sections


def load_source(source_dir: str):
    """Parse every Source/*.json into a single ordered dict of sections."""
    merged = {}
    files = sorted(f for f in os.listdir(source_dir) if f.endswith(".json"))
    if not files:
        sys.stderr.write(f"No .json files found in {source_dir!r}\n")
        sys.exit(1)
    for fname in files:
        path = os.path.join(source_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            data = hjson.load(f)
        for section, val in data.items():
            merged[section] = val
    return merged


def build_output(source_dir: str, base_path: str, include_base: bool):
    """Assemble the full Hjson text for the converted template."""
    merged = load_source(source_dir)

    header = None
    base_sections = {}
    if base_path and os.path.exists(base_path) and include_base:
        header, base_sections = parse_base(base_path)

    out: list[str] = []

    if header:
        out.extend(header)

    # Emit the non-vanilla sections copied from the base file.
    for name in _BASE_SECTIONS:
        if name in base_sections:
            out.extend(base_sections[name])

    # Emit the vanilla sections derived from the Source files.
    for section, val in merged.items():
        if isinstance(val, dict) and len(val) == 1:
            only_key, only_val = next(iter(val.items()))
            if not isinstance(only_val, dict):
                items = flatten_scalar(f"{section}.{only_key}", only_val)
                emit(items, 0, out)
                continue
        items = flatten_section(val)
        if len(items) == 1 and items[0][0] == "entry" and items[0][1] == "":
            # Single non-dict scalar at top level (rare).
            out.append(_quote_value(str(val)))
        else:
            emit([("block", section, items)], 0, out)

    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Convert vanilla Source/*.json into a VanillaTranslations .hjson template."
    )
    parser.add_argument("--culture", default="be-BY",
                        help="Target culture code used for the output filename (e.g. be-BY).")
    parser.add_argument("--source-dir", default=None,
                        help="Folder containing the Source/*.json files.")
    parser.add_argument("--output", default=None,
                        help="Explicit output file path (overrides --culture naming).")
    parser.add_argument("--base", default=None,
                        help="Reference .hjson from which to copy the tModLoader-only sections. "
                             "Defaults to the existing be-BY.hjson.")
    parser.add_argument("--no-base", action="store_true",
                        help="Do not copy the tModLoader-only sections from a base file.")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_dir = args.source_dir or os.path.join(root, "Source")
    local_dir = os.path.join(root, "Localization", "VanillaTranslations")

    base_path = args.base
    if base_path is None and not args.no_base:
        base_path = os.path.join(local_dir, "be-BY.hjson")

    output = args.output
    if output is None:
        output = os.path.join(local_dir, f"{args.culture}.hjson")

    text = build_output(source_dir, base_path, include_base=not args.no_base)

    out_dir = os.path.dirname(output)
    os.makedirs(out_dir, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)

    # Validate the produced file parses as Hjson.
    try:
        parsed = hjson.loads(text)
    except Exception as e:
        sys.stderr.write(f"WARNING: generated {output} did not re-parse cleanly: {e}\n")
    else:
        n = count_leaf_keys(parsed)
        print(f"Wrote {output} ({n} leaf entries).")

    return 0


def count_leaf_keys(obj):
    n = 0
    if isinstance(obj, dict):
        for v in obj.values():
            n += count_leaf_keys(v)
    if not isinstance(obj, dict) and not isinstance(obj, list):
        n = 1
    return n


if __name__ == "__main__":
    sys.exit(main())
