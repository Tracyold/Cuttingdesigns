#!/usr/bin/env python3
"""
check_duplicates.py

Scans every .scss file in a given directory, one file at a time.

For each file:
  1. Identifies variable declaration lines — lines starting with $name:
  2. Extracts the raw value portion after the ':' — strips all $variable
     references, leaving only raw groups (numbers, units, rgba, hex etc.)
  3. A raw group is everything connected by:
       - single space
       - single comma
       - comma + single space
       - parentheses with commas/spaces (rgba etc.)
     The group ends when a $name appears after a comma+space — that signals
     a new shadow layer starting with a variable, not part of the raw group.
  4. Compares extracted raw groups across all lines in the file.
     If the same raw group appears as the complete raw portion of two
     different variable declarations — that is a true duplicate.
     The second (and beyond) should reference the first by $name instead.

Outputs report to scripts/data/audits/duplicates/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from io import StringIO


# ── Helpers ──────────────────────────────────────────────────────────

def prompt_dir(label):
    while True:
        path = input(f"\n{label}: ").strip().strip('"').strip("'")
        if os.path.isdir(path):
            return Path(path)
        print(f"  ✗ Directory not found: {path}")


def read(path):
    return path.read_text(encoding="utf-8")


def scss_files(directory):
    return sorted(directory.rglob("*.scss"))


def make_output_path():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"duplicates_{timestamp}.txt"
    audit_dir = Path.cwd() / "scripts" / "data" / "audits" / "duplicates"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / filename


# ── Value extraction ─────────────────────────────────────────────────

# Matches a variable declaration line: $name: value;
DECL_LINE = re.compile(r"^\s*(\$[a-zA-Z0-9_-]+)\s*:(.*?)(?:;|$)", re.DOTALL)

# Matches a $variable reference
VAR_REF = re.compile(r"\$[a-zA-Z0-9_-]+")

# Matches a CSS comment at end of line
COMMENT = re.compile(r"//.*$")


def extract_raw_groups(value_str):
    """
    Given the right-hand side of a variable declaration (after the ':'),
    extract the raw value groups — the parts that are NOT $variable references.

    Rules:
    - Strip $variable references
    - A raw group is everything connected by single space, single comma,
      comma+space, or parentheses with commas/spaces
    - Stop consuming when a $name appears after a ', ' (comma+space) —
      that signals a new shadow layer starting with a variable

    Returns a list of raw group strings, stripped and non-empty.
    """
    # Remove trailing comment
    value_str = COMMENT.sub('', value_str).strip()
    # Remove trailing semicolon
    value_str = value_str.rstrip(';').strip()

    if not value_str:
        return []

    # Split on ', $' — comma+space followed by a $ signals a new layer
    # starting with a variable reference. We only care about segments
    # that contain raw values.
    # Split the value on the pattern ', $varname' to isolate raw segments
    segments = re.split(r',\s*(?=\$)', value_str)

    raw_groups = []

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Remove all $variable references from this segment
        cleaned = VAR_REF.sub('', segment).strip()

        # Clean up leftover punctuation artifacts from removing vars
        # e.g. ", " left after removing "$shadow-darc, "
        cleaned = re.sub(r'^[,\s]+', '', cleaned)
        cleaned = re.sub(r'[,\s]+$', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if cleaned:
            raw_groups.append(cleaned)

    return raw_groups


# ── Core checker ─────────────────────────────────────────────────────

def check_file(text):
    """
    Scan one file for variable declarations that have identical raw value groups.

    Returns dict:
    {
        raw_group_string: {
            "declarations": [ (var_name, line_num, full_line), ... ]
        }
    }
    Only includes groups that appear in more than one declaration.
    """
    lines = text.splitlines()

    # raw_group → list of (var_name, line_num, line_text)
    group_map = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip blank and comment lines
        if not stripped or stripped.startswith('//') or stripped.startswith('*'):
            i += 1
            continue

        m = DECL_LINE.match(line)
        if not m:
            i += 1
            continue

        var_name  = m.group(1)
        value_str = m.group(2)

        # Handle multi-line values (line ends without semicolon)
        while not re.search(r';', lines[i]) and i + 1 < len(lines):
            i += 1
            next_stripped = lines[i].strip()
            if next_stripped.startswith('//') or next_stripped.startswith('$'):
                break
            value_str += ' ' + lines[i].strip()

        raw_groups = extract_raw_groups(value_str)

        for group in raw_groups:
            # Normalize whitespace for comparison
            normalized = re.sub(r'\s+', ' ', group).strip().lower()
            if not normalized:
                continue

            group_map.setdefault(normalized, {
                "display": group,
                "declarations": []
            })
            group_map[normalized]["declarations"].append(
                (var_name, i + 1, stripped)
            )

        i += 1

    # Only return groups that appear in more than one declaration
    return {
        k: v for k, v in group_map.items()
        if len(v["declarations"]) > 1
    }


# ── Output ───────────────────────────────────────────────────────────

class Tee:
    def __init__(self):
        self.buf = StringIO()

    def write(self, text):
        print(text, end="")
        self.buf.write(text)

    def line(self, text=""):
        self.write(text + "\n")

    def section(self, title):
        self.line()
        self.line("═" * 70)
        self.line(f"  {title}")
        self.line("═" * 70)

    def get(self):
        return self.buf.getvalue()


# ── Run ───────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════════════╗")
    out.line("║         duplicate raw value group checker        ║")
    out.line("╚══════════════════════════════════════════════════╝")
    out.line()
    out.line("  Rule: the raw value portion of a variable declaration")
    out.line("  should be unique within the file. If two variables")
    out.line("  have the same raw value group, the second should")
    out.line("  reference the first by $name instead.")
    out.line()
    out.line("  A raw group is everything connected by single space,")
    out.line("  comma, comma+space, or parentheses. $variable references")
    out.line("  are stripped before comparison.")

    target_dir = prompt_dir("\nDirectory to scan (e.g. src/styles/decs)")

    files = scss_files(target_dir)

    if not files:
        out.line(f"\n  No .scss files found in {target_dir}")
        sys.exit(0)

    out.line()
    out.line(f"  Scanning {len(files)} files in {target_dir}...")
    out.line()

    total_flags = 0
    files_clean = 0
    files_dirty = 0
    all_results = {}

    for f in files:
        text    = read(f)
        flagged = check_file(text)

        if flagged:
            files_dirty += 1
            total_flags += len(flagged)
            all_results[f.name] = flagged
            print(f"  ✗  {f.name}  ({len(flagged)} duplicate group{'s' if len(flagged) > 1 else ''})")
        else:
            files_clean += 1
            print(f"  ✓  {f.name}")

    # ── Detail ────────────────────────────────────────────────────────

    out.section("DUPLICATE RAW VALUE GROUPS — DETAIL")

    if not all_results:
        out.line("\n  ✓ No duplicate raw value groups found.")
    else:
        for filename, flagged in sorted(all_results.items()):
            out.line()
            out.line(f"  ── {filename} {'─' * max(1, 50 - len(filename))}")
            for group_key, data in sorted(flagged.items()):
                out.line()
                out.line(f"    RAW GROUP  {data['display']}")
                out.line(f"    used in {len(data['declarations'])} declarations — second+ should reference first by $name")
                for var_name, line_num, line_text in data["declarations"]:
                    out.line(f"      {var_name:<40} line {line_num:>4}  {line_text[:50]}")

    # ── Summary ───────────────────────────────────────────────────────

    out.section("SUMMARY")
    out.line(f"  Files scanned      : {len(files)}")
    out.line(f"  Files clean        : {files_clean}")
    out.line(f"  Files with dups    : {files_dirty}")
    out.line(f"  Total flagged      : {total_flags} unique raw groups")
    out.line()

    # ── Save ──────────────────────────────────────────────────────────

    output_path = make_output_path()
    output_path.write_text(out.get(), encoding="utf-8")
    print(f"\n  ✓ Report saved → {output_path}\n")


if __name__ == "__main__":
    run()