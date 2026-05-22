#!/usr/bin/env python3
"""
generate_decs.py

Reads pipeline-decs.txt produced by scan_params.py.
Creates one .scss dec file per CSS property.

Non-shadow properties:
  $_a_: 16px;
  $_b_: 8px;
  $button-border-radius:  $_a_;
  $card-border-radius:    $_b_;

Shadow properties (no color — color-shadows handled by generate_colors.py):
  $_a_: 0px 4px 5px;
  $_b_: 3px 0px 0px;
  $button-box-shadow:  $_a_, $_b_;
  $card-box-shadow:    $_a_;

After writing, auto-moves eff-type files from decs/ into effs/.
"""

import re
import sys
import string
from pathlib import Path
from datetime import datetime
from collections import OrderedDict, defaultdict
from itertools import product


# ── Primitive name generator ──────────────────────────────────────────

def gen_primitive_names():
    letters = string.ascii_lowercase
    length  = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '$_' + ''.join(combo) + '_'
        length += 1


# ── Value helpers ─────────────────────────────────────────────────────

def normalize(v):
    return re.sub(r'\s+', ' ', v.strip().lower())

def is_scss_safe(value):
    v = value.strip()
    if not v:                                                    return False
    if '${' in v:                                               return False
    if '=>' in v:                                               return False
    if v.lower() in ('true', 'false'):                         return False
    if v.startswith('c.') or re.search(r'\bc\.[a-zA-Z]', v):  return False
    if 'COPIES' in v:                                           return False
    if re.search(r'\b(const|let|var|function|return)\b', v):   return False
    if re.search(r'[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(', v) and \
       not re.match(r'^(rgba?|hsla?|linear-gradient|radial-gradient|'
                    r'conic-gradient|calc|clamp|min|max|env|var)\s*\(', v):
        return False
    return True


# ── Naming helpers ────────────────────────────────────────────────────

def last_word(name):
    name = name.strip()
    if name.startswith('['):                 return 'unknown'
    if re.match(r'^inline-style-', name):   return 'unknown'
    if re.match(r'^#?\d+$', name):          return 'unknown'
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

SHADOW_PROPS = {'box-shadow', 'text-shadow'}

def split_shadow_layers(raw):
    """Split shadow value into layers, respecting parens."""
    layers, depth, cur = [], 0, ''
    for ch in raw:
        if ch == '(':   depth += 1; cur += ch
        elif ch == ')': depth -= 1; cur += ch
        elif ch == ',' and depth == 0:
            layers.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): layers.append(cur.strip())
    return layers


# ── Valid CSS properties ──────────────────────────────────────────────

VALID_CSS_PROPS = {
    "margin","margin-top","margin-right","margin-bottom","margin-left",
    "padding","padding-top","padding-right","padding-bottom","padding-left",
    "width","height","min-width","max-width","min-height","max-height",
    "box-sizing","overflow","overflow-x","overflow-y",
    "display","position","top","right","bottom","left",
    "float","clear","z-index","visibility","opacity",
    "flex","flex-direction","flex-wrap","flex-grow","flex-shrink","flex-basis",
    "justify-content","justify-items","justify-self",
    "align-items","align-content","align-self",
    "gap","row-gap","column-gap",
    "grid-template-columns","grid-template-rows","grid-area",
    "grid-column","grid-row","grid-auto-columns","grid-auto-rows",
    "font","font-family","font-size","font-weight","font-style","font-variant",
    "line-height","letter-spacing","word-spacing",
    "text-align","text-decoration","text-transform","text-indent",
    "text-overflow","white-space","word-break","vertical-align",
    "background","background-color","background-image","background-size",
    "background-position","background-repeat","background-attachment",
    "filter","backdrop-filter","mix-blend-mode",
    "border","border-top","border-right","border-bottom","border-left",
    "border-width","border-style","border-color","border-radius",
    "border-top-left-radius","border-top-right-radius",
    "border-bottom-left-radius","border-bottom-right-radius",
    "border-image","outline","outline-offset","outline-width",
    "box-shadow","text-shadow",
    "transform","transform-origin",
    "transition","transition-property","transition-duration",
    "transition-timing-function","transition-delay",
    "animation","animation-name","animation-duration",
    "animation-timing-function","animation-delay",
    "animation-iteration-count","animation-direction",
    "animation-fill-mode","animation-play-state",
    "cursor","pointer-events","user-select","resize","appearance",
    "touch-action","scroll-behavior","will-change",
    "content","clip-path","object-fit","object-position",
    "aspect-ratio","isolation","list-style","list-style-type",
    "fill","stroke","stroke-width","fill-opacity","stroke-opacity",
    "inset","flex-flow","order","place-items","place-content","place-self",
    "grid","grid-template","grid-auto-flow",
    "margin-inline","margin-block","padding-inline","padding-block",
    "scroll-behavior","overscroll-behavior","scrollbar-width",
}

def is_valid_css_prop(prop):
    p = prop.strip().lower()
    if p.startswith('--'): return True
    return p in VALID_CSS_PROPS


# ── Pipeline parser ───────────────────────────────────────────────────

def parse_pipeline(path):
    """
    Parse pipeline-decs.txt.
    Returns list of (prop, value, source_label).
    """
    text    = path.read_text(encoding='utf-8', errors='ignore')
    entries = []
    current_source = None
    in_data        = True   # decs pipeline has no theme block

    for line in text.splitlines():
        s = line.strip()

        if not s: continue
        if s.startswith('╔') or s.startswith('║') or s.startswith('╚'): continue
        if set(s) <= set('─═ '): continue
        if s.startswith('Generated') or s.startswith('Entries') or \
           s.startswith('PROPERTY') or s == 'DATA': continue

        # Try to match a known prop at the start
        parts = s.split()
        prop  = None
        rest_start = 0
        for length in range(min(4, len(parts)), 0, -1):
            candidate = '-'.join(parts[:length]).lower()
            if is_valid_css_prop(candidate):
                prop       = candidate
                rest_start = length
                break

        if not prop:
            # Not a data line — treat as source label
            if s and not set(s) <= set('─═ '):
                current_source = s
            continue

        # Extract value — source label repeated at end, strip it
        remainder = ' '.join(parts[rest_start:]).strip()
        value  = remainder
        source = current_source or 'unknown'

        if current_source and remainder.endswith(current_source):
            value = remainder[:-len(current_source)].strip()
        elif ' ' in remainder:
            rtokens = remainder.rsplit(None, 1)
            if len(rtokens) == 2:
                value  = rtokens[0].strip()
                source = rtokens[1].strip()

        if value and is_scss_safe(value):
            entries.append((prop, value, source))

    return entries


# ── Dec file builder ──────────────────────────────────────────────────

def build_dec_file(prop, entries):
    """
    Build a single .scss dec file for one property.
    entries: list of (value, source_label)

    For shadow properties: decomposes multi-layer values into
    position primitives then recombines with alias names.
    No var(--x) — those shadows are in generate_colors.py.

    For all other properties: standard $_a_ primitive + alias system.
    """
    prop_kebab = prop.strip().lower()

    if prop in SHADOW_PROPS:
        return _build_shadow_dec(prop_kebab, entries)
    else:
        return _build_standard_dec(prop_kebab, entries)


def _build_standard_dec(prop, entries):
    """Standard $_a_ primitive + component alias file."""

    # Unique values in order of first appearance
    unique_vals = OrderedDict()
    for value, source in entries:
        norm = normalize(value)
        if norm and norm not in unique_vals:
            unique_vals[norm] = value.strip()

    if not unique_vals:
        return None

    name_gen = gen_primitive_names()
    prim_map = {norm: next(name_gen) for norm in unique_vals}

    # Build aliases — dedup by alias+primitive
    aliases      = OrderedDict()   # alias → (primitive, header)
    seen_aliases = {}

    for value, source in entries:
        norm = normalize(value)
        if norm not in prim_map:
            continue
        class_name, header = split_label(source)
        alias = make_alias(class_name, prop)
        prim  = prim_map[norm]

        if alias in seen_aliases:
            if seen_aliases[alias] == prim:
                continue   # exact duplicate
            else:
                base, count = alias, 2
                while alias in seen_aliases and seen_aliases[alias] != prim:
                    alias = f'{base}-{count}'; count += 1
                if alias in seen_aliases:
                    continue

        seen_aliases[alias] = prim
        aliases[alias]      = (prim, header)

    if not aliases:
        return None

    lines = []
    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/{prop}.scss')
    lines.append(f'// Every {prop} value in the system.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Primitives
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Raw values')
    lines.append(f'// {"─" * 44}')
    lines.append('')
    max_prim = max(len(p) for p in prim_map.values())
    for norm, prim in prim_map.items():
        pad = max(1, max_prim + 4 - len(prim))
        lines.append(f'{prim}:{" " * pad}{unique_vals[norm]};')
    lines.append('')
    lines.append('')

    # Aliases grouped by source word
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 44}')
    lines.append('')

    source_groups = defaultdict(list)
    for alias, (prim, header) in aliases.items():
        fw = alias.lstrip('$').split('-')[0]
        source_groups[fw].append((alias, prim, header))

    max_alias = max(len(a) for a in aliases)
    for fw, alias_list in sorted(source_groups.items()):
        headers = [h for _, _, h in alias_list if h]
        label   = f'{fw.upper()} — {headers[0]}' if headers else fw.upper()
        lines.append(f'// ── {label} {"─" * max(1, 56 - len(label))}')
        for alias, prim, header in sorted(alias_list):
            pad     = max(1, max_alias + 4 - len(alias))
            comment = f'  // {header}' if header else ''
            lines.append(f'{alias}:{" " * pad}{prim};{comment}')
        lines.append('')

    return '\n'.join(lines)


def _build_shadow_dec(prop, entries):
    """
    Shadow dec file — no colors, pure position/blur/spread only.
    Decomposes multi-layer values into $_a_ primitives,
    then builds combined aliases: $button-box-shadow: $_a_, $_b_;
    """
    # Collect per-source layer lists
    source_layers = OrderedDict()
    for value, source in entries:
        layers = split_shadow_layers(value)
        for layer in layers:
            source_layers.setdefault(source, []).append(layer.strip())

    # Collect all unique position strings → primitives
    unique_positions = OrderedDict()
    for layers in source_layers.values():
        for pos in layers:
            norm = normalize(pos)
            if norm and norm not in unique_positions:
                unique_positions[norm] = pos

    if not unique_positions:
        return None

    name_gen = gen_primitive_names()
    prim_map = {norm: next(name_gen) for norm in unique_positions}

    # Build aliases
    seen_aliases = {}
    combined     = OrderedDict()   # alias → rendered

    for source, layers in source_layers.items():
        class_name, header = split_label(source)
        alias       = make_alias(class_name, prop)
        layer_parts = [prim_map.get(normalize(l), '') for l in layers]
        layer_parts = [p for p in layer_parts if p]
        if not layer_parts:
            continue
        rendered = ', '.join(layer_parts)

        if alias in seen_aliases:
            if seen_aliases[alias] == rendered:
                continue
            else:
                base, count = alias, 2
                while alias in seen_aliases and seen_aliases[alias] != rendered:
                    alias = f'{base}-{count}'; count += 1
                if alias in seen_aliases:
                    continue

        seen_aliases[alias] = rendered
        combined[alias]     = (rendered, header)

    if not combined:
        return None

    lines = []
    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/{prop}.scss')
    lines.append(f'// Every {prop} in the system — position/blur/spread only.')
    lines.append(f'// (Shadows with color live in colors.scss)')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Primitives
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Raw position / blur / spread values')
    lines.append(f'// {"─" * 44}')
    lines.append('')
    max_prim = max(len(p) for p in prim_map.values())
    for norm, prim in prim_map.items():
        pad = max(1, max_prim + 4 - len(prim))
        lines.append(f'{prim}:{" " * pad}{unique_positions[norm]};')
    lines.append('')
    lines.append('')

    # Aliases
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 44}')
    lines.append('')
    max_alias = max(len(a) for a in combined)
    for alias, (rendered, header) in combined.items():
        fw      = alias.lstrip('$').split('-')[0]
        pad     = max(1, max_alias + 4 - len(alias))
        comment = f'  // {header}' if header else ''
        lines.append(f'// ── {fw.upper()} {"─" * max(1, 56 - len(fw))}')
        lines.append(f'{alias}:{" " * pad}{rendered};{comment}')
        lines.append('')

    return '\n'.join(lines)


# ── Eff mover ─────────────────────────────────────────────────────────

EFF_PROPS = {
    "transform","transform-origin",
    "transition","transition-property","transition-duration",
    "transition-delay","transition-timing-function",
    "animation","animation-name","animation-duration",
    "animation-delay","animation-timing-function",
    "animation-iteration-count","animation-direction",
    "animation-fill-mode","animation-play-state",
    "flex","flex-direction","flex-wrap","flex-grow",
    "flex-shrink","flex-basis","flex-flow","order",
    "place-items","place-content","place-self",
    "cursor","pointer-events","user-select","appearance",
    "touch-action","resize","will-change",
    "opacity","isolation","visibility",
    "scroll-behavior","scroll-snap-type","scroll-snap-align",
    "overscroll-behavior",
    "outline","outline-width","outline-offset",
    "background-size","background-position",
    "background-attachment","background-repeat",
    "object-fit","object-position",
    "clip-path","offset-path",
}

def move_effs(decs_dir):
    effs_dir = decs_dir.parent / 'effs'
    effs_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for scss_file in sorted(decs_dir.glob('*.scss')):
        if scss_file.stem.lower() in EFF_PROPS:
            scss_file.rename(effs_dir / scss_file.name)
            moved += 1
            print(f'  → effs/  {scss_file.name}')
    if moved:
        print(f'\n  ✓ Moved {moved} eff file{"s" if moved != 1 else ""} → {effs_dir}')
    else:
        print(f'  –  No eff files to move')


# ── Prompts ───────────────────────────────────────────────────────────

PIPELINE_DIR = Path.cwd() / 'scripts' / 'data' / 'audits' / 'parameters'

def prompt_pipeline():
    print()
    print(f'  Looking in: {PIPELINE_DIR}')
    if not PIPELINE_DIR.exists():
        print('  ✗ Parameters directory not found. Run scan_params.py first.')
        sys.exit(1)
    reports = sorted(PIPELINE_DIR.glob('pipeline-decs*.txt'))
    if not reports:
        print('  ✗ No pipeline-decs file found. Run scan_params.py first.')
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
    print('  Where should the styles folder be created?')
    print('  (decs/ and effs/ will be placed inside this directory)')
    print(f'  Default: {Path.cwd() / "styles"}')
    print()
    raw = input('  Path (or Enter for default): ').strip().strip('"').strip("'")
    base = Path(raw) if raw else Path.cwd() / 'styles'
    if base.name in ('decs', 'effs'):
        base = base.parent
    decs_dir = base / 'decs'
    effs_dir = base / 'effs'
    decs_dir.mkdir(parents=True, exist_ok=True)
    effs_dir.mkdir(parents=True, exist_ok=True)
    print(f'\n  ✓ Output root → {base}')
    print(f'    decs/ and effs/ ready\n')
    return decs_dir


# ── Run ───────────────────────────────────────────────────────────────

def run():
    print()
    print('╔══════════════════════════════════════════════════════╗')
    print('║         dec file generator                           ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    print('  Reads pipeline-decs.txt from scan_params.py')
    print('  Writes one .scss file per CSS property')
    print('  Auto-moves eff files into effs/ when done')

    pipeline_path = prompt_pipeline()
    print(f'\n  Using: {pipeline_path.name}\n')

    entries = parse_pipeline(pipeline_path)

    if not entries:
        print('  ✗ No entries found in pipeline file.')
        sys.exit(1)

    print(f'  Found {len(entries)} entries across '
          f'{len(set(e[0] for e in entries))} properties.\n')

    output_dir = prompt_output_dir()
    generated  = 0

    # Group by property
    by_prop = defaultdict(list)
    for prop, value, source in entries:
        by_prop[prop].append((value, source))

    for prop, prop_entries in sorted(by_prop.items()):
        content = build_dec_file(prop, prop_entries)
        if not content:
            print(f'  –  {prop}.scss  (no usable values)')
            continue
        filename = prop + '.scss'
        out_path = output_dir / filename
        out_path.write_text(content, encoding='utf-8')
        generated += 1
        unique_count = len(set(normalize(v) for v, _ in prop_entries))
        print(f'  ✓  {filename:<40} {unique_count} unique values')

    print(f'\n  ✓ Generated {generated} dec files')
    print(f'  ✓ Saved to → {output_dir}\n')

    # Auto-move effs
    print('  Moving eff files...\n')
    move_effs(output_dir)
    print()


if __name__ == '__main__':
    run()