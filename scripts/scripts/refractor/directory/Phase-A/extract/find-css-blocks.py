#!/usr/bin/env python3
"""
extract_ai_styles.py

Extracts every style-related parameter from AI-written frontend code.
Groups output by the comment/section headers found in the file.

Works on:  .tsx  .jsx  .ts  .js  .css  .scss  .html

What it finds:
  • const token objects      const DARK = { base: "#...", ... }
  • inline style blocks      style={{ prop: value, ... }}
  • CSS / SCSS rules         .selector { prop: value; }

Header types it recognises (used to group output):
  // ── Name ─────────────   line comment with dash decoration
  {/* Name */}              JSX block comment
  /* Name */                plain block comment
  // Name                   simple line comment directly above a block

Values captured in full:
  "#1c2230"                 → #1c2230
  "rgba(0,0,0,0.45)"        → rgba(0,0,0,0.45)
  390                       → 390
  `0 40px 80px ${c.neuDark}`→ 0 40px 80px ${c.neuDark}    (template refs kept)
  dark ? "#fff" : "#000"    → dark ? "#fff" : "#000"       (ternaries kept)
  c.base                    → c.base                       (variable refs kept)

Output saved to:
  scripts/data/audits/ai-styles/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

EXTENSIONS = {'.tsx', '.jsx', '.ts', '.js', '.css', '.scss', '.html'}

ORDINALS = [
    'one','two','three','four','five','six','seven','eight','nine','ten',
    'eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen',
    'eighteen','nineteen','twenty',
]


# ── Utilities ──────────────────────────────────────────────────────────────────

def to_kebab(name: str) -> str:
    """camelCase / PascalCase → kebab-case. Adds leading - for vendor prefixes."""
    # Insert - before uppercase letter preceded by lowercase
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '-', name)
    # Handle runs of caps: "WebGL" → "web-gl"
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', s)
    s = s.lower()
    # Restore leading dash for vendor prefixes
    for prefix in ('webkit-', 'moz-', 'ms-', 'o-'):
        if s.startswith(prefix):
            s = '-' + s
            break
    return s


def make_output_dir() -> Path:
    d = Path.cwd() / 'scripts' / 'data' / 'audits' / 'ai-styles'
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Input prompt ───────────────────────────────────────────────────────────────

def prompt_target():
    print()
    print('  [1]  Single file')
    print('  [2]  Entire directory')
    print()
    choice = input('  Choice (1 or 2): ').strip()

    if choice == '1':
        while True:
            raw = input('\n  File path: ').strip().strip('"\'')
            p = Path(raw)
            if p.is_file():
                return 'file', [p]
            print(f'  ✗  Not found: {raw}')
    else:
        while True:
            raw = input('\n  Directory path: ').strip().strip('"\'')
            p = Path(raw)
            if p.is_dir():
                files = []
                for ext in EXTENSIONS:
                    files.extend(p.rglob(f'*{ext}'))
                return 'dir', sorted(files)
            print(f'  ✗  Not found: {raw}')


# ── Header detection ───────────────────────────────────────────────────────────
#
# Recognises every comment style AI writers use as section dividers.
# ──────────────────────────────────────────────────────────────────────────────

_HEAD_PATTERNS = [
    # // ── Name ─────────────────   (line comment with dash decoration)
    re.compile(
        r'^[ \t]*//[ \t]*[─═━\-]{2,}[ \t]+(.+?)[ \t]*[─═━\-]*[ \t]*$',
        re.MULTILINE
    ),
    # {/* Name */}   JSX comment (any line)
    re.compile(r'\{/\*+[ \t]*(.+?)[ \t]*\*+/\}'),
    # /* Name */   plain block comment (short, looks like a label)
    re.compile(r'(?<!\{)/\*+[ \t]*([A-Za-z][^\n*]{1,50}?)[ \t]*\*+/(?!\})'),
    # // Name   plain line comment directly above a block opener
    #           (const, function, class, export, or <JSXElement)
    re.compile(
        r'^[ \t]*//[ \t]+([A-Za-z][A-Za-z0-9 _\-./,:()\'"]{1,60}?)[ \t]*$'
        r'(?=[ \t]*\n[ \t]*(?:const |function |class |export |<[A-Z]))',
        re.MULTILINE
    ),
]

_DASH_NOISE = re.compile(r'[─═━\-]{3,}')


def find_headers(text: str) -> list:
    """
    Return a sorted list of (char_pos, name) for every comment header found.
    Deduplicates hits that are within 10 characters of each other.
    """
    hits = []
    for pat in _HEAD_PATTERNS:
        for m in pat.finditer(text):
            raw  = m.group(1).strip()
            name = _DASH_NOISE.sub('', raw).strip()
            name = re.sub(r'\s{2,}', ' ', name)
            if 2 < len(name) < 90:
                hits.append((m.start(), name))

    hits.sort(key=lambda x: x[0])

    # Deduplicate hits that are very close (same header matched by two patterns)
    out, last_pos = [], -999
    for pos, name in hits:
        if pos - last_pos > 10:
            out.append((pos, name))
            last_pos = pos
    return out


def nearest_header(pos: int, headers: list, default='[no header]') -> str:
    """Return the name of the last header that appears before `pos`."""
    label = default
    for h_pos, h_name in headers:
        if h_pos < pos:
            label = h_name
        else:
            break
    return label


# ── Balanced-brace extractor ───────────────────────────────────────────────────
#
# Extracts the body of any { ... } block, correctly skipping over strings
# ('', "", ``) and template-expression placeholders (${...}).
# ──────────────────────────────────────────────────────────────────────────────

def extract_braces(text: str, start: int):
    """
    text[start] must be '{'.
    Returns (body_without_braces, end_pos) or (None, start) on failure.
    """
    if start >= len(text) or text[start] != '{':
        return None, start

    i      = start + 1
    depth  = 1
    in_str = None   # None | '"' | "'" | '`'
    tdepth = 0      # depth inside ${...} within a template literal

    while i < len(text):
        c = text[i]

        if in_str:
            if c == '\\' and i + 1 < len(text):
                i += 2
                continue
            if in_str == '`':
                if c == '$' and i + 1 < len(text) and text[i + 1] == '{':
                    tdepth += 1
                    i += 2
                    continue
                if c == '{' and tdepth > 0:
                    tdepth += 1
                elif c == '}' and tdepth > 0:
                    tdepth -= 1
                    i += 1
                    continue
                if c == '`' and tdepth == 0:
                    in_str = None
            elif c == in_str:
                in_str = None
        else:
            if c in ('"', "'", '`'):
                in_str = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start + 1 : i], i + 1

        i += 1

    return None, len(text)


# ── JS value reader ────────────────────────────────────────────────────────────
#
# Reads one JS value from `text` at `pos` (position after the colon).
# Strips surrounding quotes from plain strings.
# Template literals keep their ${...} references.
# Ternaries and variable references are kept as-is.
# ──────────────────────────────────────────────────────────────────────────────

def read_js_value(text: str, pos: int):
    """
    Read one JS value at pos.
    Returns (value_str, next_pos).
    """
    n = len(text)
    i = pos
    while i < n and text[i] in ' \t':
        i += 1
    if i >= n:
        return None, i

    c = text[i]

    # ── String literal ────────────────────────────────────────────────────────
    if c in ('"', "'"):
        q, j, buf = c, i + 1, []
        while j < n:
            ch = text[j]
            if ch == '\\' and j + 1 < n:
                esc = text[j + 1]
                buf.append({'n': '\n', 't': '\t', 'r': '\r'}.get(esc, esc))
                j += 2
                continue
            if ch == q:
                return ''.join(buf), j + 1
            buf.append(ch)
            j += 1
        return ''.join(buf), j

    # ── Template literal ──────────────────────────────────────────────────────
    if c == '`':
        j, buf, td = i + 1, [], 0
        while j < n:
            ch = text[j]
            if ch == '\\' and j + 1 < n:
                buf.append(text[j + 1])
                j += 2
                continue
            if ch == '$' and j + 1 < n and text[j + 1] == '{':
                buf.append('${')
                td += 1
                j += 2
                continue
            if ch == '{' and td > 0:
                buf.append('{')
                td += 1
            elif ch == '}' and td > 0:
                td -= 1
                buf.append('}')
            elif ch == '`' and td == 0:
                return ''.join(buf), j + 1
            else:
                buf.append(ch)
            j += 1
        return ''.join(buf), j

    # ── Numeric literal ───────────────────────────────────────────────────────
    m = re.match(r'-?\d+\.?\d*', text[i:])
    if m and (i == 0 or text[i - 1] not in '0123456789._'):
        return m.group(), i + len(m.group())

    # ── Expression ────────────────────────────────────────────────────────────
    # Reads to a comma or closing } at depth 0.
    # Handles: ternaries, function calls, variable references.
    j, depth, in_s = i, 0, None

    while j < n:
        ch = text[j]
        if in_s:
            if ch == '\\':
                j += 2
                continue
            if ch == in_s:
                in_s = None
        elif ch in ('"', "'", '`'):
            in_s = ch
        elif ch in ('(', '['):
            depth += 1
        elif ch in (')', ']'):
            if depth > 0:
                depth -= 1
            else:
                break
        elif ch == '{':
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
            else:
                break
        elif ch == ',' and depth == 0:
            break
        elif ch == '\n' and depth == 0:
            # Stop if the next non-whitespace line starts a new key
            rest = text[j + 1 : j + 80].lstrip()
            if re.match(r'[A-Za-z_$]\w*\s*:', rest):
                break
        j += 1

    val = text[i : j].strip().rstrip(',').strip()
    return (val if val else None), j


# ── JS object property parser ──────────────────────────────────────────────────

def parse_js_object(body: str) -> OrderedDict:
    """
    Parse key: value pairs from the body of a JS object (text between { and }).
    Returns OrderedDict { css-prop: [value, ...] }
    """
    props = OrderedDict()
    i, n  = 0, len(body)

    while i < n:
        # Skip whitespace and commas
        while i < n and body[i] in ' \t\n\r,':
            i += 1
        if i >= n:
            break

        # Skip line comments
        if body[i : i + 2] == '//':
            end = body.find('\n', i)
            i   = end + 1 if end >= 0 else n
            continue

        # Skip block comments
        if body[i : i + 2] == '/*':
            end = body.find('*/', i + 2)
            i   = end + 2 if end >= 0 else n
            continue

        # Match a key (identifier followed by colon)
        km = re.match(r'([A-Za-z_$][A-Za-z0-9_$]*)\s*:', body[i:])
        if not km:
            # Not a key — skip to next newline and try again
            end = body.find('\n', i)
            i   = end + 1 if end >= 0 else n
            continue

        key  = km.group(1)
        i   += len(km.group(0))

        value, i = read_js_value(body, i)

        if key and value is not None:
            val = value.strip().rstrip(',').strip()
            if val:
                props.setdefault(to_kebab(key), []).append(val)

    return props


# ── Extractors ─────────────────────────────────────────────────────────────────

def extract_const_objects(text: str) -> list:
    """
    Find every  const NAME = { ... }  block.
    Returns [(pos, name, props_dict)]
    """
    result = []
    pat    = re.compile(r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\{')

    for m in pat.finditer(text):
        brace_pos = m.end() - 1        # position of the opening {
        body, _   = extract_braces(text, brace_pos)
        if not body:
            continue
        props = parse_js_object(body)
        if props:
            result.append((m.start(), m.group(1), props))

    return result


def extract_style_blocks(text: str) -> list:
    """
    Find every  style={{ ... }}  block in JSX.
    Returns [(pos, props_dict)]
    """
    result = []
    pat    = re.compile(r'style=\{\{')

    for m in pat.finditer(text):
        # The JS object opener { is the second {  → m.end() - 1
        brace_pos = m.end() - 1
        body, _   = extract_braces(text, brace_pos)
        if not body:
            continue
        props = parse_js_object(body)
        if props:
            result.append((m.start(), props))

    return result


def extract_css_rules(text: str) -> list:
    """
    Find CSS/SCSS rules: .selector { prop: value; }
    Returns [(pos, selector, props_dict)]
    """
    result   = []
    # Remove block comments before scanning
    clean    = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    rule_pat = re.compile(
        r'([.#:@][\w\s.,:#>()\[\]"\'=\-+~^$*]{1,120}?)\s*\{([^{}]+)\}',
        re.DOTALL
    )

    for m in rule_pat.finditer(clean):
        selector = re.sub(r'\s+', ' ', m.group(1).strip())
        if not selector or selector.startswith('@'):
            continue
        body  = m.group(2)
        props = OrderedDict()

        # SCSS variable declarations
        for vm in re.finditer(r'(\$[\w-]+)\s*:\s*(.+?)(?:;|$)', body, re.MULTILINE):
            k, v = vm.group(1).strip(), vm.group(2).strip().rstrip(';, ')
            if k and v:
                props.setdefault(k, []).append(v)

        # Regular CSS properties
        for pm in re.finditer(r'([\w-]+)\s*:\s*([^;}{]+?)(?:;|$)', body, re.MULTILINE):
            k, v = pm.group(1).strip(), pm.group(2).strip().rstrip(';, ')
            if k and v and not k.startswith('//'):
                props.setdefault(k, []).append(v)

        if props:
            result.append((m.start(), selector, props))

    return result


# ── Nearest-element label finder ───────────────────────────────────────────────

def find_element_label(text: str, pos: int) -> str | None:
    """
    Look backward from pos to find the best label for a style block:
      1. className= on the same element
      2. JSX comment {/* ... */} just above
      3. Returns None if nothing useful found

    Never looks past the most recent function/component declaration so labels
    cannot leak from one component into another.
    """
    lookback = text[max(0, pos - 600) : pos]

    # Stop at the most recent function / arrow-component declaration.
    # This prevents {/* BottomNav */} from App() leaking into StarBtn etc.
    import re as _re
    func_pat = _re.compile(
        r'(?:^|\n)[ \t]*(?:function\s+\w|export\s+default\s+function|'        r'const\s+[A-Z]\w*\s*=\s*(?:\([^)]*\)|[A-Za-z_]\w*)\s*=>)',
        _re.MULTILINE
    )
    last_func = None
    for m in func_pat.finditer(lookback):
        last_func = m
    if last_func:
        lookback = lookback[last_func.start():]

    # 1. className= on the same unclosed tag
    last_lt = lookback.rfind('<')
    if last_lt >= 0:
        frag = lookback[last_lt:]
        # Only if we haven't hit a > since the last <
        if '>' not in frag or frag.rfind('>') < frag.rfind('className'):
            cm = re.search(r'className=["\']([^"\']+)["\']', frag)
            if cm:
                return cm.group(1).split()[0]

    # 2. JSX comment immediately above
    jc = list(re.finditer(r'\{/\*+\s*(.+?)\s*\*+/\}', lookback))
    if jc:
        name = jc[-1].group(1).strip()
        if name and len(name) < 80:
            return name

    return None


# ── CSS / SCSS file processor ──────────────────────────────────────────────────

def process_css_file(path: Path) -> tuple:
    """Process a .css/.scss file. Returns (sections, block_count)."""
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        print(f'  ✗  {path.name}: {e}')
        return OrderedDict(), 0

    headers  = find_headers(text)
    sections = OrderedDict()

    for pos, selector, props in extract_css_rules(text):
        section = nearest_header(pos, headers, default='styles')
        sections.setdefault(section, []).append((selector, props))

    return sections, sum(len(v) for v in sections.values())


# ── JS / TS / JSX / TSX file processor ────────────────────────────────────────

def process_js_file(path: Path) -> tuple:
    """Process a JS/TS/JSX/TSX file. Returns (sections, block_count)."""
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        print(f'  ✗  {path.name}: {e}')
        return OrderedDict(), 0

    headers  = find_headers(text)
    sections = OrderedDict()

    # ── 1. const NAME = { ... } blocks ────────────────────────────────────────
    for pos, name, props in extract_const_objects(text):
        section = nearest_header(pos, headers)
        sections.setdefault(section, []).append((f'const {name}', props))

    # ── 2. style={{ ... }} blocks ─────────────────────────────────────────────
    # Track per-section counters for unlabelled styles
    label_counts: dict = {}

    for pos, props in extract_style_blocks(text):
        section = nearest_header(pos, headers)
        label   = find_element_label(text, pos)

        if not label:
            # Number unlabelled styles per section
            n    = label_counts.get(section, 0) + 1
            label_counts[section] = n
            word  = ORDINALS[n - 1] if n <= len(ORDINALS) else str(n)
            label = f'inline-style-{word}'

        sections.setdefault(section, []).append((label, props))

    return sections, sum(len(v) for v in sections.values())


# ── HTML file processor ────────────────────────────────────────────────────────

def process_html_file(path: Path) -> tuple:
    """Process an HTML file. Returns (sections, block_count)."""
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        print(f'  ✗  {path.name}: {e}')
        return OrderedDict(), 0

    headers  = find_headers(text)
    sections = OrderedDict()

    # Inline style="" attributes
    style_pat = re.compile(r'style=["\']([^"\']+)["\']')
    label_counts: dict = {}

    for m in style_pat.finditer(text):
        pos   = m.start()
        body  = m.group(1)
        props = OrderedDict()

        for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+)', body):
            k = pm.group(1).strip()
            v = pm.group(2).strip().rstrip(';, ')
            if k and v:
                props.setdefault(k, []).append(v)

        if not props:
            continue

        section = nearest_header(pos, headers)

        # Label by id/class if present on the same element
        lookback = text[max(0, pos - 200) : pos]
        last_lt  = lookback.rfind('<')
        label    = None
        if last_lt >= 0:
            frag = lookback[last_lt:]
            for attr in ('id', 'class'):
                am = re.search(rf'\b{attr}=["\']([^"\']+)["\']', frag)
                if am:
                    label = am.group(1).split()[0]
                    break

        if not label:
            n    = label_counts.get(section, 0) + 1
            label_counts[section] = n
            word  = ORDINALS[n - 1] if n <= len(ORDINALS) else str(n)
            label = f'inline-style-{word}'

        sections.setdefault(section, []).append((label, props))

    # Also pick up embedded <style> blocks
    style_block = re.compile(r'<style[^>]*>(.*?)</style>', re.DOTALL | re.IGNORECASE)
    for sm in style_block.finditer(text):
        css_text = sm.group(1)
        for pos2, selector, props in extract_css_rules(css_text):
            section = nearest_header(sm.start() + pos2, headers, default='<style>')
            sections.setdefault(section, []).append((selector, props))

    return sections, sum(len(v) for v in sections.values())


# ── Router ─────────────────────────────────────────────────────────────────────

def process_file(path: Path) -> tuple:
    ext = path.suffix.lower()
    if ext in ('.css', '.scss'):
        return process_css_file(path)
    elif ext == '.html':
        return process_html_file(path)
    else:  # .tsx .jsx .ts .js
        return process_js_file(path)


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(sections: OrderedDict, filename: str) -> str:
    fn_display = filename[:57]
    lines = [
        '╔══════════════════════════════════════════════════════════════════╗',
        f'║  REPORT 1 — {fn_display:<53}║',
        '║  Grouped by section comment header                               ║',
        '╚══════════════════════════════════════════════════════════════════╝',
    ]

    for section, blocks in sections.items():
        lines.append(f"\n{'═' * 70}")
        lines.append(f'  ── {section}')
        lines.append('═' * 70)

        for label, props in blocks:
            lines.append(f'\n  {label} {{')
            for prop, values in props.items():
                for v in values:
                    lines.append(f'    {prop}: {v}')
            lines.append('  }')

        lines.append('')

    return '\n'.join(lines)


# ── Run ────────────────────────────────────────────────────────────────────────

def run():
    print()
    print('╔══════════════════════════════════════════════════════╗')
    print('║   AI Style Extractor                                 ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    print('  Finds ALL style blocks, const token objects, and CSS')
    print('  parameters — grouped by the comment headers above them.')
    print()
    print('  Supports: .tsx  .jsx  .ts  .js  .css  .scss  .html')

    mode, files = prompt_target()

    if not files:
        print('\n  No matching files found.')
        return

    print(f'\n  Processing {len(files)} file{"s" if len(files) > 1 else ""}...\n')

    output_dir = make_output_dir()
    timestamp  = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    if mode == 'file':
        path = files[0]
        sections, count = process_file(path)

        # Summary to terminal
        print(f'  ✓  {path.name}')
        print(f'\n  {len(sections)} section{"s" if len(sections) != 1 else ""}:\n')
        for section, blocks in sections.items():
            print(f'    {section:<50}  {len(blocks)} block{"s" if len(blocks) != 1 else ""}')

        report   = build_report(sections, path.name)
        out_path = output_dir / f'{path.stem}_{timestamp}.txt'
        out_path.write_text(report, encoding='utf-8')

        print(f'\n  ✓  Report → {out_path}')
        print(f'     {count} total blocks\n')

    else:
        all_sections: OrderedDict = OrderedDict()
        total = 0

        for p in files:
            secs, count = process_file(p)
            if secs:
                print(f'  ✓  {p.name:<40} {count} blocks, {len(secs)} sections')
                for section, blocks in secs.items():
                    key = f'{p.name} / {section}'
                    all_sections.setdefault(key, []).extend(blocks)
                total += count
            else:
                print(f'  –  {p.name:<40} (nothing extracted)')

        report   = build_report(all_sections, f'{len(files)} files')
        out_path = output_dir / f'directory_{timestamp}.txt'
        out_path.write_text(report, encoding='utf-8')

        print(f'\n  ✓  Report → {out_path}')
        print(f'     {total} total blocks across {len(files)} files\n')


if __name__ == '__main__':
    run()