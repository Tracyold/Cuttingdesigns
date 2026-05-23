#!/usr/bin/env python3
"""
Replace raw color values in locs files with $name SCSS color token references
from styles/decs/colors.scss.

Complements replace_locs.py (which handles structural/layout properties but
skips color and background-color).  This script handles exactly those two.

Usage:
  python3 replace_locs_colors.py [--dry-run] [--file FILENAME]
"""
import re
import argparse
from pathlib import Path
from collections import defaultdict

LOCS_DIR    = Path('/home/user/Cuttingdesigns/my-app/src/locs')
COLORS_FILE = Path('/home/user/Cuttingdesigns/my-app/src/styles/decs/colors.scss')

COLOR_PROPS = {'color', 'background-color'}

# Color-role words that trail the component name in a $token — never the comp
_COLOR_ROLES = {
    'bg', 'text', 'icon', 'track', 'button', 'bar', 'scrim', 'hint',
    'label', 'border', 'overlay', 'ring', 'glow', 'dot', 'arrow',
    'divider', 'knob', 'fade', 'active', 'inactive', 'read', 'waiting',
    'top', 'bot', 'down', 'up', 'receipt', 'note', 'placeholder',
}

# State words — never the component when a real component name precedes them
_STATE_ONLY  = {'hover', 'focus', 'disabled'}
_MAYBE_STATE = {'active', 'open'}


def normalize(v: str) -> str:
    return re.sub(r'\s+', ' ', v.strip())


# ── colors.scss parser ────────────────────────────────────────────────────────

def _comp_from_color_name(name: str) -> str:
    """Extract component prefix from a color $token name.

    '$panel-bg'              → 'panel'
    '$nav-icon-active'       → 'nav'
    '$status-pill-read-bg'   → 'status-pill'
    '$bubble-admin-text'     → 'bubble-admin'
    '$star-active'           → 'star'
    """
    parts = name.lstrip('$').split('-')
    while len(parts) > 1 and parts[-1] in _COLOR_ROLES:
        parts.pop()
    return '-'.join(parts)


def parse_colors(filepath: Path) -> dict[str, list[tuple[str, str]]]:
    """Return {normalized_hex_or_rgba: [(comp, '$name')]} from dark-theme values.

    colors.scss stores color values in two ways:
      1. $name: var(--variable)  — resolved through :root dark-theme values
      2. $name: #hex             — raw hex used directly (e.g. $star-active)
    """
    text = filepath.read_text(encoding='utf-8')

    # Dark-theme :root custom property values
    var_to_val: dict[str, str] = {}
    root_m = re.search(r':root\s*\{([^}]*)\}', text, re.DOTALL)
    if root_m:
        for m in re.finditer(r'--([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*;',
                             root_m.group(1)):
            var_to_val[m.group(1)] = normalize(m.group(2))

    # Remove all :selector { } blocks so we only process file-scope $names
    scope_text = re.sub(r':[A-Za-z.][^{]*\{[^}]*\}', '', text, flags=re.DOTALL)

    lookup: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for m in re.finditer(r'^\$([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*;',
                         scope_text, re.MULTILINE):
        name = m.group(1)
        val  = normalize(m.group(2))
        resolved: str | None = None

        var_m = re.match(r'^var\(--([A-Za-z0-9_-]+)\)$', val)
        if var_m:
            dark_val = var_to_val.get(var_m.group(1), '')
            # Only tokenise simple atomic colors (hex / rgba) — not gradients etc.
            if re.match(r'^#[0-9a-fA-F]{3,8}$|^rgba?\(', dark_val):
                resolved = dark_val
        elif re.match(r'^#[0-9a-fA-F]{3,8}$', val):
            resolved = val

        if resolved:
            comp = _comp_from_color_name(name)
            lookup[resolved].append((comp, f'${name}'))

    return dict(lookup)


# ── Class / property line parser ──────────────────────────────────────────────

CLASS_RE = re.compile(r'^\.([^\s{,]+)')
PROP_RE  = re.compile(r'^(\s+)([\w-]+)\s*:\s*(.+?)\s*;(\s*(?://.*)?)?$')


def _comp_from_classname(class_name: str, known: set[str]) -> str:
    """Derive the most-specific component prefix from a CSS class name.
    Mirrors the same function in replace_locs.py.
    """
    name     = class_name.lstrip('.')
    parts    = name.split('-')
    compound = parts[-1]

    def best_suffix(s: str) -> str | None:
        for c in sorted(known, key=len, reverse=True):
            if s.endswith(c) and c != s:
                return c
        return None

    def best_prefix(s: str) -> str | None:
        for c in sorted(known, key=len, reverse=True):
            if s.startswith(c) and c != s:
                return c
        return None

    suffix_comp = best_suffix(compound)
    prefix_comp = best_prefix(compound)

    if suffix_comp is not None:
        if suffix_comp in _STATE_ONLY:
            if prefix_comp:
                return prefix_comp
        elif suffix_comp in _MAYBE_STATE and prefix_comp:
            return prefix_comp
        else:
            return suffix_comp

    if compound in known:
        return compound
    if prefix_comp:
        return prefix_comp
    for p in reversed(parts):
        if p in known:
            return p
    joined = ''.join(parts)
    for c in sorted(known, key=len, reverse=True):
        if joined.endswith(c):
            return c
    return parts[-1]


def find_color_var(
    css_value: str,
    prop: str,
    class_name: str,
    lookup: dict[str, list[tuple[str, str]]],
    known_comps: set[str],
) -> tuple[str | None, str | None]:
    """Find best $token for (css_value, prop, class_name).

    Returns (var_name, None) on success or (None, reason) on skip.
    """
    norm = normalize(css_value)
    if norm not in lookup:
        return None, f"value not in colors.scss dark theme: {norm!r}"

    aliases = lookup[norm]
    if len(aliases) == 1:
        return aliases[0][1], None

    # Prefer tokens whose role suffix matches the CSS property
    role_hints: dict[str, set[str]] = {
        'color':            {'text', 'icon', 'label', 'hint', 'arrow', 'meta',
                             'placeholder', 'receipt'},
        'background-color': {'bg', 'track', 'button', 'knob'},
    }
    hints     = role_hints.get(prop, set())
    hinted    = [(c, n) for c, n in aliases
                 if any(n.endswith(f'-{h}') for h in hints)]
    candidates = hinted if hinted else aliases

    if len(candidates) == 1:
        return candidates[0][1], None

    comp = _comp_from_classname(class_name, known_comps)

    # Exact component match — prefer base var (no -2 suffix) first
    for no_num in (True, False):
        matches = [
            (c, n) for c, n in candidates
            if c == comp and (not no_num or not re.search(r'-\d+$', n))
        ]
        if matches:
            return matches[0][1], None

    # Partial component match
    for no_num in (True, False):
        matches = [
            (c, n) for c, n in candidates
            if (comp in c or c in comp)
            and (not no_num or not re.search(r'-\d+$', n))
        ]
        if matches:
            return matches[0][1], None

    opts = ', '.join(f'{n}[{c}]' for c, n in candidates[:6])
    return None, f"ambiguous for class='{class_name}' comp='{comp}' opts=[{opts}]"


# ── File processor ────────────────────────────────────────────────────────────

def process_file(
    filepath: Path,
    lookup: dict[str, list[tuple[str, str]]],
    known_comps: set[str],
    dry_run: bool = False,
) -> tuple[str, list[str]]:
    """Return (new_content, [skip_notes])."""
    content   = filepath.read_text(encoding='utf-8')
    lines     = content.splitlines(keepends=True)
    out_lines: list[str] = []
    skips:     list[str] = []
    current_class: str | None = None

    for line in lines:
        stripped = line.rstrip('\n')

        cls_m = CLASS_RE.match(stripped.lstrip())
        if cls_m:
            current_class = cls_m.group(1)

        prop_m = PROP_RE.match(stripped) if current_class else None

        if prop_m:
            indent   = prop_m.group(1)
            prop     = prop_m.group(2)
            value    = prop_m.group(3).strip()
            trailing = prop_m.group(4) or ''

            if prop not in COLOR_PROPS:
                out_lines.append(line)
                continue

            # Already tokenised
            if value.startswith('$') or 'var(' in value:
                out_lines.append(line)
                continue

            var_name, reason = find_color_var(
                value, prop, current_class, lookup, known_comps
            )
            if var_name:
                out_lines.append(f'{indent}{prop}: {var_name};{trailing}\n')
            else:
                skips.append(
                    f'  .{current_class} / {prop}: {value}  ← {reason}'
                )
                out_lines.append(line)
        else:
            out_lines.append(line)

    return ''.join(out_lines), skips


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description='Replace raw color values in locs/ with $name color tokens.'
    )
    ap.add_argument('--dry-run', action='store_true',
                    help='Print changes without writing files')
    ap.add_argument('--file', default=None,
                    help='Process only this filename (e.g. shop-card.scss)')
    args = ap.parse_args()

    print('Parsing colors.scss…')
    lookup = parse_colors(COLORS_FILE)

    known_comps: set[str] = set()
    for aliases in lookup.values():
        for comp, _ in aliases:
            known_comps.add(comp)

    total_tokens = sum(len(v) for v in lookup.values())
    print(f'  {total_tokens} color tokens across {len(lookup)} unique values, '
          f'{len(known_comps)} component prefixes\n')

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
                print(f'[DRY RUN] Would update: '
                      f'{fp.relative_to(LOCS_DIR.parent.parent)}')
            else:
                fp.write_text(new_content, encoding='utf-8')
                print(f'Updated: {fp.name}')

        if skips:
            all_skips[fp.name] = skips

    prefix = '[DRY RUN] ' if args.dry_run else ''
    print(f'\n{prefix}{changed} file(s) {"would be " if args.dry_run else ""}modified.\n')

    if all_skips:
        print('═' * 60)
        print('SKIP REPORT (raw color values with no matching $name):')
        print('═' * 60)
        for fname, notes in sorted(all_skips.items()):
            print(f'\n  {fname}:')
            for n in notes:
                print(n)


if __name__ == '__main__':
    main()
