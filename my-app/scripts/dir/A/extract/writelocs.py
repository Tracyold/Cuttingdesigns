"""
scss_dec_sub.py — Dec/Eff Value Substitution
─────────────────────────────────────────────────────────────────
Reads a plain text file containing already-separated CSS blocks
in this format:

  ══════════...
    ── section name
  ══════════...
    .classname {
      property: value
      property: value
    }

Loads all dec/eff .scss files, builds a reverse lookup map of
raw value → $name, then writes one .scss file per section with
every matched value replaced by its $name.

Unmatched values are flagged with  // ⚠ no dec found
and a _unmatched.txt report is written to the output folder.

Usage:
    python3 scss_dec_sub.py
─────────────────────────────────────────────────────────────────
"""

import re
import os
from pathlib import Path
from collections import defaultdict


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def slugify(text: str) -> str:
    text = re.split(r'[—–]', text)[0]
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    text = text.lower().strip()
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def normalise(value: str) -> str:
    v = value.strip().rstrip(';')
    v = re.sub(r'\s*//.*$', '', v).strip()
    v = re.sub(r'\s+', ' ', v)
    return v.lower()


# Values never substituted — universal CSS keywords with no dec meaning
SKIP_SUB = {
    'inherit', 'initial', 'unset', 'revert',
    'currentcolor', '100%', '100vh', '100vw',
    '-webkit-fill-available',
}

# Properties where same keyword (none, auto, 0) means different things
AMBIGUOUS = {
    'display', 'overflow', 'overflow-x', 'overflow-y',
    'cursor', 'position', 'transition', 'transform',
    'text-decoration', 'text-align', 'white-space',
    'resize', 'user-select', 'will-change',
    'flex', 'flex-direction', 'flex-shrink', 'flex-wrap',
    'align-items', 'justify-content',
    'pointer-events', 'touch-action', 'visibility',
}

# $name prefix → CSS property (for prop-scoped lookup)
PREFIX_TO_PROP = {
    'cursor':         'cursor',
    'display':        'display',
    'overflow-scroll-y': 'overflow-y',
    'overflow-scroll-x': 'overflow-x',
    'overflow-hidden': 'overflow',
    'overflow-strip':  'overflow',
    'overflow-store':  'overflow-y',
    'overflow-panel':  'overflow',
    'scrollbar':       'scrollbar-width',
    'pos-':            'position',
    'flex-dir':        'flex-direction',
    'flex-align':      'align-items',
    'flex-justify':    'justify-content',
    'flex-shrink':     'flex-shrink',
    'whitespace':      'white-space',
    'resize':          'resize',
    'select':          'user-select',
    'will-change':     'will-change',
    'touch':           'touch-action',
    'pointer':         'pointer-events',
    'transform':       'transform',
    'align':           'align-items',
    'justify':         'justify-content',
}


# ══════════════════════════════════════════════════════════════════
# DEC / EFF LOADER
# ══════════════════════════════════════════════════════════════════

def load_dec_files(directories: list) -> tuple:
    """
    Parse all .scss files in the given directories.
    Returns:
        value_map   — { norm_value: $name }      generic lookup
        prop_scoped — { "prop|norm_value": $name } scoped lookup
    """
    value_map   = {}
    prop_scoped = {}

    var_re = re.compile(r'^\s*(\$[\w-]+)\s*:\s*(.+?)\s*;', re.MULTILINE)

    for directory in directories:
        if not directory:
            continue
        dec_dir = Path(directory)
        if not dec_dir.is_dir():
            print(f'  ⚠  Directory not found, skipping: {directory}')
            continue

        for scss_file in sorted(dec_dir.glob('**/*.scss')):
            try:
                text = scss_file.read_text(encoding='utf-8')
            except Exception as e:
                print(f'  ⚠  Could not read {scss_file}: {e}')
                continue

            for m in var_re.finditer(text):
                name = m.group(1).strip()
                raw  = re.sub(r'\s*//.*$', '', m.group(2)).strip()

                # Skip private raw vars ($_a, $_b …)
                if re.match(r'^\$_', name):
                    continue

                norm = normalise(raw)
                if not norm:
                    continue

                # Generic map — first match wins
                if norm not in value_map:
                    value_map[norm] = name

                # Prop-scoped map — infer property from $name
                name_body = name.lstrip('$')
                for prefix, prop in PREFIX_TO_PROP.items():
                    if name_body.startswith(prefix):
                        key = f'{prop}|{norm}'
                        if key not in prop_scoped:
                            prop_scoped[key] = name
                        break

    return value_map, prop_scoped


# ══════════════════════════════════════════════════════════════════
# TEXT FILE PARSER
# Reads the already-separated block format:
#
#   ══════════...
#     ── section name
#   ══════════...
#     .classname {
#       property: value
#     }
# ══════════════════════════════════════════════════════════════════

def parse_block_file(text: str) -> list:
    """
    Returns a list of sections:
        [
          {
            'name': 'phone shell',
            'slug': 'phone-shell',
            'blocks': [
              {
                'selector': '.home',
                'props': [('flex', '1'), ('overflow-y', 'auto'), ...]
              },
              ...
            ]
          },
          ...
        ]
    """
    sections  = []
    current   = None
    lines     = text.split('\n')
    i         = 0

    # regex patterns
    divider_re  = re.compile(r'^[\s═]+$')
    section_re  = re.compile(r'^\s*──\s+(.+)')
    selector_re = re.compile(r'^\s*(\S[^{]*)\{')
    prop_re     = re.compile(r'^\s+([a-zA-Z][a-zA-Z0-9-]*):\s*(.+)')
    close_re    = re.compile(r'^\s*\}')

    while i < len(lines):
        line = lines[i]

        # ── Section header ─────────────────────────────────────────
        m = section_re.match(line)
        if m:
            name = m.group(1).strip()
            current = {'name': name, 'slug': slugify(name), 'blocks': []}
            sections.append(current)
            i += 1
            continue

        # ── Block opener ───────────────────────────────────────────
        m = selector_re.match(line)
        if m and current is not None:
            selector = m.group(1).strip()
            props    = []
            i += 1

            while i < len(lines):
                pline = lines[i]

                if close_re.match(pline):
                    i += 1
                    break

                pm = prop_re.match(pline)
                if pm:
                    prop  = pm.group(1).strip()
                    value = pm.group(2).strip().rstrip(';')
                    props.append((prop, value))

                i += 1

            if props:
                current['blocks'].append({'selector': selector, 'props': props})
            continue

        i += 1

    return sections


# ══════════════════════════════════════════════════════════════════
# SUBSTITUTION ENGINE
# ══════════════════════════════════════════════════════════════════

def substitute(prop: str, value: str, value_map: dict, prop_scoped: dict) -> tuple:
    """
    Returns (output_value, was_replaced: bool)
    """
    # Already a $variable or var() reference — leave it
    if value.startswith('$') or value.startswith('var('):
        return value, True

    # Hard zero — always stays as 0
    if value.strip() == '0':
        return value, True

    # Skip list
    norm = normalise(value)
    if norm in SKIP_SUB:
        return value, True

    # Prop-scoped lookup for ambiguous keywords
    if prop in AMBIGUOUS:
        key = f'{prop}|{norm}'
        if key in prop_scoped:
            return prop_scoped[key], True

    # Generic lookup
    if norm in value_map:
        return value_map[norm], True

    return value, False


# ══════════════════════════════════════════════════════════════════
# RENDERER
# ══════════════════════════════════════════════════════════════════

def render_section(section: dict, value_map: dict,
                   prop_scoped: dict, stats: dict,
                   unmatched_log: list) -> str:
    lines = [
        f'// ── {section["name"]}',
        '// Substituted by scss_dec_sub.py — review before use.',
        '',
    ]

    for block in section['blocks']:
        lines.append(f'{block["selector"]} {{')

        for prop, value in block['props']:
            result, matched = substitute(prop, value, value_map, prop_scoped)

            if matched and result != value:
                lines.append(f'  {prop}: {result};')
                stats['replaced'] += 1
            elif matched:
                lines.append(f'  {prop}: {value};')
                stats['kept'] += 1
            else:
                lines.append(f'  {prop}: {value}; // ⚠ no dec found')
                stats['unmatched'] += 1
                unmatched_log.append((section['name'], prop, value))

        lines.append('}')
        lines.append('')

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def prompt(label: str, optional: bool = False) -> str:
    suffix = ' (press enter to skip)' if optional else ''
    return input(f'{label}{suffix}: ').strip().strip('\'"')


def main() -> None:
    print()
    print('╔══════════════════════════════════════════════════════════╗')
    print('║   scss_dec_sub.py — Dec/Eff Value Substitution          ║')
    print('╚══════════════════════════════════════════════════════════╝')
    print()
    print('  Reads your extracted block text file, substitutes raw CSS')
    print('  values with $names from your dec/eff files, writes one')
    print('  .scss file per section to the output directory.')
    print()

    input_file = prompt('Path to block text file (.txt)')
    decs_dir   = prompt('Path to decs/ directory')
    effs_dir   = prompt('Path to effs/ directory', optional=True)
    output_dir = prompt('Path to write output .scss files')

    # ── Validate ──────────────────────────────────────────────────
    input_path = Path(input_file)
    if not input_path.is_file():
        print(f'\n✗  File not found: {input_file}')
        return

    if not Path(decs_dir).is_dir():
        print(f'\n✗  Decs directory not found: {decs_dir}')
        return

    # ── Load decs ─────────────────────────────────────────────────
    directories = [decs_dir]
    if effs_dir:
        directories.append(effs_dir)

    print(f'\n  Loading dec/eff files:')
    for d in directories:
        if d:
            count = len(list(Path(d).glob('**/*.scss')))
            print(f'    {d}  ({count} file{"s" if count != 1 else ""})')

    value_map, prop_scoped = load_dec_files(directories)

    print(f'\n  Lookup map:')
    print(f'    {len(value_map):>5}  generic   value → $name')
    print(f'    {len(prop_scoped):>5}  scoped    prop|value → $name')

    # ── Parse block file ──────────────────────────────────────────
    text     = input_path.read_text(encoding='utf-8')
    sections = parse_block_file(text)

    if not sections:
        print('\n✗  No sections found.')
        print('   Expected lines like:  ── section name')
        return

    print(f'\n  Found {len(sections)} section{"s" if len(sections) != 1 else ""}:\n')
    max_slug = max(len(s["slug"]) + 5 for s in sections)
    for s in sections:
        fn    = s['slug'] + '.scss'
        count = len(s['blocks'])
        print(f'  {fn:<{max_slug}}  {count} block{"s" if count != 1 else ""}')

    # ── Write output ──────────────────────────────────────────────
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print()
    stats        = {'replaced': 0, 'kept': 0, 'unmatched': 0}
    unmatched_log = []

    for section in sections:
        if not section['blocks']:
            print(f'  ─  {section["slug"]}.scss  (no blocks — skipped)')
            continue

        scss_text = render_section(section, value_map, prop_scoped,
                                   stats, unmatched_log)
        out_file  = out_path / f'{section["slug"]}.scss'
        out_file.write_text(scss_text, encoding='utf-8')

        r = sum(1 for s, p, v in unmatched_log if s == section['name'])
        print(f'  ✓  {section["slug"]}.scss')

    # ── Summary ───────────────────────────────────────────────────
    print()
    print(f'  ─────────────────────────────────────────────────────')
    print(f'  Sections written : {len([s for s in sections if s["blocks"]])}')
    print(f'  Values replaced  : {stats["replaced"]}')
    print(f'  Values kept      : {stats["kept"]}  (var(), $var, 0, keywords)')
    print(f'  Unmatched        : {stats["unmatched"]}  (flagged ⚠ in output)')
    print(f'  Output           : {out_path.resolve()}')

    # ── Unmatched report ──────────────────────────────────────────
    if unmatched_log:
        report = out_path / '_unmatched.txt'
        by_section = defaultdict(list)
        for sname, prop, value in unmatched_log:
            by_section[sname].append((prop, value))

        with open(report, 'w', encoding='utf-8') as f:
            f.write('# Unmatched values — no dec $name found\n')
            f.write('# Add these to your dec files then re-run.\n\n')
            for sname, entries in sorted(by_section.items()):
                f.write(f'## {sname}\n')
                for prop, value in sorted(set(entries)):
                    f.write(f'  {prop}: {value};\n')
                f.write('\n')

        print(f'\n  Unmatched report → _unmatched.txt')
        print(f'  Add missing $names to your dec files and re-run.')

    print()


if __name__ == '__main__':
    main()