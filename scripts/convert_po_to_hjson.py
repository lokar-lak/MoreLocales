#!/usr/bin/env python3
"""Convert a translated .po file back into a VanillaTranslations .hjson file.

The existing be-BY.hjson template is patched in place: section nesting, ordering
and formatting are preserved, and only leaf values are updated. For every .po
entry (keyed by its msgctxt dotted path):

  * msgstr is non-empty  -> a real translation, so its value is written.
  * msgstr is empty      -> untranslated, so a blank value ("") is written.

Usage:
    python3 scripts/convert_po_to_hjson.py --force

By default the result is written to be-BY.hjson.new; pass --force to overwrite
the real be-BY.hjson (a .backup is saved first). Use --dry-run to preview how
many strings would be translated vs kept blank.
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
        "The `hjson` package is required to re-validate the generated file."
        " Install it with:  pip install hjson\n"
    )
    sys.exit(1)

try:
    import polib
except ImportError:
    polib = None


def load_po_entries(po_path: str):
    """Return a list of (msgctxt, msgstr) for every entry in the .po file."""
    if polib is not None:
        po = polib.pofile(po_path)
        return [(e.msgctxt or "", e.msgstr or "") for e in po if e.msgctxt]
    return _parse_po_manual(po_path)


def _parse_po_manual(po_path: str):
    """Fallback gettext parser used when polib is unavailable."""
    entries = []
    ctx = mid = mstr = ""
    section = None
    for raw in open(po_path, "r", encoding="utf-8"):
        line = raw.rstrip("\n")
        if line == "":
            if section == "msgstr" and ctx:
                entries.append((ctx, mstr))
            ctx = mid = mstr = ""
            section = None
            continue
        m = re.match(r'^msgctxt "((?:[^"\\]|\\.)*)"$', line)
        if m:
            ctx = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            section = "msgctxt"
            continue
        m = re.match(r'^msgstr "((?:[^"\\]|\\.)*)"$', line)
        if m:
            mstr = _unescape(m.group(1))
            section = "msgstr"
            continue
    if ctx:
        entries.append((ctx, mstr))
    return entries


def _unescape(s: str) -> str:
    s = s.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    s = s.replace('\\"', '"').replace("\\\\", "\\")
    return s


# --- Hjson value formatting (mirrors convert_source_to_hjson.py) ---

_FORBIDDEN_FIRST = frozenset('{["\'#')


def _needs_quotes(value: str) -> bool:
    if value != value.strip():
        return True
    if value == "":
        return True
    if value[0] in _FORBIDDEN_FIRST:
        return True
    return False


def _quote_value(value: str) -> str:
    if _needs_quotes(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _emit_value(indent: str, key_text: str, value: str):
    """Emit the Hjson text for a single leaf given its indent and key token."""
    if value == "":
        return [f"{indent}{key_text}: \"\""]
    if "\n" in value:
        return _emit_multiline(indent, key_text, value)
    return [f"{indent}{key_text}: {_quote_value(value)}"]


def _emit_multiline(indent: str, key_text: str, value: str):
    out = [f"{indent}/* {key_text}:"]
    out.append(f"{indent}\t'''")
    for line in value.split("\n"):
        out.append(f"{indent}\t{line}")
    out.append(f"{indent}\t''' */")
    return out


# --- In-place patcher ---

_SECTION_OPEN = re.compile(
    r'^(\s*)((?:"[^"]*")|([A-Za-z0-9_.\-]+)):\s*\{\s*$'
)
_LEAF = re.compile(r'^(\s*)((?:"[^"]*")|([A-Za-z0-9_.\-]+)):')
_BLOCK_START = re.compile(r'^(\s*)/\*\s*((?:"[^"]*")|([A-Za-z0-9_.\-]+)):')
_BLOCK_END = re.compile(r"^\s*'''\s*\*/\s*$")
_KEY_TOKEN = re.compile(r'^((?:"[^"]*")|([A-Za-z0-9_.\-]+))$')


def _key_text(match) -> str:
    return match.group(2) or match.group(3)


def _bare_key(key_text: str) -> str:
    if len(key_text) >= 2 and key_text[0] == '"' and key_text[-1] == '"':
        return key_text[1:-1]
    return key_text


def _full_key(path, key_text) -> str:
    key = _bare_key(key_text)
    return ".".join(path + [key]) if path else key


def patch_hjson(text: str, value_map: dict):
    """Return the be-BY.hjson text with leaf values updated from value_map."""
    lines = text.split("\n")
    n = len(lines)
    path: list[str] = []
    depth = 0
    out: list[str] = []
    changed = 0
    i = 0

    while i < n:
        line = lines[i]

        m = _SECTION_OPEN.match(line)
        if m:
            out.append(line)
            path.append(_key_text(m))
            depth += 1
            i += 1
            continue

        if depth > 0 and line.strip() == "}":
            out.append(line)
            depth -= 1
            if path:
                path.pop()
            i += 1
            continue

        m = _BLOCK_START.match(line)
        if m:
            indent = m.group(1)
            key = _full_key(path, _key_text(m))
            # Collect the whole commented multiline block.
            start = i
            i += 1
            while i < n and not _BLOCK_END.match(lines[i]):
                i += 1
            i += 1  # skip closing ''' */
            if key in value_map:
                out.extend(_emit_value(indent, _key_text(m), value_map[key]))
                changed += 1
            else:
                out.extend(lines[start:i])
            continue

        m = _LEAF.match(line)
        if m:
            key = _full_key(path, _key_text(m))
            if key in value_map:
                indent = m.group(1)
                out.extend(_emit_value(indent, _key_text(m), value_map[key]))
                changed += 1
            else:
                out.append(line)
            i += 1
            continue

        out.append(line)
        i += 1

    return "\n".join(out), changed


def _is_section_open(line: str) -> bool:
    return bool(_SECTION_OPEN.match(line))


def main():
    parser = argparse.ArgumentParser(
        description="Convert a translated .po file back into a VanillaTranslations .hjson."
    )
    parser.add_argument("--input", default=None,
                        help="Input .po file (default: Localization/be-BY.po).")
    parser.add_argument("--base", default=None,
                        help="Existing .hjson template to patch (default: be-BY.hjson in VanillaTranslations).")
    parser.add_argument("--output", default=None,
                        help="Output .hjson path (default: <base>.new).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite the real be-BY.hjson (saves a .hjson.backup first).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report how many strings would change, without writing.")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_dir = os.path.join(root, "Localization")
    vt_dir = os.path.join(local_dir, "VanillaTranslations")

    po_path = args.input or os.path.join(local_dir, "be-BY.po")
    base_path = args.base or os.path.join(vt_dir, "be-BY.hjson")

    if not os.path.exists(base_path):
        sys.stderr.write(f"Base template not found: {base_path}\n")
        sys.exit(1)

    entries = load_po_entries(po_path)
    value_map = {}
    for ctx, mstr in entries:
        # Blank msgstr marks an untranslated string -> blank value.
        value_map[ctx] = mstr

    with open(base_path, "r", encoding="utf-8") as f:
        text = f.read()

    new_text, changed = patch_hjson(text, value_map)

    # Validate the patched result re-parses as Hjson.
    try:
        hjson.loads(new_text)
    except Exception as e:
        sys.stderr.write(f"ERROR: patched output did not re-parse as Hjson: {e}\n")
        sys.exit(1)

    translated = sum(1 for v in value_map.values() if v != "")
    blank = len(value_map) - translated
    print(f"Entries from .po: {len(value_map)} (translated: {translated}, blank: {blank})")
    print(f"Patched leaf values: {changed}")

    if args.dry_run:
        print("DRY RUN - no file written.")
        return 0

    out_path = args.output
    if out_path is None:
        if args.force:
            backup = base_path + ".backup"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(base_path, backup)
            out_path = base_path
        else:
            out_path = base_path + ".new"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"Wrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
