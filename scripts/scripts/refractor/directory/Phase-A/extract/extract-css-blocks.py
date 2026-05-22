"""
AnatomyMatters — SCSS Block Extractor
──────────────────────────────────────
Reads a report file, parses every named style block grouped under
section headers, and writes one .scss file per section.

Usage:
    python3 extract_scss.py
"""

import re
import os


# ── Helpers ──────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Convert a human name into a valid CSS class name / filename slug."""
    # Drop anything after an em-dash  (e.g. "Profile overlay — rendered last …")
    text = re.split(r'[—–]', text)[0]
    # Keep alphanumeric, spaces, and hyphens (preserve existing kebab names)
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    text = text.lower().strip()
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def is_js_line(name: str) -> bool:
    """Return True when a block opener looks like JavaScript, not a CSS block."""
    js_prefixes = (
        'const ', 'let ', 'var ', 'if ', 'else ', 'return ',
        'setPos', 'setScale', 'setDragging', 'setStart', 'setFavs',
        'Array.', 'drw.', '); ', 'if(', '0%', '50%', '100%',
    )
    return any(name.startswith(p) for p in js_prefixes)


def fix_vendor_prefix(prop: str) -> str:
    """Restore the leading dash on webkit/moz/ms properties the report stripped."""
    if prop.startswith('webkit-') or prop.startswith('moz-') or prop.startswith('ms-'):
        return '-' + prop
    return prop


def needs_comment(prop: str) -> bool:
    """
    Return True for properties that look invalid — e.g. variable names
    used as property keys in the original JS object, garbled by extraction.
    """
    invalid = {
        'base-whisper', 'base-mute', 'accent', 'pulse-anim',
        'undefined', '0', '600', '20',
    }
    return prop in invalid or not re.match(r'^-?[a-zA-Z][a-zA-Z0-9-]*$', prop)


# ── Parser ───────────────────────────────────────────────────────────────────

Block = tuple  # (raw_name: str, comment: str | None, props: list[(str, str)])

def parse_report(text: str) -> list[tuple[str, list[Block]]]:
    """
    Walk the report line by line and return:
        [(section_name, [block, ...]), ...]
    where each block = (clean_name, side_comment, [(prop, value), ...])
    """
    sections: list[tuple[str, list]] = []
    current_section: str | None = None
    current_blocks: list = []

    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── Section header  ────────────────────────────────────────────────
        #   Matches:  "  ── Section Name"
        m = re.match(r'\s*──\s+(.+)', line)
        if m:
            if current_section is not None:
                sections.append((current_section, current_blocks))
            current_section = m.group(1).strip()
            current_blocks = []
            i += 1
            continue

        # ── Block opener  ──────────────────────────────────────────────────
        #   Matches:  "  BlockName {"  (exactly 2-space indent)
        m = re.match(r'^  (.+?)\s*\{$', line)
        if m and current_section is not None:
            raw_name = m.group(1).strip()

            # Skip JS blocks — read past their closing brace
            if is_js_line(raw_name):
                depth = 1
                i += 1
                while i < len(lines) and depth > 0:
                    depth += lines[i].count('{') - lines[i].count('}')
                    i += 1
                continue

            # Split off any trailing comment after an em-dash
            side_comment: str | None = None
            if '—' in raw_name or '–' in raw_name:
                parts = re.split(r'[—–]', raw_name, maxsplit=1)
                clean_name = parts[0].strip()
                side_comment = parts[1].strip()
            else:
                clean_name = raw_name

            # ── Collect properties until the closing "  }" ─────────────────
            props: list[tuple[str, str]] = []
            i += 1
            while i < len(lines):
                pline = lines[i]

                # Block ends at a line that is exactly "  }" (2-space indent)
                if re.match(r'^  \}\s*$', pline):
                    i += 1
                    break

                # Property line: 4+ spaces, then "prop: value"
                pm = re.match(r'^\s{4,}([a-zA-Z][a-zA-Z0-9 _-]*):\s+(.+)', pline)
                if pm:
                    props.append((pm.group(1).strip(), pm.group(2).strip()))

                i += 1

            if props:
                current_blocks.append((clean_name, side_comment, props))

            continue

        i += 1

    # Flush the last section
    if current_section is not None:
        sections.append((current_section, current_blocks))

    return sections


# ── Renderer ─────────────────────────────────────────────────────────────────

def render_scss(section_name: str, blocks: list[Block]) -> str:
    """Render all blocks in a section as a single .scss file."""
    out: list[str] = [
        f'// ── {section_name}',
        '// Generated by extract_scss.py — review values before use.',
        '',
    ]

    for clean_name, side_comment, props in blocks:
        class_name = slugify(clean_name)
        if not class_name:
            continue

        if side_comment:
            out.append(f'// {side_comment}')

        out.append(f'.{class_name} {{')

        for prop, val in props:
            prop = fix_vendor_prefix(prop)

            # Template literals reference JS variables — flag them
            if '${' in val:
                out.append(f'  // TODO: {prop}: {val};')

            # Garbled / non-CSS property names — flag them
            elif needs_comment(prop):
                out.append(f'  // INVALID: {prop}: {val};')

            else:
                out.append(f'  {prop}: {val};')

        out.append('}')
        out.append('')

    return '\n'.join(out)


def section_filename(section_name: str) -> str:
    spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', section_name)
    return slugify(spaced) + '.scss'


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print('╔══════════════════════════════════════════════════╗')
    print('║   AnatomyMatters — SCSS Block Extractor          ║')
    print('╚══════════════════════════════════════════════════╝')
    print()

    # ── Input file ───────────────────────────────────────────────────────────
    raw_input = input('Path to report file: ').strip().strip('"\'')

    if not os.path.isfile(raw_input):
        print(f'\n✗  File not found: {raw_input}')
        return

    with open(raw_input, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = parse_report(content)

    if not sections:
        print('\n✗  No sections found. Does the file contain "── Section Name" headers?')
        return

    # ── Preview ──────────────────────────────────────────────────────────────
    print(f'\nFound {len(sections)} sections:\n')
    max_fn = max(len(section_filename(n)) for n, _ in sections)
    for name, blocks in sections:
        fn = section_filename(name)
        tag = f'{len(blocks)} block{"s" if len(blocks) != 1 else ""}'
        skipped = ' (no style blocks — will skip)' if not blocks else ''
        print(f'  {fn:<{max_fn + 2}} {tag}{skipped}')

    # ── Output directory ─────────────────────────────────────────────────────
    print()
    raw_output = input('Path to save SCSS files: ').strip().strip('"\'')

    os.makedirs(raw_output, exist_ok=True)

    print()
    written = 0
    skipped = 0

    for name, blocks in sections:
        fn = section_filename(name)

        if not blocks:
            print(f'  ─  {fn}  (skipped)')
            skipped += 1
            continue

        filepath = os.path.join(raw_output, fn)
        scss = render_scss(name, blocks)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(scss)

        block_count = len(blocks)
        print(f'  ✓  {fn}  ({block_count} block{"s" if block_count != 1 else ""})')
        written += 1

    abs_out = os.path.abspath(raw_output)
    print(f'\n  {written} file{"s" if written != 1 else ""} written → {abs_out}')
    if skipped:
        print(f'  {skipped} section{"s" if skipped != 1 else ""} skipped (no style blocks found)')
    print()


if __name__ == '__main__':
    main()