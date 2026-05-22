#!/usr/bin/env python3
"""
check_colors.py

Prompts for a colors.scss file and a target directory.
Finds mismatches:
  1. color.$name used in target files but not declared in colors.scss
  2. $name declared in colors.scss but never used in target directory
  3. Raw hex or rgba values hardcoded in target files (should be variables)
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from io import StringIO


# ── Helpers ──────────────────────────────────────────────────────────

def prompt_file(label):
    while True:
        path = input(f"\n{label}: ").strip().strip('"').strip("'")
        if os.path.isfile(path):
            return Path(path)
        print(f"  ✗ File not found: {path}")


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


# ── Parsers ──────────────────────────────────────────────────────────

def parse_declared_vars(colors_text):
    """
    Extract every $name declared in colors.scss Section 2.
    Matches lines like:  $some-name:   var(--something);
                     or  $some-name:   $other-name;
    Returns a set of names (without the $).
    """
    pattern = re.compile(r"^\s*\$([a-zA-Z0-9_-]+)\s*:", re.MULTILINE)
    return set(pattern.findall(colors_text))


def parse_used_color_vars(text):
    """
    Find all color variable references in a scss file.
    Catches two patterns:
      color.$some-name   — namespaced token via tokens.scss @forward
      $some-name         — direct $name usage
    Returns a dict: { var_name: set of patterns found }
    """
    namespaced = re.compile(r"color\.\$([a-zA-Z0-9_-]+)")
    direct     = re.compile(r"(?<![a-zA-Z0-9_-])\$([a-zA-Z0-9_-]+)")

    found = {}
    for match in namespaced.finditer(text):
        name = match.group(1)
        found.setdefault(name, set()).add(f"color.${name}")
    for match in direct.finditer(text):
        name = match.group(1)
        found.setdefault(name, set()).add(f"${name}")
    return found


def parse_raw_hex(text):
    """
    Find hardcoded hex color values.
    Returns list of (hex_string, line_number).
    """
    pattern = re.compile(r"#([0-9a-fA-F]{3,8})\b")
    results = []
    for i, line in enumerate(text.splitlines(), 1):
        # skip comment lines
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for match in pattern.finditer(line):
            results.append((match.group(0), i, line.strip()))
    return results


def parse_raw_rgba(text):
    """
    Find hardcoded rgba() or rgb() values.
    Returns list of (rgba_string, line_number).
    """
    pattern = re.compile(r"rgba?\([^)]+\)")
    results = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for match in pattern.finditer(line):
            results.append((match.group(0), i, line.strip()))
    return results


# ── Output ───────────────────────────────────────────────────────────

class Tee:
    """Writes to both terminal and a string buffer simultaneously."""
    def __init__(self):
        self.buf = StringIO()

    def write(self, text):
        print(text, end="")
        self.buf.write(text)

    def line(self, text=""):
        self.write(text + "\n")

    def section(self, title):
        self.line()
        self.line("═" * 60)
        self.line(f"  {title}")
        self.line("═" * 60)

    def get(self):
        return self.buf.getvalue()


def make_output_path(target_dir):
    """
    Build scripts/data/audits/color-match/ relative to the target dir's
    project root (two levels up from the target dir), then timestamp the file.
    Creates the folder if it doesn't exist.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"color-match_{timestamp}.txt"

    # resolve relative to cwd so it always lands in the project root
    audit_dir = Path.cwd() / "scripts" / "data" / "audits" / "color-match"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / filename


# ── Run ───────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════╗")
    out.line("║         colors.scss mismatch checker     ║")
    out.line("╚══════════════════════════════════════════╝")

    colors_path = prompt_file("Path to colors.scss")
    target_dir  = prompt_dir("Path to directory to search (e.g. locs/)")

    colors_text   = read(colors_path)
    declared_vars = parse_declared_vars(colors_text)
    target_files  = scss_files(target_dir)

    if not target_files:
        out.line(f"\n  No .scss files found in {target_dir}")
        sys.exit(0)

    out.line()
    out.line(f"  Colors file : {colors_path}")
    out.line(f"  Target dir  : {target_dir}")
    out.line(f"  Files found : {len(target_files)}")
    out.line(f"  Vars declared in colors.scss: {len(declared_vars)}")

    # ── Collect data ─────────────────────────────────────────────────

    all_used       = {}
    undefined_uses = {}
    raw_hexes      = {}
    raw_rgbas      = {}

    for f in target_files:
        text = read(f)
        used = parse_used_color_vars(text)  # dict: name -> set of patterns

        for var, patterns in used.items():
            all_used.setdefault(var, set()).add(f.name)
            if var not in declared_vars:
                undefined_uses.setdefault(var, set()).update(
                    f"{f.name} ({p})" for p in sorted(patterns)
                )

        hexes = parse_raw_hex(text)
        if hexes:
            raw_hexes[f.name] = hexes

        rgbas = parse_raw_rgba(text)
        if rgbas:
            raw_rgbas[f.name] = rgbas

    never_used = declared_vars - set(all_used.keys())

    # ── Report 1: undefined vars ──────────────────────────────────────

    out.section("1 — USED BUT NOT DECLARED IN colors.scss")
    if undefined_uses:
        for var, refs in sorted(undefined_uses.items()):
            out.line(f"\n  ${var}")
            for ref in sorted(refs):
                out.line(f"    → {ref}")
    else:
        out.line("\n  ✓ No undefined color variables found.")

    # ── Report 2: declared but never used ────────────────────────────

    out.section("2 — DECLARED IN colors.scss BUT NEVER USED IN TARGET DIR")
    if never_used:
        for var in sorted(never_used):
            out.line(f"  ${var}")
    else:
        out.line("\n  ✓ All declared variables are used.")

    # ── Report 3: raw hex values ──────────────────────────────────────

    out.section("3 — HARDCODED HEX VALUES (should be color variables)")
    if raw_hexes:
        for filename, hits in sorted(raw_hexes.items()):
            out.line(f"\n  {filename}")
            for hex_val, line_num, line_text in hits:
                out.line(f"    line {line_num:>4}  {hex_val:<12}  {line_text[:72]}")
    else:
        out.line("\n  ✓ No hardcoded hex values found.")

    # ── Report 4: raw rgba values ─────────────────────────────────────

    out.section("4 — HARDCODED rgba() VALUES (should be color variables)")
    if raw_rgbas:
        for filename, hits in sorted(raw_rgbas.items()):
            out.line(f"\n  {filename}")
            for rgba_val, line_num, line_text in hits:
                out.line(f"    line {line_num:>4}  {rgba_val[:30]:<32}  {line_text[:60]}")
    else:
        out.line("\n  ✓ No hardcoded rgba values found.")

    # ── Summary ───────────────────────────────────────────────────────

    out.section("SUMMARY")
    out.line(f"  Undefined vars      : {len(undefined_uses)}")
    out.line(f"  Never-used vars     : {len(never_used)}")
    out.line(f"  Files with raw hex  : {len(raw_hexes)}")
    out.line(f"  Files with raw rgba : {len(raw_rgbas)}")
    out.line()

    # ── Write to file ─────────────────────────────────────────────────

    output_path = make_output_path(target_dir)
    output_path.write_text(out.get(), encoding="utf-8")
    print(f"\n  ✓ Report saved → {output_path}\n")


if __name__ == "__main__":
    run()