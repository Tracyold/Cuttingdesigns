#!/usr/bin/env python3
"""
generate_colors.py

Reads pipeline-colors-shadows.txt produced by scan_params.py.
Writes a single colors.scss containing:

  1. :root block (or themed blocks) with --a, --b, --c ... color variables
  2. Per-component sections with full-property-name aliases
       $button-color:              var(--a);
       $button-background-color:   var(--b);
       $button-border-color:       var(--c);
       $button-box-shadow:         $_a_ var(--c), $_b_ var(--d);
  3. Box-shadow position primitives
       $_a_: 0px 4px 5px;
  4. Shadow index
       $shadow-1: $button-box-shadow;

Output: <user-chosen-dir>/colors.scss
"""

import re
import sys
import string
from pathlib import Path
from datetime import datetime
from collections import OrderedDict, defaultdict
from itertools import product


# ── Primitive / variable name generators ─────────────────────────────

def gen_var_names():
    """--a  --b  ...  --z  --aa  ..."""
    letters = string.ascii_lowercase
    length = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '--' + ''.join(combo)
        length += 1

def gen_primitive_names():
    """$_a_  $_b_  ...  $_z_  $_aa_  ..."""
    letters = string.ascii_lowercase
    length = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '$_' + ''.join(combo) + '_'
        length += 1


# ── Value helpers ─────────────────────────────────────────────────────

def normalize(v):
    return re.sub(r'\s+', ' ', v.strip().lower())

COLOR_VALUE_RE = re.compile(
    r'^(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\)|'
    r'black|white|red|blue|green|yellow|orange|purple|pink|'
    r'gray|grey|navy|teal|silver|gold|lime|aqua|fuchsia|maroon|olive|'
    r'currentColor)$',
    re.IGNORECASE
)
SHADOW_PROPS = {'box-shadow', 'text-shadow'}

def is_color_value(v):
    return bool(COLOR_VALUE_RE.match(v.strip()))


# ── Naming helpers ────────────────────────────────────────────────────

def last_word(name):
    name = name.strip()
    if name.startswith('['):
        return 'unknown'
    if re.match(r'^inline-style-', name):
        return 'unknown'
    if re.match(r'^#?\d+$', name):
        return 'unknown'
    name = re.sub(r'\.(jsx|tsx|ts|js|css|scss|html)$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'::?[\w-]+', '', name)
    name = re.sub(r'^[.#]+', '', name)
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', '-', name)
    parts = re.split(r'[\s\-_.\>\+\~]+', name)
    parts = [p for p in parts if p and not re.match(r'^\d+$', p)]
    return parts[-1].lower() if parts else 'unknown'

def split_label(label):
    if ' / ' in label:
        parts = label.split(' / ', 1)
        left, right = parts[0].strip(), parts[1].strip()
        if right.startswith('[') or re.match(r'^inline-style-', right):
            return left, None
        return right, left
    if label.startswith('['):
        return label, None
    return label, None

def make_alias(source_name, prop):
    lw = last_word(source_name)
    return f'${lw}-{prop.strip().lower()}'


# ── Shadow helpers ────────────────────────────────────────────────────

def split_shadow_layers(raw):
    """Split 'a, b, c' respecting parens."""
    layers, depth, cur = [], 0, ''
    for ch in raw:
        if ch == '(':   depth += 1; cur += ch
        elif ch == ')': depth -= 1; cur += ch
        elif ch == ',' and depth == 0:
            layers.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): layers.append(cur.strip())
    return layers

def split_layer_parts(layer):
    """Split one layer into (position_string, color_token_or_None)."""
    tokens, depth, cur = [], 0, ''
    for ch in layer:
        if ch == '(':   depth += 1; cur += ch
        elif ch == ')': depth -= 1; cur += ch
        elif ch == ' ' and depth == 0:
            if cur.strip(): tokens.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): tokens.append(cur.strip())
    if tokens and is_color_value(tokens[-1]):
        return ' '.join(tokens[:-1]), tokens[-1]
    return ' '.join(tokens), None


# ── Pipeline parser ───────────────────────────────────────────────────

def parse_pipeline(path):
    """
    Parse pipeline-colors-shadows.txt.
    Returns:
      theme:   {'type': str, 'themes': {name: {--var: value}}}
      entries: list of (prop, value, source_label)
    """
    text    = path.read_text(encoding='utf-8', errors='ignore')
    lines   = text.splitlines()

    theme        = {'type': 'none', 'themes': {}}
    entries      = []
    in_theme     = False
    in_data      = False
    current_theme_name = None
    current_source     = None

    i = 0
    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        # Skip box-drawing and empty
        if not s or s.startswith('╔') or s.startswith('║') or s.startswith('╚'):
            i += 1; continue

        # Theme block start
        if s.startswith('THEME:'):
            theme['type'] = s.split(':', 1)[1].strip()
            in_theme = True
            in_data  = False
            i += 1; continue

        # Data block start
        if s == 'DATA':
            in_theme = False
            in_data  = True
            i += 1; continue

        # Separator lines
        if set(s) <= set('─═ '):
            i += 1; continue

        # Skip column headers
        if s.startswith('PROPERTY') or s.startswith('Generated') or s.startswith('Entries'):
            i += 1; continue

        if in_theme:
            # Theme name line: "── light"
            m = re.match(r'^──\s+(\w+)', s)
            if m:
                current_theme_name = m.group(1).strip()
                if current_theme_name not in theme['themes']:
                    theme['themes'][current_theme_name] = {}
                i += 1; continue
            # CSS var line: "--primary: #ffffff;"
            m = re.match(r'^(--[\w-]+)\s*:\s*(.+?);?$', s)
            if m and current_theme_name:
                theme['themes'][current_theme_name][m.group(1).strip()] = m.group(2).strip()
            i += 1; continue

        if in_data:
            # Source label line — sits between separator lines, no prop prefix
            # Data lines always start with a known property name
            known_props = {
                'color', 'background-color', 'border-color', 'border-top-color',
                'border-right-color', 'border-bottom-color', 'border-left-color',
                'outline-color', 'box-shadow', 'text-shadow', 'fill', 'stroke',
                'caret-color', 'text-decoration-color', 'column-rule-color',
                'background', 'font-color',
            }

            # Try to match a known prop at the start of the line
            prop = None
            rest_start = 0
            parts = s.split()
            for length in range(min(4, len(parts)), 0, -1):
                candidate = '-'.join(parts[:length]).lower()
                if candidate in known_props:
                    prop = candidate
                    rest_start = length
                    break

            if not prop:
                # Not a data line — treat as source label
                if s and not set(s) <= set('─═ '):
                    current_source = s
                i += 1; continue

            # Everything after the prop name is "value ... source"
            # Source is the LAST token that matches a known source pattern
            # (classname like pop-button, static-card, or "section / selector")
            # Value is everything in between
            # Strategy: source label was stored as the last field — but since
            # both value and source can have spaces, we stored source at the end
            # matching the source we set from the separator line above
            remainder = ' '.join(parts[rest_start:]).strip()

            # The source label is repeated at the end of each line
            # Try to find it by matching current_source at the end
            value = remainder
            source = current_source or 'unknown'

            if current_source and remainder.endswith(current_source):
                value = remainder[:-len(current_source)].strip()
                source = current_source
            elif ' ' in remainder:
                # Fall back: last whitespace-separated token is the source
                rtokens = remainder.rsplit(None, 1)
                if len(rtokens) == 2:
                    value  = rtokens[0].strip()
                    source = rtokens[1].strip()

            if value and prop:
                entries.append((prop, value, source))

        i += 1

    return theme, entries


# ── Root block builder ────────────────────────────────────────────────

def build_root_block(theme, var_map):
    """
    Build the :root / themed CSS block.

    If theme type is 'data-theme': [data-theme="light"] { } [data-theme="dark"] { }
    If theme type is 'root-class': :root.light { } :root.dark { }
    If theme type is 'media':      @media (prefers-color-scheme: dark) { :root { } }
    If theme type is 'root'/'none': :root { }

    var_map: norm_value → --varname  (the colors we actually found in the scan)
    theme:   {'type': str, 'themes': {name: {--css-var: raw_value}}}
    """
    lines = []

    t_type  = theme['type']
    themes  = theme['themes']

    if t_type in ('data-theme', 'root-class', 'media') and themes:
        for theme_name, css_vars in themes.items():
            if t_type == 'data-theme':
                selector = f':root[data-theme="{theme_name}"]'
            elif t_type == 'root-class':
                selector = f':root.{theme_name}'
            else:
                selector = f'@media (prefers-color-scheme: {theme_name}) {{\n  :root'

            lines.append(f'{selector} {{')
            # Write the theme's own --css-vars
            for var, val in css_vars.items():
                lines.append(f'  {var}: {val};')
            lines.append('}')
            if t_type == 'media':
                lines.append('}')
            lines.append('')
        # After themed blocks, add :root with our generated --a --b --c aliases
        # for any scanned color values not already covered by theme vars
        lines.append(':root {')
        max_len = max((len(v) for v in var_map.values()), default=4)
        for norm, var in var_map.items():
            # Find the display value
            pad = max(1, max_len + 4 - len(var))
            lines.append(f'  {var}:{" " * pad}/* mapped color */;')
        lines.append('}')
    else:
        # Plain :root
        lines.append(':root {')
        max_len = max((len(v) for v in var_map.values()), default=4)
        # If theme has a root block, write those vars
        root_vars = themes.get('root', {})
        for css_var, val in root_vars.items():
            lines.append(f'  {css_var}: {val};')
        if root_vars:
            lines.append('')
        # Our generated aliases
        for norm, var in var_map.items():
            pad = max(1, max_len + 4 - len(var))
            lines.append(f'  {var}:{" " * pad}{norm};')
        lines.append('}')

    return lines


# ── Main builder ──────────────────────────────────────────────────────

def build_colors_scss(theme, entries, output_path):
    """
    Build and write colors.scss.

    theme:   parsed theme dict
    entries: list of (prop, value, source_label)
    """
    # ── Step 1: collect all unique color values → assign --a --b --c
    unique_colors = OrderedDict()
    shadow_entries_by_source = OrderedDict()  # source → [(pos, color_token)]

    for prop, value, source in entries:
        if prop in SHADOW_PROPS:
            # Decompose shadow layers
            for layer in split_shadow_layers(value):
                pos, color = split_layer_parts(layer)
                if color:
                    norm = normalize(color)
                    if norm not in unique_colors:
                        unique_colors[norm] = color.strip()
                shadow_entries_by_source.setdefault(source, []).append((pos, color))
        else:
            norm = normalize(value)
            if norm not in unique_colors:
                unique_colors[norm] = value.strip()

    var_gen = gen_var_names()
    var_map = {norm: next(var_gen) for norm in unique_colors}  # norm → --a

    # ── Step 2: collect shadow position values → $_a_ primitives
    unique_positions = OrderedDict()
    for layers in shadow_entries_by_source.values():
        for pos, color in layers:
            if pos:
                norm = normalize(pos)
                if norm and norm not in unique_positions:
                    unique_positions[norm] = pos.strip()

    prim_gen = gen_primitive_names()
    prim_map = {norm: next(prim_gen) for norm in unique_positions}  # norm → $_a_

    # ── Step 3: group all non-shadow entries by source
    by_source = OrderedDict()
    for prop, value, source in entries:
        if prop not in SHADOW_PROPS:
            by_source.setdefault(source, []).append((prop, value))

    # Add shadow sources
    for source in shadow_entries_by_source:
        if source not in by_source:
            by_source[source] = []

    # ── Step 4: build aliases per source
    # source → list of (alias, rendered_value, comment)
    source_aliases  = OrderedDict()
    shadow_combined = []   # for $shadow-N index

    seen_aliases = {}

    for source in sorted(by_source.keys()):
        class_name, header = split_label(source)
        alias_list = []

        # Non-shadow color props
        for prop, value in by_source.get(source, []):
            norm  = normalize(value)
            var   = var_map.get(norm)
            if not var:
                continue
            alias = make_alias(class_name, prop)

            # Dedup: same alias + same var → skip
            if alias in seen_aliases:
                if seen_aliases[alias] == var:
                    continue
                else:
                    base, count = alias, 2
                    while alias in seen_aliases and seen_aliases[alias] != var:
                        alias = f'{base}-{count}'; count += 1
                    if alias in seen_aliases:
                        continue

            seen_aliases[alias] = var
            comment = f'  // {header}' if header else ''
            alias_list.append((alias, f'var({var})', comment))

        # Shadow props for this source
        if source in shadow_entries_by_source:
            layers     = shadow_entries_by_source[source]
            layer_parts = []
            for pos, color in layers:
                prim = prim_map.get(normalize(pos), '') if pos else ''
                var  = var_map.get(normalize(color), '') if color else ''
                if prim and var:   layer_parts.append(f'{prim} var({var})')
                elif prim:         layer_parts.append(prim)
                elif var:          layer_parts.append(f'var({var})')

            if layer_parts:
                rendered = ', '.join(layer_parts)
                alias    = make_alias(class_name, 'box-shadow')

                if alias in seen_aliases:
                    if seen_aliases[alias] == rendered:
                        pass  # already there
                    else:
                        base, count = alias, 2
                        while alias in seen_aliases and seen_aliases[alias] != rendered:
                            alias = f'{base}-{count}'; count += 1

                if alias not in seen_aliases:
                    seen_aliases[alias] = rendered
                    comment = f'  // {header}' if header else ''
                    alias_list.append((alias, rendered, comment))
                    shadow_combined.append(alias)

        if alias_list:
            source_aliases[source] = (class_name, alias_list)

    # ── Step 5: write the file
    lines = []
    lines.append(f'// {"═" * 67}')
    lines.append(f'// colors.scss')
    lines.append(f'// Master color + shadow contract for the entire system.')
    lines.append(f'// All color aliases and shadow aliases live here.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_colors.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Root block
    lines.extend(build_root_block(theme, var_map))
    lines.append('')

    # Per-component sections
    lines.append(f'// {"─" * 67}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 67}')

    max_alias_len = max((len(a) for src, (cn, al) in source_aliases.items() for a, _, _ in al), default=10)

    for source, (class_name, alias_list) in source_aliases.items():
        lw = last_word(class_name)
        lines.append('')
        lines.append(f'// ── {lw.upper()} {"─" * max(1, 56 - len(lw))}')
        for alias, rendered, comment in alias_list:
            pad = max(1, max_alias_len + 4 - len(alias))
            lines.append(f'{alias}:{" " * pad}{rendered};{comment}')

    lines.append('')
    lines.append('')

    # Shadow position primitives
    if prim_map:
        lines.append(f'// {"─" * 67}')
        lines.append(f'// Box-shadow position primitives')
        lines.append(f'// {"─" * 67}')
        lines.append('')
        max_prim = max(len(p) for p in prim_map.values())
        for norm, prim in prim_map.items():
            pad = max(1, max_prim + 4 - len(prim))
            lines.append(f'{prim}:{" " * pad}{unique_positions[norm]};')
        lines.append('')
        lines.append('')

    # Shadow index
    if shadow_combined:
        lines.append(f'// {"─" * 67}')
        lines.append(f'// Shadow index')
        lines.append(f'// {"─" * 67}')
        lines.append('')
        for i, alias in enumerate(shadow_combined, 1):
            lines.append(f'$shadow-{i}:  {alias};')
        lines.append('')

    output_path.write_text('\n'.join(lines), encoding='utf-8')
    return len(unique_colors), len(shadow_combined)


# ── Prompts ───────────────────────────────────────────────────────────

PIPELINE_DIR = Path.cwd() / 'scripts' / 'data' / 'audits' / 'parameters'

def prompt_pipeline():
    print()
    print(f'  Looking in: {PIPELINE_DIR}')

    if not PIPELINE_DIR.exists():
        print('  ✗ Parameters directory not found. Run scan_params.py first.')
        sys.exit(1)

    reports = sorted(PIPELINE_DIR.glob('pipeline-colors-shadows*.txt'))
    if not reports:
        print('  ✗ No pipeline-colors-shadows file found. Run scan_params.py first.')
        sys.exit(1)

    print()
    print('  Available pipeline files:\n')
    for i, r in enumerate(reports):
        print(f'  [{i+1}]  {r.name}')

    print()
    choice = input('  Enter number (or Enter for most recent): ').strip()
    if not choice:
        return sorted(reports)[-1]
    try:
        return reports[int(choice) - 1]
    except (ValueError, IndexError):
        print('  Invalid — using most recent.')
        return sorted(reports)[-1]


def prompt_output_dir():
    print()
    print('  Where should colors.scss be written?')
    print(f'  Default: {Path.cwd() / "styles"}')
    print()
    raw = input('  Path (or Enter for default): ').strip().strip('"').strip("'")
    base = Path(raw) if raw else Path.cwd() / 'styles'
    base.mkdir(parents=True, exist_ok=True)
    return base


# ── Run ───────────────────────────────────────────────────────────────

def run():
    print()
    print('╔══════════════════════════════════════════════════════╗')
    print('║         colors + shadows writer                      ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    print('  Reads pipeline-colors-shadows.txt from scan_params.py')
    print('  Writes a single colors.scss with:')
    print('    :root block  →  --a, --b, --c color variables')
    print('    per-component sections  →  $alias: var(--x)')
    print('    shadow primitives  →  $_a_: 0px 4px 5px')
    print('    shadow index  →  $shadow-1: $button-box-shadow')

    pipeline_path = prompt_pipeline()
    print(f'\n  Using: {pipeline_path.name}\n')

    theme, entries = parse_pipeline(pipeline_path)

    if not entries:
        print('  ✗ No entries found in pipeline file.')
        sys.exit(1)

    print(f'  Found {len(entries)} entries')
    print(f'  Theme: {theme["type"]}')
    if theme['themes']:
        for name in theme['themes']:
            print(f'    ── {name}: {len(theme["themes"][name])} variables')
    print()

    output_dir  = prompt_output_dir()
    output_path = output_dir / 'colors.scss'

    color_count, shadow_count = build_colors_scss(theme, entries, output_path)

    print(f'\n  ✓ colors.scss written → {output_path}')
    print(f'    {color_count} unique color variables')
    print(f'    {shadow_count} shadow aliases')
    print()


if __name__ == '__main__':
    run()