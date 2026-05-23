#!/usr/bin/env python3
"""
Replace raw CSS values in locs files with $name SCSS variable references
from styles/decs/ and styles/effs/ directories.

Usage:
  python3 replace_locs.py [--dry-run]
"""
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

LOCS_DIR  = Path('/home/user/Cuttingdesigns/my-app/src/locs')
DECS_DIR  = Path('/home/user/Cuttingdesigns/my-app/src/styles/decs')
EFFS_DIR  = Path('/home/user/Cuttingdesigns/my-app/src/styles/effs')

# CSS properties that have no token file and should be left alone
ALWAYS_SKIP_PROPS = {'transform', 'color', 'background-color'}

def normalize_value(v: str) -> str:
    """Collapse whitespace for reliable comparison."""
    return re.sub(r'\s+', ' ', v.strip())

def parse_token_file(filepath: Path):
    """Return (raw_scale, aliases) for one decs/effs file.

    raw_scale : {$_x_: css_value}
    aliases   : [(var_name, raw_var_name)]   — preserves order
    """
    text = filepath.read_text(encoding='utf-8')

    raw_scale: dict[str, str] = {}
    for m in re.finditer(r'^(\$_\w+_)\s*:\s*(.*?)\s*;', text, re.MULTILINE):
        raw_scale[m.group(1)] = normalize_value(m.group(2))

    aliases: list[tuple[str, str]] = []
    for m in re.finditer(r'^(\$[\w()*./+\-]+)\s*:\s*(\$_\w+_)\s*;', text, re.MULTILINE):
        aliases.append((m.group(1), m.group(2)))

    return raw_scale, aliases

def build_lookup() -> dict:
    """Return {property: {css_value: [(component, var_name)]}}."""
    lookup: dict[str, dict[str, list]] = {}

    for dirpath in (DECS_DIR, EFFS_DIR):
        for fp in sorted(dirpath.glob('*.scss')):
            stem = fp.stem
            if stem.startswith('--') or stem in ('colors', 'shadows'):
                continue  # skip CSS-custom-property and theme files

            prop = stem  # CSS property name matches file stem
            raw_scale, aliases = parse_token_file(fp)

            by_value: dict[str, list] = defaultdict(list)
            for var_name, raw_var in aliases:
                if raw_var in raw_scale:
                    val = raw_scale[raw_var]
                    comp = _comp_from_varname(var_name, prop)
                    by_value[val].append((comp, var_name))

            if by_value:
                lookup[prop] = dict(by_value)

    return lookup

def _comp_from_varname(var_name: str, prop: str) -> str:
    """Extract component prefix from a token var name.

    '$nav-height'   + 'height'   → 'nav'
    '$nav-height-2' + 'height'   → 'nav'
    '$btn-z-index'  + 'z-index'  → 'btn'
    """
    body = var_name.lstrip('$')
    pattern = rf'^(.+?)-{re.escape(prop)}(?:-\d+)?$'
    m = re.match(pattern, body)
    return m.group(1) if m else body


# Words that act only as UI-state suffixes and should never be the component identifier
# when a genuine component name precedes them in a compound word.
_STATE_ONLY_SUFFIXES = {'hover', 'focus', 'disabled'}

# Words that CAN be real components but also appear as state suffixes; when a
# known component is also found as a prefix of the compound, prefer the prefix.
_MAYBE_STATE_SUFFIXES = {'active', 'open'}


def _comp_from_classname(class_name: str, known: set[str]) -> str:
    """Derive the most-specific component prefix from a CSS class name.

    '.bottom-nav'      → 'nav'
    '.nav-btn'         → 'btn'
    '.nav-btnfab'      → 'fab'         (suffix of compound 'btnfab')
    '.panel'           → 'panel'
    '.panelclosing'    → 'closing'
    '.drawer'          → 'drawer'
    '.panel-closehover'→ 'close'       (strip state suffix 'hover')
    '.panel-closeactive'→ 'close'      (prefer prefix 'close' over 'active')
    """
    name = class_name.lstrip('.')
    parts = name.split('-')
    compound = parts[-1]

    # Helper: find the longest known comp that is a SUFFIX of s (and shorter)
    def best_suffix(s: str) -> str | None:
        for c in sorted(known, key=len, reverse=True):
            if s.endswith(c) and c != s:
                return c
        return None

    # Helper: find the longest known comp that is a PREFIX of s (and shorter)
    def best_prefix(s: str) -> str | None:
        for c in sorted(known, key=len, reverse=True):
            if s.startswith(c) and c != s:
                return c
        return None

    # 1) Analyse the compound last segment
    suffix_comp = best_suffix(compound)
    prefix_comp = best_prefix(compound)

    if suffix_comp is not None:
        if suffix_comp in _STATE_ONLY_SUFFIXES:
            # Pure state suffix — prefer the prefix component if one exists
            if prefix_comp:
                return prefix_comp
        elif suffix_comp in _MAYBE_STATE_SUFFIXES and prefix_comp:
            # Ambiguous suffix — prefer the prefix (e.g. 'close' over 'active')
            return prefix_comp
        else:
            return suffix_comp  # e.g. 'fab' from 'btnfab'

    # 2) Exact match on the compound itself — BEFORE prefix fallback so that
    #    e.g. 'panel' resolves to 'panel' not to the shorter prefix 'pane'.
    if compound in known:
        return compound

    if prefix_comp:
        return prefix_comp  # e.g. 'close' from 'closehover' when 'hover' not known

    # 3) Exact match on the rightmost hyphen-separated part
    if parts[-1] in known:
        return parts[-1]

    # 3) Exact match on any part (right to left)
    for p in reversed(parts):
        if p in known:
            return p

    # 4) Suffix of full joined name (e.g. 'panelclosing' → 'closing')
    joined = ''.join(parts)
    for comp in sorted(known, key=len, reverse=True):
        if joined.endswith(comp):
            return comp

    return parts[-1]

def find_var(css_value: str, prop: str, class_name: str,
             lookup: dict, known_comps: set) -> tuple[str | None, str | None]:
    """Find the best $variable for (css_value, prop) in context of class_name.

    Returns (var_name, None) on success or (None, reason) on skip.
    """
    if prop not in lookup:
        return None, f"no token file for '{prop}'"

    val_map = lookup[prop]
    norm = normalize_value(css_value)

    if norm not in val_map:
        return None, f"value not declared in {prop}.scss"

    aliases = val_map[norm]

    if len(aliases) == 1:
        return aliases[0][1], None

    comp = _comp_from_classname(class_name, known_comps)

    # exact component match — prefer the base var (no -2 suffix)
    for no_num in (True, False):
        matches = [
            (c, n) for c, n in aliases
            if c == comp and (not no_num or not re.search(r'-\d+$', n))
        ]
        if matches:
            return matches[0][1], None

    # partial match
    for no_num in (True, False):
        matches = [
            (c, n) for c, n in aliases
            if (comp in c or c in comp)
            and (not no_num or not re.search(r'-\d+$', n))
        ]
        if matches:
            return matches[0][1], None

    # give up — too ambiguous
    opts = ', '.join(f'{n}[{c}]' for c, n in aliases[:6])
    return None, f"ambiguous for class='{class_name}' comp='{comp}' opts=[{opts}]"


# ── CSS line / class parser ───────────────────────────────────────────────────

# Matches:  .classname {
CLASS_RE = re.compile(r'^\.([^\s{,]+)')
# Matches:  <indent>property: value;  (value may include spaces, parens, commas)
PROP_RE  = re.compile(r'^(\s+)([\w-]+)\s*:\s*(.+?)\s*;(\s*(?://.*)?)?$')


def process_file(filepath: Path, lookup: dict, known_comps: set,
                 dry_run: bool = False) -> tuple[str, list[str]]:
    """Return (new_content, [skip_notes])."""
    content   = filepath.read_text(encoding='utf-8')
    lines     = content.splitlines(keepends=True)
    out_lines = []
    skip_notes: list[str] = []

    current_class: str | None = None

    for line in lines:
        stripped = line.rstrip('\n')

        # Track current top-level class
        cls_m = CLASS_RE.match(stripped.lstrip())
        if cls_m:
            current_class = cls_m.group(1)

        prop_m = PROP_RE.match(stripped) if current_class else None

        if prop_m:
            indent   = prop_m.group(1)
            prop     = prop_m.group(2)
            value    = prop_m.group(3).strip()
            trailing = prop_m.group(4) or ''

            # Skip properties that are out of scope
            if prop in ALWAYS_SKIP_PROPS:
                out_lines.append(line)
                continue

            # Skip values that already use var() or a $variable
            if 'var(' in value or value.startswith('$'):
                out_lines.append(line)
                continue

            var_name, reason = find_var(value, prop, current_class,
                                        lookup, known_comps)
            if var_name:
                new_line = f"{indent}{prop}: {var_name};{trailing}\n"
                out_lines.append(new_line)
            else:
                skip_notes.append(
                    f"  .{current_class} / {prop}: {value}  ← {reason}"
                )
                out_lines.append(line)
        else:
            out_lines.append(line)

    new_content = ''.join(out_lines)
    return new_content, skip_notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='Print changes without writing files')
    ap.add_argument('--file', default=None,
                    help='Process only this filename (e.g. 3-bottom-nav.scss)')
    args = ap.parse_args()

    print('Building token lookup…')
    lookup = build_lookup()
    known_comps: set[str] = set()
    for val_map in lookup.values():
        for aliases in val_map.values():
            for comp, _ in aliases:
                known_comps.add(comp)

    print(f'  {len(lookup)} CSS properties, {len(known_comps)} component prefixes\n')

    locs_files = (
        list(LOCS_DIR.glob('*.scss')) +
        list((LOCS_DIR / 'locs-re').glob('*.scss'))
    )
    if args.file:
        locs_files = [f for f in locs_files if f.name == args.file]

    all_skips: dict[str, list[str]] = {}
    changed = 0

    for fp in sorted(locs_files):
        new_content, skips = process_file(fp, lookup, known_comps, args.dry_run)
        original = fp.read_text(encoding='utf-8')

        if new_content != original:
            changed += 1
            if args.dry_run:
                print(f'[DRY RUN] Would update: {fp.relative_to(LOCS_DIR.parent.parent)}')
            else:
                fp.write_text(new_content, encoding='utf-8')
                print(f'Updated: {fp.name}')

        if skips:
            all_skips[fp.name] = skips

    print(f'\n{"[DRY RUN] " if args.dry_run else ""}{changed} file(s) would be modified.\n')

    if all_skips:
        print('═' * 60)
        print('SKIP REPORT (values with no declared $name):')
        print('═' * 60)
        for fname, notes in sorted(all_skips.items()):
            print(f'\n  {fname}:')
            for n in notes:
                print(n)

if __name__ == '__main__':
    main()
