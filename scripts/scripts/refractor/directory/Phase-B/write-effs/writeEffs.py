#!/usr/bin/env python3
"""
generate_effs.py

Reads scan_effects.py Report 2 (per-property) output.
Generates one SCSS effs file per motion property type.

Naming rules:
  Primitives at top of each file:
    $_a_: 150ms;
    $_b_: 80ms;
    ...

  Component aliases:
    $firstword_full_property_name: $_a_;

    firstword = first word of the source filename (before - or _)
    full_property_name = entire CSS property with - replaced by _

  Example:
    transition-duration: 150ms  from  button.jsx
    → $button_transition_duration: $_a_;

Output: scripts/data/AnatomyMatters/styles/effs/
  One file per motion property:
    durations.scss
    easings.scss
    transforms.scss
    animations.scss
    ... etc
"""

import re
import sys
import string
from pathlib import Path
from datetime import datetime
from collections import defaultdict, OrderedDict
from itertools import product


# ── Paths ─────────────────────────────────────────────────────────────

EFFECTS_DIR = Path.cwd() / "scripts" / "data" / "audits" / "effects"
OUTPUT_DIR  = Path.cwd() / "scripts" / "data" / "AnatomyMatters" / "styles" / "effs"


# ── Motion properties whitelist ───────────────────────────────────────

VALID_MOTION_PROPS = {
    # Transition
    "transition", "transition-property", "transition-duration",
    "transition-timing-function", "transition-delay",
    # Animation
    "animation", "animation-name", "animation-duration",
    "animation-timing-function", "animation-delay",
    "animation-iteration-count", "animation-direction",
    "animation-fill-mode", "animation-play-state",
    # Transform
    "transform", "transform-origin", "transform-style",
    "perspective", "perspective-origin",
    # Scroll motion
    "scroll-behavior", "scroll-snap-type", "scroll-snap-align",
    "scroll-padding", "scroll-padding-left", "scroll-padding-top",
    "overscroll-behavior", "overscroll-behavior-x", "overscroll-behavior-y",
    # Performance
    "will-change",
    # Opacity (motion context)
    "opacity",
    # Offset/motion path
    "offset-path", "offset-distance", "offset-rotate",
}

# Friendly file names per property
PROP_FILENAMES = {
    "transition-duration":       "durations",
    "animation-duration":        "durations",
    "transition-timing-function":"easings",
    "animation-timing-function": "easings",
    "transition-delay":          "delays",
    "animation-delay":           "delays",
    "transform":                 "transforms",
    "transform-origin":          "transform-origins",
    "animation-name":            "animation-names",
    "animation-iteration-count": "iterations",
    "animation-fill-mode":       "fills",
    "animation-direction":       "directions",
    "animation-play-state":      "play-states",
    "transition":                "transitions",
    "animation":                 "animations",
    "will-change":               "will-change",
    "scroll-behavior":           "scroll-behavior",
    "scroll-snap-type":          "scroll-snap",
    "scroll-snap-align":         "scroll-snap",
    "overscroll-behavior":       "overscroll",
    "opacity":                   "opacities",
    "perspective":               "perspective",
}

def prop_filename(prop):
    return PROP_FILENAMES.get(prop.lower(), prop.lower().replace(' ', '-'))


def is_valid_motion_prop(prop):
    return prop.strip().lower() in VALID_MOTION_PROPS


# ── Primitive name generator ──────────────────────────────────────────

def gen_primitive_names():
    letters = string.ascii_lowercase
    length  = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '$_' + ''.join(combo) + '_'
        length += 1


# ── Name helpers ──────────────────────────────────────────────────────

def first_word(name):
    name  = re.sub(r'\.\w+$', '', name.strip())
    parts = re.split(r'[\s\-_/\\]+', name)
    return parts[0].lower() if parts else name.lower()

def prop_to_varfrag(prop):
    return prop.strip().lower().replace('-', '_')

def make_alias(source_name, prop):
    return f'${first_word(source_name)}_{prop_to_varfrag(prop)}'

def normalize(v):
    return re.sub(r'\s+', ' ', v.strip().lower())


def is_scss_safe(value):
    """Return False if value contains JS expressions that are not valid SCSS."""
    v = value.strip()
    if '${' in v:             return False   # JS template literal
    if '=>' in v:             return False   # JS arrow function
    if 'true' == v.lower():   return False   # JS boolean
    if 'false' == v.lower():  return False   # JS boolean
    if v.startswith('c.'):    return False   # JS object access
    if v.startswith('dark'):  return False   # JS variable
    if 'COPIES' in v:         return False   # JS constant
    if re.search(r'(const|let|var|function|return)', v): return False
    return True


# ── Report 2 parser (effects) ─────────────────────────────────────────

def parse_effects_report2(path):
    """
    Parse scan_effects.py Report 2 (per-property) format:

    ──────────────────────────────────────
      transition-duration  (4 unique values)
    ──────────────────────────────────────
      150ms         ← button.jsx, card.jsx
      80ms          ← fab.jsx

    Returns list of (prop, value, source_file)
    """
    text    = path.read_text(encoding="utf-8", errors="ignore")
    entries = []

    current_prop = None

    for line in text.splitlines():
        s = line.strip()

        if not s:
            continue
        if s.startswith('╔') or s.startswith('║') or s.startswith('╚'):
            continue
        if set(s) <= set('─═ '):
            continue

        # Property header: "transition-duration  (4 unique values)"
        m = re.match(r'^([\w\-]+)\s+\(\d+\s+unique\s+value', s)
        if m:
            current_prop = m.group(1).strip()
            continue

        # Value line: "  150ms    ← button.jsx, card.jsx"
        if current_prop and '←' in s:
            parts = s.split('←', 1)
            value = parts[0].strip()
            files = [f.strip() for f in parts[1].split(',')]
            if value and is_valid_motion_prop(current_prop):
                for f in files:
                    entries.append((current_prop, value, f))
            continue

        # Value without file
        if current_prop and s and is_valid_motion_prop(current_prop):
            entries.append((current_prop, s, 'unknown'))

    return entries


# ── effs file generator ───────────────────────────────────────────────

def generate_effs_file(prop, fname, entries):
    """
    entries: list of (value, source_file)
    Returns SCSS file content string or None.
    """
    unique_vals = OrderedDict()
    for value, source in entries:
        if not is_scss_safe(value):
            continue
        norm = normalize(value)
        if norm and norm not in unique_vals:
            unique_vals[norm] = value.strip()

    if not unique_vals:
        return None

    name_gen = gen_primitive_names()
    prim_map = {norm: next(name_gen) for norm in unique_vals}

    aliases      = OrderedDict()
    seen_aliases = set()

    for value, source in entries:
        norm = normalize(value)
        if norm not in prim_map:
            continue
        alias = make_alias(source, prop)
        if alias in seen_aliases:
            continue
        seen_aliases.add(alias)
        aliases[alias] = prim_map[norm]

    prop_kebab = prop.strip().lower()
    lines      = []

    lines.append(f'// {"═" * 67}')
    lines.append(f'// effs/{fname}.scss')
    lines.append(f'// Every {prop_kebab} value in the system.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_effs.py')
    lines.append(f'//')
    lines.append(f'// Usage: @use "tokens" as *;')
    lines.append(f'// Then:  {prop_kebab}: $component_{prop_to_varfrag(prop)};')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Primitives
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Raw scale — every unique {prop_kebab} value')
    lines.append(f'// {"─" * 44}')
    lines.append('')

    max_prim_len = max(len(p) for p in prim_map.values()) if prim_map else 6
    for norm, prim in prim_map.items():
        display = unique_vals[norm]
        pad     = max(1, max_prim_len + 4 - len(prim))
        lines.append(f'{prim}:{" " * pad}{display};')

    lines.append('')

    # Aliases
    lines.append('')
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 44}')
    lines.append('')

    source_groups   = defaultdict(list)
    max_alias_len   = max(len(a) for a in aliases) if aliases else 10

    for alias, prim in aliases.items():
        fw = alias.split('_')[1] if alias.count('_') >= 2 else alias[1:]
        source_groups[fw].append((alias, prim))

    for source, alias_list in sorted(source_groups.items()):
        lines.append(f'// ── {source} {"─" * max(1, 48 - len(source))}')
        for alias, prim in sorted(alias_list):
            pad = max(1, max_alias_len + 4 - len(alias))
            lines.append(f'{alias}:{" " * pad}{prim};')
        lines.append('')

    return '\n'.join(lines)


# ── Main ──────────────────────────────────────────────────────────────

def prompt_report():
    print()
    print(f"  Looking in: {EFFECTS_DIR}")

    if not EFFECTS_DIR.exists():
        print("  ✗ Effects directory not found. Run scan_effects.py first.")
        sys.exit(1)

    reports = sorted(EFFECTS_DIR.glob("*.txt"))
    if not reports:
        print("  ✗ No reports found. Run scan_effects.py first.")
        sys.exit(1)

    print()
    print("  Available reports:\n")
    for i, r in enumerate(reports):
        print(f"  [{i+1}]  {r.name}")

    print()
    choice = input("  Enter number (or Enter for most recent): ").strip()

    if not choice:
        return sorted(reports)[-1]
    try:
        return reports[int(choice) - 1]
    except (ValueError, IndexError):
        print("  Invalid — using most recent.")
        return sorted(reports)[-1]


def run():
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║         effs file generator                          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Reads a scan_effects.py Report 2 (per-property) file.")
    print("  Generates one .scss effs file per motion property.")
    print()
    print("  Naming:")
    print("    Primitives : $_a_  $_b_  $_c_  ...")
    print("    Aliases    : $firstword_full_property_name: $_a_;")
    print()
    print("  Output: scripts/data/AnatomyMatters/styles/effs/")

    report_path = prompt_report()
    print(f"\n  Using: {report_path.name}\n")

    entries = parse_effects_report2(report_path)

    if not entries:
        print("  ✗ No entries found. Select a Report 2 (per-property) file.")
        sys.exit(1)

    print(f"  Found {len(entries)} motion entries across "
          f"{len(set(e[0] for e in entries))} properties.\n")

    # Group by property
    by_prop = defaultdict(list)
    for prop, value, source in entries:
        by_prop[prop].append((value, source))

    # Further group by output filename (multiple props can share a file e.g. durations)
    by_file = defaultdict(lambda: defaultdict(list))
    for prop, prop_entries in by_prop.items():
        fname = prop_filename(prop)
        by_file[fname][prop].extend(prop_entries)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated = 0

    for fname, prop_group in sorted(by_file.items()):
        # Merge all props that share a filename into one file
        all_entries = []
        prop_list   = []
        for prop, ents in prop_group.items():
            all_entries.extend([(prop, v, s) for v, s in ents])
            prop_list.append(prop)

        # Use the first prop for file generation metadata
        primary_prop = prop_list[0]
        merged_entries = [(v, s) for _, v, s in all_entries]

        content = generate_effs_file(primary_prop, fname, merged_entries)
        if not content:
            print(f"  –  {fname}.scss  (no usable values)")
            continue

        out_path = OUTPUT_DIR / f"{fname}.scss"
        out_path.write_text(content, encoding="utf-8")
        generated += 1
        unique_count = len(set(normalize(v) for v, _ in merged_entries))
        print(f"  ✓  {fname}.scss{' ' * max(1, 30 - len(fname))} {unique_count} unique values")

    print(f"\n  ✓ Generated {generated} effs files")
    print(f"  ✓ Saved to → {OUTPUT_DIR}\n")


if __name__ == "__main__":
    run()