#!/usr/bin/env python3
"""
check_colors.py

Prompts for:
  1. colors.scss path   — source of truth for every color variable
  2. tokens.scss path   — read to discover the namespace prefix
                          e.g.  @forward 'decs/colors' as color-*
                                → prefix = "color"
  3. target directory   — every .scss file here is scanned

How the matching works
──────────────────────
colors.scss declares variables WITHOUT the prefix:
    $card-background:   $surface;
    $button-text:       $text;

locs files reference those variables WITH the prefix prepended after the $:
    background-color:   $color-card-background;
    color:              $color-button-text;

tokens.scss supplies the prefix ("color" in the examples above).
The script builds the search pattern  $color-([a-zA-Z0-9_-]+)  and strips
"color-" from every match to recover the bare name, which is then checked
against what colors.scss actually declares.

Reports produced
────────────────
  1  $color-name used in target files but NOT declared in colors.scss
  2  $name declared in colors.scss but NEVER referenced in target directory
  3  Hardcoded hex values on a color CSS property
  4  Hardcoded rgba/rgb values on a color CSS property
  5  Bare $variable references on color properties (not using the prefix)
  6  Color-variable coverage per file
  7  Files with no color properties at all
  8  Summary
"""

import re
import sys
from pathlib import Path
from datetime import datetime
from io import StringIO


# ─────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────

def prompt_file(label: str) -> Path:
    while True:
        raw = input(f"\n{label}: ").strip().strip('"').strip("'")
        p   = Path(raw)
        if p.is_file():
            return p
        print(f"  ✗  File not found: {raw}")


def prompt_dir(label: str) -> Path:
    while True:
        raw = input(f"\n{label}: ").strip().strip('"').strip("'")
        p   = Path(raw)
        if p.is_dir():
            return p
        print(f"  ✗  Directory not found: {raw}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def scss_files(directory: Path):
    return sorted(directory.rglob("*.scss"))


# ─────────────────────────────────────────────────────────────
# Step 1 — read tokens.scss, find the color prefix
# ─────────────────────────────────────────────────────────────

def parse_color_prefix(tokens_text: str, colors_path: Path) -> str | None:
    """
    Locate the @forward line for colors.scss and return the prefix.

        @forward 'decs/colors' as color-*;
                                   ^^^^^  ← returned ("color")

    Works with any relative path style and with or without .scss extension.
    Returns None if no matching line is found.
    """
    stem    = colors_path.stem          # e.g. "colors"
    pattern = re.compile(
        r"@forward\s+['\"][^'\"]*"
        + re.escape(stem)
        + r"(?:\.scss)?['\"]\s+as\s+([a-zA-Z0-9_-]+)-\*",
        re.IGNORECASE,
    )
    m = pattern.search(tokens_text)
    return m.group(1) if m else None


# ─────────────────────────────────────────────────────────────
# Step 2 — read colors.scss, collect every declared $name
# ─────────────────────────────────────────────────────────────

def parse_declared_vars(colors_text: str) -> set:
    """
    Return every SCSS variable name declared in colors.scss (without the $).

    Matches lines like:
        $card-background:   $surface;
        $button-text:       var(--text);
        $surface:           var(--surface);

    CSS custom properties (--name: value) are skipped — they are not what
    locs files reference via the prefix.
    """
    return set(re.findall(
        r"^\s*\$([a-zA-Z0-9_-]+)\s*:",
        colors_text,
        re.MULTILINE,
    ))


# ─────────────────────────────────────────────────────────────
# CSS properties whose values should be color variables
# ─────────────────────────────────────────────────────────────

_COLOR_PROP_RE = re.compile(
    r"^\s*("
    r"color"
    r"|background(?:-color)?"
    r"|border(?:-(?:top|right|bottom|left))?(?:-color)?"
    r"|outline(?:-color)?"
    r"|box-shadow"
    r"|text-shadow"
    r"|fill"
    r"|stroke(?:-color)?"
    r"|caret-color"
    r"|column-rule(?:-color)?"
    r"|text-decoration-color"
    r"|accent-color"
    r"|stop-color"
    r"|flood-color"
    r"|lighting-color"
    r")\s*:",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────
# Step 3 — scan a single target file
# ─────────────────────────────────────────────────────────────

def scan_file(text: str, prefix: str) -> dict:
    """
    Scan one .scss file for color-property lines only.

    Returns:
        {
          "prefixed": {
              varname_without_prefix: [
                  {"full_ref": "$color-card-background",
                   "line_num": 12,
                   "before": "...", "match": "...", "after": "..."},
                  ...
              ]
          },
          "bare":   [(ref, line_num, line_text), ...],   # $other-name, not using prefix
          "hex":    [(value, line_num, line_text), ...],
          "rgba":   [(value, line_num, line_text), ...],
          "has_color_props": bool,
        }
    """
    # Matches exactly  $color-anything  (prefix supplied at call time)
    prefixed_re = re.compile(r"\$" + re.escape(prefix) + r"-([a-zA-Z0-9_-]+)")
    # Any other bare $variable on a color property (does NOT start with prefix-)
    bare_re     = re.compile(r"(?<!\w)\$([a-zA-Z0-9_-]+)")
    hex_re      = re.compile(r"#([0-9a-fA-F]{3,8})\b")
    rgba_re     = re.compile(r"rgba?\([^)]+\)")

    lines = text.splitlines()

    prefixed        = {}
    bare_hits       = []
    hex_hits        = []
    rgba_hits       = []
    has_color_props = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue

        on_color_prop = bool(_COLOR_PROP_RE.match(line))
        if not on_color_prop:
            continue

        has_color_props = True
        before = lines[i - 1].strip() if i > 0              else ""
        after  = lines[i + 1].strip() if i < len(lines) - 1 else ""
        ctx    = {"line_num": i + 1, "before": before, "match": stripped, "after": after}

        # — prefixed color variables ($color-...) ————————————
        prefixed_found = set()
        for m in prefixed_re.finditer(line):
            varname  = m.group(1)                         # e.g. "card-background"
            full_ref = f"${prefix}-{varname}"             # e.g. "$color-card-background"
            prefixed_found.add(m.group(0))                # track to exclude from bare check
            prefixed.setdefault(varname, []).append({
                "full_ref": full_ref, **ctx
            })

        # — bare $variables not using the prefix —————————————
        for m in bare_re.finditer(line):
            ref = m.group(0)
            # skip any match that was already captured as a prefixed ref
            if ref in prefixed_found:
                continue
            # skip matches that are the prefix itself at the start (shouldn't happen but safe)
            bare_hits.append((ref, i + 1, stripped))

        # — raw hex ——————————————————————————————————————————
        for m in hex_re.finditer(line):
            hex_hits.append((m.group(0), i + 1, stripped))

        # — raw rgba/rgb ——————————————————————————————————————
        for m in rgba_re.finditer(line):
            rgba_hits.append((m.group(0), i + 1, stripped))

    return {
        "prefixed":        prefixed,
        "bare":            bare_hits,
        "hex":             hex_hits,
        "rgba":            rgba_hits,
        "has_color_props": has_color_props,
    }


# ─────────────────────────────────────────────────────────────
# Output helper — writes to terminal AND a string buffer
# ─────────────────────────────────────────────────────────────

class Tee:
    def __init__(self):
        self.buf = StringIO()

    def write(self, text: str):
        print(text, end="")
        self.buf.write(text)

    def line(self, text: str = ""):
        self.write(text + "\n")

    def section(self, title: str):
        self.line()
        self.line("═" * 64)
        self.line(f"  {title}")
        self.line("═" * 64)

    def get(self) -> str:
        return self.buf.getvalue()


def make_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir   = Path.cwd() / "scripts" / "data" / "audits" / "color-match"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"color-match_{timestamp}.txt"


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════════╗")
    out.line("║       colors.scss mismatch checker           ║")
    out.line("╚══════════════════════════════════════════════╝")

    colors_path  = prompt_file("1. Path to colors.scss      (e.g. src/styles/decs/colors.scss)")
    tokens_path  = prompt_file("2. Path to tokens.scss      (e.g. src/styles/tokens.scss)")
    target_dir   = prompt_dir ("3. Directory to scan        (e.g. src/locs)")

    colors_text   = read(colors_path)
    tokens_text   = read(tokens_path)
    declared_vars = parse_declared_vars(colors_text)

    # ── Discover prefix from tokens.scss ─────────────────────
    prefix = parse_color_prefix(tokens_text, colors_path)
    if prefix is None:
        out.line()
        out.line(f"  ✗  Could not find a @forward line for '{colors_path.name}' in tokens.scss.")
        out.line(f"     Expected something like:  @forward '.../{colors_path.stem}' as PREFIX-*;")
        sys.exit(1)

    target_files = scss_files(target_dir)
    if not target_files:
        out.line(f"\n  No .scss files found in {target_dir}")
        sys.exit(0)

    out.line()
    out.line(f"  Colors file      : {colors_path}")
    out.line(f"  Tokens file      : {tokens_path}")
    out.line(f"  Color prefix     : ${prefix}-")
    out.line(f"  Target dir       : {target_dir}")
    out.line(f"  Files to scan    : {len(target_files)}")
    out.line(f"  Declared vars    : {len(declared_vars)}")

    # ── Scan every target file ────────────────────────────────
    # Aggregated across all files:
    all_used_varnames = {}    # varname → set of filenames where it appears
    undefined_refs    = {}    # varname → list of {file, full_ref, line_num, ...}
    all_bare          = {}    # filename → list of (ref, line_num, line)
    all_hex           = {}    # filename → list of (value, line_num, line)
    all_rgba          = {}    # filename → list of (value, line_num, line)
    no_color_files    = []    # filenames with no color properties at all
    coverage          = {}    # filename → {uses_prefixed, uses_bare, uses_raw}

    for f in target_files:
        result = scan_file(read(f), prefix)

        coverage[f.name] = {
            "uses_prefixed": bool(result["prefixed"]),
            "uses_bare":     bool(result["bare"]),
            "uses_raw":      bool(result["hex"]) or bool(result["rgba"]),
        }

        if not result["has_color_props"]:
            no_color_files.append(f.name)

        for varname, entries in result["prefixed"].items():
            all_used_varnames.setdefault(varname, set()).add(f.name)
            if varname not in declared_vars:
                for e in entries:
                    undefined_refs.setdefault(varname, []).append({"file": f.name, **e})

        if result["bare"]:
            all_bare[f.name] = result["bare"]
        if result["hex"]:
            all_hex[f.name]  = result["hex"]
        if result["rgba"]:
            all_rgba[f.name] = result["rgba"]

    never_used = declared_vars - set(all_used_varnames.keys())

    # ─────────────────────────────────────────────────────────
    # Report 1 — used but not declared
    # ─────────────────────────────────────────────────────────
    out.section(f"1 — USED AS ${prefix}-name BUT NOT DECLARED IN colors.scss")
    if undefined_refs:
        for varname, entries in sorted(undefined_refs.items()):
            out.line(f"\n  ${prefix}-{varname}")
            seen = set()
            for e in entries:
                key = (e["file"], e["line_num"])
                if key in seen:
                    continue
                seen.add(key)
                out.line(f"\n    ┌ {e['file']}  line {e['line_num']}  [{e['full_ref']}]")
                if e["before"]: out.line(f"    │  {e['before']}")
                out.line(        f"    ▶  {e['match']}")
                if e["after"]:  out.line(f"    │  {e['after']}")
                out.line(        f"    └{'─' * 52}")
    else:
        out.line(f"\n  ✓  No undefined ${prefix}-* references found.")

    # ─────────────────────────────────────────────────────────
    # Report 2 — declared but never used
    # ─────────────────────────────────────────────────────────
    out.section("2 — DECLARED IN colors.scss BUT NEVER REFERENCED IN TARGET DIR")
    if never_used:
        out.line()
        for varname in sorted(never_used):
            out.line(f"  ${varname}   (would be referenced as  ${prefix}-{varname})")
    else:
        out.line("\n  ✓  Every declared variable is referenced at least once.")

    # ─────────────────────────────────────────────────────────
    # Report 3 — hardcoded hex
    # ─────────────────────────────────────────────────────────
    out.section("3 — HARDCODED HEX VALUES ON COLOR PROPERTIES")
    if all_hex:
        for fname, hits in sorted(all_hex.items()):
            out.line(f"\n  {fname}")
            for value, ln, line in hits:
                out.line(f"    line {ln:>4}  {value:<14}  {line[:70]}")
    else:
        out.line("\n  ✓  No hardcoded hex values found.")

    # ─────────────────────────────────────────────────────────
    # Report 4 — hardcoded rgba/rgb
    # ─────────────────────────────────────────────────────────
    out.section("4 — HARDCODED rgba()/rgb() VALUES ON COLOR PROPERTIES")
    if all_rgba:
        for fname, hits in sorted(all_rgba.items()):
            out.line(f"\n  {fname}")
            for value, ln, line in hits:
                out.line(f"    line {ln:>4}  {value[:28]:<30}  {line[:60]}")
    else:
        out.line("\n  ✓  No hardcoded rgba/rgb values found.")

    # ─────────────────────────────────────────────────────────
    # Report 5 — bare $variable on color properties
    # ─────────────────────────────────────────────────────────
    out.section(f"5 — BARE $VARIABLE REFS ON COLOR PROPERTIES (not using ${prefix}-)")
    if all_bare:
        for fname, hits in sorted(all_bare.items()):
            out.line(f"\n  {fname}")
            for ref, ln, line in hits:
                out.line(f"    line {ln:>4}  {ref:<30}  {line[:60]}")
    else:
        out.line(f"\n  ✓  No bare variable references found — all colors use the ${prefix}- prefix.")

    # ─────────────────────────────────────────────────────────
    # Report 6 — coverage per file
    # ─────────────────────────────────────────────────────────
    out.section("6 — COLOR USAGE PER FILE")
    out.line()
    out.line(f"  {'FILE':<44} {'PREFIX':>8}  {'BARE':>6}  {'RAW':>5}")
    out.line(f"  {'─' * 44} {'─' * 8}  {'─' * 6}  {'─' * 5}")
    for fname in sorted(coverage):
        c = coverage[fname]
        out.line(
            f"  {fname:<44}"
            f"  {'✓' if c['uses_prefixed'] else '–':>8}"
            f"  {'✓' if c['uses_bare']     else '–':>6}"
            f"  {'✓' if c['uses_raw']      else '–':>5}"
        )

    # ─────────────────────────────────────────────────────────
    # Report 7 — files with no color properties
    # ─────────────────────────────────────────────────────────
    out.section("7 — FILES WITH NO COLOR PROPERTIES AT ALL")
    if no_color_files:
        out.line()
        for fname in sorted(no_color_files):
            out.line(f"  {fname}")
    else:
        out.line("\n  ✓  Every file has at least one color property.")

    # ─────────────────────────────────────────────────────────
    # Report 8 — summary
    # ─────────────────────────────────────────────────────────
    out.section("SUMMARY")
    out.line(f"  Prefix used          :  ${prefix}-")
    out.line(f"  Files scanned        :  {len(target_files)}")
    out.line(f"  Using ${prefix}-* vars  :  {sum(1 for c in coverage.values() if c['uses_prefixed'])}")
    out.line(f"  Using bare $vars     :  {sum(1 for c in coverage.values() if c['uses_bare'])}")
    out.line(f"  Using raw values     :  {sum(1 for c in coverage.values() if c['uses_raw'])}")
    out.line(f"  No color props       :  {len(no_color_files)}")
    out.line(f"  Undefined ${prefix}-*  :  {len(undefined_refs)}  (unique variable names)")
    out.line(f"  Never-used $names    :  {len(never_used)}")
    out.line(f"  Files with raw hex   :  {len(all_hex)}")
    out.line(f"  Files with raw rgba  :  {len(all_rgba)}")
    out.line()

    # ─────────────────────────────────────────────────────────
    # Save to file
    # ─────────────────────────────────────────────────────────
    output_path = make_output_path()
    output_path.write_text(out.get(), encoding="utf-8")
    print(f"\n  ✓  Report saved → {output_path}\n")


if __name__ == "__main__":
    run()