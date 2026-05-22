#!/usr/bin/env python3
"""
decs_audit.py

Prompts for:
  1. tokens.scss path    — the single @forward map for all decs and effs files
  2. decs/ directory     — where declared variable files live
  3. effs/ directory     — where declared motion/effect variable files live
  4. locs/ directory     — where CSS class files live that consume those variables

What it does
────────────────────────────────────────────────────────────────
tokens.scss is the map. Every @forward line declares:
  - which decs or effs file exists
  - what prefix locs files use to reference it

    @forward 'decs/colors'         as color-*;   → prefix "color",  file decs/colors.scss
    @forward 'decs/border-radius'  as radius-*;  → prefix "radius", file decs/border-radius.scss
    @forward 'effs/durations'      as dur-*;     → prefix "dur",    file effs/durations.scss

The script reads that map, reads every declared variable from every
forwarded file, then scans every locs file for $prefix-varname references
and cross-checks in both directions.

Reports produced
────────────────────────────────────────────────────────────────
  1  @forward lines in tokens.scss pointing to files that do not exist on disk
  2  Prefixes used in locs files that have no @forward entry in tokens.scss
  3  Per decs/effs file — variables declared but never used in any locs file
  4  Per decs/effs file — variables used in locs but not declared in that file
  5  Per locs file — every prefix it uses and how many distinct variables
  6  Summary
"""

import re
import sys
from pathlib import Path
from datetime import datetime
from io import StringIO
from collections import defaultdict


# ─────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────

def prompt_file(label: str) -> Path:
    while True:
        raw = input(f"\n{label}: ").strip().strip('"').strip("'")
        p   = Path(raw)
        if p.is_file():
            return p
        print(f"  x  File not found: {raw}")


def prompt_dir(label: str) -> Path:
    while True:
        raw = input(f"\n{label}: ").strip().strip('"').strip("'")
        p   = Path(raw)
        if p.is_dir():
            return p
        print(f"  x  Directory not found: {raw}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def scss_files(directory: Path):
    return sorted(directory.rglob("*.scss"))


# ─────────────────────────────────────────────────────────────
# tokens.scss — build the full prefix → file map
# ─────────────────────────────────────────────────────────────

def parse_tokens(tokens_text: str, tokens_path: Path, decs_dir: Path, effs_dir: Path) -> list:
    """
    Read every @forward line in tokens.scss and return a list of dicts:

        {
            "prefix":    "color",
            "raw_path":  "decs/colors",
            "file":      Path(...) or None if file not found on disk,
            "layer":     "decs" | "effs" | "unknown",
        }

    Tries to resolve the file path relative to the tokens.scss parent directory,
    then falls back to checking decs_dir and effs_dir directly.
    """
    pattern = re.compile(
        r"@forward\s+['\"]([^'\"]+)['\"]\s+as\s+([a-zA-Z0-9_-]+)-\*",
        re.IGNORECASE,
    )

    tokens_dir = tokens_path.parent
    entries    = []

    for m in pattern.finditer(tokens_text):
        raw_path = m.group(1)    # e.g. "decs/colors" or "../locs/button"
        prefix   = m.group(2)   # e.g. "color"

        # Determine layer from path string
        if "decs/" in raw_path or raw_path.startswith("decs"):
            layer = "decs"
        elif "effs/" in raw_path or raw_path.startswith("effs"):
            layer = "effs"
        else:
            layer = "unknown"

        # Try to resolve the actual file
        resolved = None
        candidates = [
            tokens_dir / (raw_path + ".scss"),
            tokens_dir / raw_path,
            Path(raw_path + ".scss"),
            Path(raw_path),
        ]
        for c in candidates:
            if c.is_file():
                resolved = c.resolve()
                break

        entries.append({
            "prefix":   prefix,
            "raw_path": raw_path,
            "file":     resolved,
            "layer":    layer,
        })

    return entries


# ─────────────────────────────────────────────────────────────
# decs / effs files — declared variable names
# ─────────────────────────────────────────────────────────────

def parse_declared_vars(file_text: str) -> set:
    """
    Return every SCSS variable name declared in a decs or effs file (no $).

    Matches:
        $card-background:   $surface;
        $button-hover:      150ms;
        $modal-enter:       cubic-bezier(...);

    Skips CSS custom properties (--name).
    Skips comment lines.
    """
    names = set()
    for line in file_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = re.match(r"^\$([a-zA-Z0-9_-]+)\s*:", stripped)
        if m:
            names.add(m.group(1))
    return names


# ─────────────────────────────────────────────────────────────
# locs files — variable references
# ─────────────────────────────────────────────────────────────

def parse_locs_refs(file_text: str, known_prefixes: set) -> dict:
    """
    Scan a locs file and collect every $prefix-varname reference where
    the prefix is in known_prefixes.

    Returns:
        {
            prefix: {
                varname: [ {line_num, line_text}, ... ]
            }
        }

    Only lines that are NOT pure comments are scanned.
    The full line is kept for context in reports.
    """
    results = defaultdict(lambda: defaultdict(list))

    # Build one combined pattern that matches any known prefix
    # e.g.  \$(color|radius|dur)-([a-zA-Z0-9_-]+)
    if not known_prefixes:
        return results

    prefix_group = "|".join(re.escape(p) for p in sorted(known_prefixes, key=len, reverse=True))
    pattern      = re.compile(r"\$(" + prefix_group + r")-([a-zA-Z0-9_-]+)")

    for i, line in enumerate(file_text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for m in pattern.finditer(line):
            prefix  = m.group(1)
            varname = m.group(2)
            if varname.startswith("_"):
                continue
            results[prefix][varname].append({
                "line_num":  i,
                "line_text": stripped,
            })

    return results


def parse_all_dollar_refs(file_text: str, known_prefixes: set) -> dict:
    """
    Scan a locs file and collect EVERY $varname reference, split into two buckets:

      "unrecognized"  — any $var whose prefix segment does not match a known prefix,
                        AND bare $var names with no prefix segment at all.
                        These are things the system does not recognize.

      "recognized"    — $prefix-var where prefix IS in known_prefixes.
                        Returned for completeness so callers can cross-check.

    Rules:
      - $_ names (underscore immediately after $) are always skipped.
      - Comment lines are skipped.

    Returns:
        {
            "unrecognized": [(full_ref, line_num, line_text), ...],
            "recognized":   [(full_ref, line_num, line_text), ...],
        }
    """
    any_var  = re.compile(r"\$([a-zA-Z0-9_-]+)")
    result   = {"unrecognized": [], "recognized": []}

    # Build prefix matcher for quick lookup
    prefix_group = "|".join(re.escape(p) for p in sorted(known_prefixes, key=len, reverse=True))
    prefix_re    = re.compile(r"^(" + prefix_group + r")-(.+)$") if known_prefixes else None

    for i, line in enumerate(file_text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for m in any_var.finditer(line):
            name = m.group(1)
            if name.startswith("_"):
                continue
            full_ref = f"${name}"
            entry    = (full_ref, i, stripped)
            if prefix_re and prefix_re.match(name):
                result["recognized"].append(entry)
            else:
                result["unrecognized"].append(entry)

    return result


# ─────────────────────────────────────────────────────────────
# Output — writes to terminal AND string buffer simultaneously
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
        self.line("=" * 68)
        self.line(f"  {title}")
        self.line("=" * 68)

    def subsection(self, title: str):
        self.line()
        self.line(f"  ── {title}")
        self.line(f"  {'─' * 60}")

    def get(self) -> str:
        return self.buf.getvalue()


def make_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir   = Path.cwd() / "scripts" / "data" / "audits" / "decs-audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"decs-audit_{timestamp}.txt"


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════════════╗")
    out.line("║          decs + effs full audit                  ║")
    out.line("╚══════════════════════════════════════════════════╝")

    tokens_path = prompt_file("1. Path to tokens.scss   (e.g. src/styles/tokens.scss)")
    decs_dir    = prompt_dir ("2. Path to decs/          (e.g. src/styles/decs)")
    effs_dir    = prompt_dir ("3. Path to effs/          (e.g. src/styles/effs)")
    locs_dir    = prompt_dir ("4. Path to locs/          (e.g. src/locs)")

    tokens_text = read(tokens_path)
    locs_files  = scss_files(locs_dir)

    if not locs_files:
        out.line(f"\n  No .scss files found in {locs_dir}")
        sys.exit(0)

    # ── Parse tokens.scss → build prefix map ─────────────────
    token_entries = parse_tokens(tokens_text, tokens_path, decs_dir, effs_dir)
    known_prefixes = {e["prefix"] for e in token_entries}

    out.line()
    out.line(f"  tokens.scss      : {tokens_path}")
    out.line(f"  decs/            : {decs_dir}")
    out.line(f"  effs/            : {effs_dir}")
    out.line(f"  locs/            : {locs_dir}")
    out.line(f"  @forward entries : {len(token_entries)}")
    out.line(f"  locs files       : {len(locs_files)}")

    # ── Read declared vars from every forwarded file ──────────
    # declared_map: prefix → set of varnames
    declared_map = {}
    for entry in token_entries:
        if entry["file"] is not None:
            text = read(entry["file"])
            declared_map[entry["prefix"]] = parse_declared_vars(text)
        else:
            declared_map[entry["prefix"]] = set()

    # ── Scan every locs file ──────────────────────────────────
    # used_map:     prefix → varname → list of {file, line_num, line_text}
    # unknown_map:  locs filename → list of (ref, line_num, line_text)
    used_map    = defaultdict(lambda: defaultdict(list))
    unknown_map = defaultdict(list)

    for lf in locs_files:
        text = read(lf)
        refs = parse_locs_refs(text, known_prefixes)
        for prefix, varnames in refs.items():
            for varname, hits in varnames.items():
                for h in hits:
                    used_map[prefix][varname].append({
                        "file":      lf.name,
                        "line_num":  h["line_num"],
                        "line_text": h["line_text"],
                    })

        all_refs = parse_all_dollar_refs(text, known_prefixes)
        if all_refs["unrecognized"]:
            unknown_map[lf.name].extend(all_refs["unrecognized"])

    # ─────────────────────────────────────────────────────────
    # Report 1 — @forward entries with no file on disk
    # ─────────────────────────────────────────────────────────
    out.section("1 — @FORWARD ENTRIES IN tokens.scss WITH NO FILE ON DISK")
    missing_files = [e for e in token_entries if e["file"] is None]
    if missing_files:
        out.line()
        for e in missing_files:
            out.line(f"  prefix: ${e['prefix']}-*   path: {e['raw_path']}")
    else:
        out.line("\n  All @forward entries resolve to existing files.")

    # ─────────────────────────────────────────────────────────
    # Report 2 — prefixes used in locs with no @forward entry
    # ─────────────────────────────────────────────────────────
    out.section("2 — $VARIABLE REFS IN LOCS NOT MATCHING ANY KNOWN PREFIX")
    if unknown_map:
        for fname, hits in sorted(unknown_map.items()):
            # Deduplicate by ref+line
            seen  = set()
            clean = []
            for ref, ln, line in hits:
                key = (ref, ln)
                if key not in seen:
                    seen.add(key)
                    clean.append((ref, ln, line))
            out.line(f"\n  {fname}")
            for ref, ln, line in clean:
                out.line(f"    line {ln:>4}  {ref:<32}  {line[:55]}")
    else:
        out.line("\n  Every prefix used in locs has a matching @forward entry.")

    # ─────────────────────────────────────────────────────────
    # Report 2b — every unrecognized $var across all locs files
    # ─────────────────────────────────────────────────────────
    out.section("2b — ALL UNRECOGNIZED $VARIABLE REFS ACROSS ALL LOCS FILES")
    out.line()

    # Collect fresh across every locs file for this report
    all_unrecognized = {}   # locs filename → [(full_ref, line_num, line_text)]
    for lf in locs_files:
        refs = parse_all_dollar_refs(read(lf), known_prefixes)
        if refs["unrecognized"]:
            # Deduplicate by (ref, line_num)
            seen  = set()
            clean = []
            for entry in refs["unrecognized"]:
                key = (entry[0], entry[1])
                if key not in seen:
                    seen.add(key)
                    clean.append(entry)
            all_unrecognized[lf.name] = clean

    if all_unrecognized:
        for fname in sorted(all_unrecognized):
            out.line(f"  {fname}")
            for full_ref, ln, line_text in all_unrecognized[fname]:
                out.line(f"    line {ln:>4}  {full_ref:<32}  {line_text[:55]}")
            out.line()
    else:
        out.line("  Every $variable in every locs file uses a recognized prefix.")

    # ─────────────────────────────────────────────────────────
    # Report 3 — declared but never used (per file)
    # ─────────────────────────────────────────────────────────
    out.section("3 — DECLARED IN DECS/EFFS BUT NEVER USED IN ANY LOCS FILE")

    total_unused = 0
    for entry in token_entries:
        prefix    = entry["prefix"]
        declared  = declared_map.get(prefix, set())
        used      = set(used_map.get(prefix, {}).keys())
        never_used = sorted(declared - used)

        label = f"{entry['raw_path']}   (${prefix}-*)"
        out.subsection(label)

        if entry["file"] is None:
            out.line("  [file not found — skipped]")
            continue

        if not declared:
            out.line("  [no variables declared in this file]")
            continue

        if never_used:
            total_unused += len(never_used)
            for varname in never_used:
                out.line(f"  ${prefix}-{varname}")
        else:
            out.line(f"  All {len(declared)} declared variables are used.")

    out.line()
    out.line(f"  Total unused declarations across all files: {total_unused}")

    # ─────────────────────────────────────────────────────────
    # Report 4 — used in locs but not declared (per file)
    # ─────────────────────────────────────────────────────────
    out.section("4 — USED IN LOCS BUT NOT DECLARED IN THE MATCHING DECS/EFFS FILE")

    total_missing = 0
    for entry in token_entries:
        prefix   = entry["prefix"]
        declared = declared_map.get(prefix, set())
        used     = used_map.get(prefix, {})
        missing  = {v: locs for v, locs in used.items() if v not in declared}

        if not missing:
            continue

        label = f"{entry['raw_path']}   (${prefix}-*)"
        out.subsection(label)
        total_missing += len(missing)

        for varname in sorted(missing):
            full_ref = f"${prefix}-{varname}"
            out.line(f"\n  {full_ref}")
            seen = set()
            for hit in missing[varname]:
                key = (hit["file"], hit["line_num"])
                if key in seen:
                    continue
                seen.add(key)
                out.line(f"    {hit['file']}  line {hit['line_num']:>4}  {hit['line_text'][:60]}")

    if total_missing == 0:
        out.line("\n  Every variable used in locs is declared in its decs/effs file.")
    else:
        out.line(f"\n  Total undeclared variables used across all files: {total_missing}")

    # ─────────────────────────────────────────────────────────
    # Report 5 — per locs file usage overview
    # ─────────────────────────────────────────────────────────
    out.section("5 — PER LOCS FILE — PREFIX USAGE OVERVIEW")
    out.line()

    # Build per-locs-file summary by re-scanning
    locs_summary = {}
    for lf in locs_files:
        text = read(lf)
        refs = parse_locs_refs(text, known_prefixes)
        prefix_counts = {
            pfx: len(vars_)
            for pfx, vars_ in refs.items()
        }
        locs_summary[lf.name] = prefix_counts

    for fname in sorted(locs_summary):
        counts = locs_summary[fname]
        if not counts:
            out.line(f"  {fname}")
            out.line(f"    (no known-prefix variables found)")
            continue
        out.line(f"  {fname}")
        for prefix in sorted(counts):
            n = counts[prefix]
            out.line(f"    ${prefix}-*   {n} variable{'s' if n != 1 else ''}")

    # ─────────────────────────────────────────────────────────
    # Report 6 — summary
    # ─────────────────────────────────────────────────────────
    out.section("6 — SUMMARY")

    total_declared = sum(len(v) for v in declared_map.values())
    total_used     = sum(len(v) for v in used_map.values())
    files_ok       = sum(
        1 for e in token_entries
        if e["file"] is not None
        and not (set(used_map.get(e["prefix"], {}).keys()) - declared_map.get(e["prefix"], set()))
        and not (declared_map.get(e["prefix"], set()) - set(used_map.get(e["prefix"], {}).keys()))
    )

    out.line(f"  @forward entries         :  {len(token_entries)}")
    out.line(f"  Files missing on disk    :  {len(missing_files)}")
    out.line(f"  Total declared vars      :  {total_declared}")
    out.line(f"  Total used var names     :  {total_used}")
    out.line(f"  Unused declarations      :  {total_unused}")
    out.line(f"  Undeclared usages        :  {total_missing}")
    out.line(f"  Unrecognized $var refs   :  {sum(len(v) for v in unknown_map.values())}")
    out.line(f"  Locs files scanned       :  {len(locs_files)}")
    out.line(f"  Fully clean decs/effs    :  {files_ok} of {len(token_entries)}")
    out.line()

    # ─────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────
    output_path = make_output_path()
    output_path.write_text(out.get(), encoding="utf-8")
    print(f"\n  Report saved → {output_path}\n")


if __name__ == "__main__":
    run()