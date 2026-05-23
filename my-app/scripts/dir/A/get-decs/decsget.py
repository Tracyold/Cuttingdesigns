#!/usr/bin/env python3
"""
scan_params.py

Scan a single file OR an entire directory of:
  .jsx .tsx .ts .html .css .scss

Extracts every CSS property, value, selector, and variable.

Two output reports:

  REPORT 1:
    Directory mode  → grouped by filename → selector → properties
    Single file     → grouped by section header comment → properties

  REPORT 2:
    Both modes → grouped by property name → values → which files use them

Saves to scripts/data/audits/parameters/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict, OrderedDict


# ── Output path ───────────────────────────────────────────────────────

def make_output_dir():
    d = Path.cwd() / "scripts" / "data" / "audits" / "parameters"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Input prompt ──────────────────────────────────────────────────────

EXTENSIONS = {".jsx", ".tsx", ".ts", ".html", ".css", ".scss"}

def prompt_target():
    print()
    print("  [1] Scan a single file")
    print("  [2] Scan an entire directory")
    print()
    choice = input("  Choice (1 or 2): ").strip()

    if choice == "1":
        while True:
            path = input("\n  File path: ").strip().strip('"').strip("'")
            p = Path(path)
            if p.is_file() and p.suffix.lower() in EXTENSIONS:
                return "file", [p]
            elif p.is_file():
                print(f"  ✗ Unsupported extension: {p.suffix}")
            else:
                print(f"  ✗ File not found: {path}")
    else:
        while True:
            path = input("\n  Directory path: ").strip().strip('"').strip("'")
            p = Path(path)
            if p.is_dir():
                files = []
                for ext in EXTENSIONS:
                    files.extend(p.rglob(f"*{ext}"))
                return "directory", sorted(files)
            print(f"  ✗ Directory not found: {path}")


# ── Helpers ───────────────────────────────────────────────────────────

def clean_value(v):
    return v.strip().rstrip(";,").strip('"').strip("'").strip()

def to_kebab(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower()

COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)
COMMENT_LINE  = re.compile(r'//.*$', re.MULTILINE)


# ── CSS / SCSS parser ─────────────────────────────────────────────────

CSS_BLOCK = re.compile(r'([^{}@/][^{}]*?)\{([^{}]*?)\}', re.DOTALL)
CSS_PROP  = re.compile(r'([\w-]+)\s*:\s*([^;}{]+?)(?:;|$)', re.MULTILINE)


def parse_css(text, filename):
    groups = OrderedDict()
    clean  = COMMENT_BLOCK.sub('', text)

    # SCSS variable declarations → own group
    scss_vars = OrderedDict()
    for line in clean.splitlines():
        m = re.match(r'^\s*(\$[\w-]+)\s*:\s*(.+?);', line.strip())
        if m:
            scss_vars[m.group(1)] = clean_value(m.group(2))
    if scss_vars:
        groups["[scss variables]"] = {k: [v] for k, v in scss_vars.items()}

    for match in CSS_BLOCK.finditer(clean):
        selector = match.group(1).strip().replace('\n', ' ')
        selector = re.sub(r'\s+', ' ', selector).strip()
        if not selector or selector.startswith('@'):
            continue
        body  = match.group(2)
        props = OrderedDict()
        for p in CSS_PROP.finditer(body):
            prop  = p.group(1).strip()
            value = clean_value(p.group(2))
            if prop and value:
                props.setdefault(prop, []).append(value)
        if props:
            groups[selector] = props
    return groups


# ── React / JSX parser (ENHANCED) ────────────────────────────────────
#
# Now captures className AND heading text near each style={{}} block
# so generate_decs.py knows what component each value belongs to.
#
# Priority for labelling each style block:
#   1. className on the SAME element  → e.g. "card-header"
#   2. JSX comment just above         → {/* Card Header */}
#   3. Heading tag just above          → <h2>Card Header</h2>
#   4. Falls back to [inline style #N]
# ─────────────────────────────────────────────────────────────────────

# Matches:  style={{ borderRadius: '8px', color: 'red' }}
STYLE_OBJ  = re.compile(r'style=\{\{(.*?)\}\}', re.DOTALL)

# Matches string values:   borderRadius: '8px'   or  color: "red"
REACT_PROP = re.compile(r'(\w+)\s*:\s*["`\']([^"`\']+)["`\']')

# Matches numeric values:  zIndex: 10
REACT_NUM  = re.compile(r'(\w+)\s*:\s*(\d[\d.]*)\b')

# Matches className on the same JSX opening tag as style={{}}
# Looks backward from the style= for the opening < and forward
CLASS_ON_ELEMENT = re.compile(
    r'<\w[\w.]*[^<>]*?className=["\']([^"\']+)["\'][^<>]*?style=\{\{|'
    r'<\w[\w.]*[^<>]*?style=\{\{[^<>]*?className=["\']([^"\']+)["\']',
    re.DOTALL
)

# JSX comment: {/* Card Header */}
JSX_COMMENT = re.compile(r'\{/\*+\s*(.*?)\s*\*+/\}')

# HTML/JSX heading tags
HEADING_TAG = re.compile(r'<h[1-6][^>]*>\s*(.*?)\s*</h[1-6]>', re.DOTALL)


def _find_label_for_style(text, style_match_start):
    """
    Given the character position where style={{ starts in `text`,
    look backward up to 600 chars to find:
      - className on the same element
      - a JSX comment
      - a heading tag
    Returns the best label string found, or None.
    """
    lookback = text[max(0, style_match_start - 600): style_match_start]

    # 1. className on the same opening tag — look for the most recent <
    #    that hasn't been closed yet (i.e. we're still inside the opening tag)
    last_open = lookback.rfind('<')
    if last_open != -1:
        tag_fragment = lookback[last_open:]
        # Make sure we haven't hit a closing > in between
        if '>' not in tag_fragment:
            cm = re.search(r'className=["\']([^"\']+)["\']', tag_fragment)
            if cm:
                # Take the first class name only
                return cm.group(1).split()[0].strip()

    # 2. className on the same opening tag — the tag may span back further,
    #    try a wider capture (element might have many props on multiple lines)
    tag_block_m = re.search(
        r'<\w[\w.]*(?:[^<>]|\n)*?className=["\']([^"\']+)["\'](?:[^<>]|\n)*?$',
        lookback
    )
    if tag_block_m:
        return tag_block_m.group(1).split()[0].strip()

    # 3. JSX comment directly above
    jsx_comments = list(JSX_COMMENT.finditer(lookback))
    if jsx_comments:
        label = jsx_comments[-1].group(1).strip()
        if label:
            return label

    # 4. Heading tag
    headings = list(HEADING_TAG.finditer(lookback))
    if headings:
        label = re.sub(r'<[^>]+>', '', headings[-1].group(1)).strip()
        if label:
            return label

    return None


def parse_react(text, filename):
    """
    Parses all style={{}} blocks in JSX/TSX.
    Labels each block with its className or nearest heading/comment.
    If the same label appears more than once, props are MERGED into
    one group — no #2 #3 numbering.
    """
    groups = OrderedDict()

    for i, match in enumerate(STYLE_OBJ.finditer(text)):
        body = match.group(1)

        props = OrderedDict()
        for m in REACT_PROP.finditer(body):
            prop  = to_kebab(m.group(1))
            value = clean_value(m.group(2))
            if prop and value:
                props.setdefault(prop, []).append(value)
        for m in REACT_NUM.finditer(body):
            prop  = to_kebab(m.group(1))
            value = m.group(2)
            if prop and value:
                props.setdefault(prop, []).append(value)

        if not props:
            continue

        raw_label = _find_label_for_style(text, match.start())
        NUMBERS = ['one','two','three','four','five','six','seven','eight',
                   'nine','ten','eleven','twelve','thirteen','fourteen','fifteen',
                   'sixteen','seventeen','eighteen','nineteen','twenty']
        n = i  # i is 0-based
        word = NUMBERS[n] if n < len(NUMBERS) else f'{n+1}'
        label = raw_label if raw_label else f"inline-style-{word}"

        # Merge into existing group if label already seen —
        # never append #2, #3 etc to a label
        if label in groups:
            for prop, values in props.items():
                groups[label].setdefault(prop, []).extend(values)
        else:
            groups[label] = props

    return groups


# ── TypeScript parser ─────────────────────────────────────────────────

TS_BLOCK  = re.compile(r'(?:interface|type)\s+(\w+)\s*(?:=\s*)?\{([^{}]+)\}', re.DOTALL)
TS_MEMBER = re.compile(r'^\s+(\w+)\??\s*:\s*(.+?)(?:;|,|$)', re.MULTILINE)


def parse_ts(text):
    groups = OrderedDict()
    for match in TS_BLOCK.finditer(text):
        name  = match.group(1)
        body  = match.group(2)
        props = OrderedDict()
        for m in TS_MEMBER.finditer(body):
            prop  = m.group(1).strip()
            value = clean_value(m.group(2))
            if prop and value:
                props.setdefault(prop, []).append(value)
        if props:
            groups[f"type {name}"] = props
    return groups


# ── HTML parser (ENHANCED) ────────────────────────────────────────────
#
# Now captures the id= or class= on the same element that has style=""
# so we get useful labels instead of [html inline #N]
# ─────────────────────────────────────────────────────────────────────

HTML_STYLE = re.compile(r'style=["\']([^"\']+)["\']')
HTML_PROP  = re.compile(r'([\w-]+)\s*:\s*([^;]+)')


def _find_html_label(text, style_match_start):
    """Look backward from style= to find id= or class= on the same tag."""
    lookback = text[max(0, style_match_start - 400): style_match_start]
    last_open = lookback.rfind('<')
    if last_open == -1:
        return None
    tag_fragment = lookback[last_open:]
    if '>' in tag_fragment:
        return None   # already past the tag opener

    # Try id first, then class
    m = re.search(r'\bid=["\']([^"\']+)["\']', tag_fragment)
    if m:
        return m.group(1).strip()
    m = re.search(r'\bclass=["\']([^"\']+)["\']', tag_fragment)
    if m:
        return m.group(1).split()[0].strip()
    return None


def parse_html(text):
    groups = OrderedDict()

    for i, match in enumerate(HTML_STYLE.finditer(text)):
        props = OrderedDict()
        for m in HTML_PROP.finditer(match.group(1)):
            prop  = m.group(1).strip()
            value = clean_value(m.group(2))
            if prop and value:
                props.setdefault(prop, []).append(value)

        if not props:
            continue

        raw_label = _find_html_label(text, match.start())
        NUMBERS = ['one','two','three','four','five','six','seven','eight',
                   'nine','ten','eleven','twelve','thirteen','fourteen','fifteen',
                   'sixteen','seventeen','eighteen','nineteen','twenty']
        n = i
        word = NUMBERS[n] if n < len(NUMBERS) else f'{n+1}'
        label = raw_label if raw_label else f"inline-style-{word}"

        # Merge into existing group — never number duplicate labels
        if label in groups:
            for prop, values in props.items():
                groups[label].setdefault(prop, []).extend(values)
        else:
            groups[label] = props

    return groups


# ── Single file: section header grouping ─────────────────────────────

HEADER_RE = re.compile(
    r'(?:^|\n)\s*(?:/\*+\s*(.*?)\s*\*+/|//\s*[─═\-]+\s*(.+?)\s*[─═\-]*\s*$)',
    re.MULTILINE
)

def extract_sections(text):
    sections  = []
    headers   = list(HEADER_RE.finditer(text))
    prev_name = "[top level]"
    prev_end  = 0

    for i, h in enumerate(headers):
        name = (h.group(1) or h.group(2) or "").strip()
        name = re.sub(r'[─═\-]+', '', name).strip()
        if not name:
            continue
        chunk = text[prev_end:h.start()]
        if chunk.strip():
            sections.append((prev_name, chunk))
        prev_name = name
        prev_end  = h.end()

    chunk = text[prev_end:]
    if chunk.strip():
        sections.append((prev_name, chunk))

    return sections if sections else [("[all]", text)]


def parse_file_by_sections(path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    ext      = path.suffix.lower()
    groups   = OrderedDict()
    sections = extract_sections(text)

    for section_name, chunk in sections:
        section_groups = OrderedDict()

        if ext in (".css", ".scss"):
            section_groups.update(parse_css(chunk, path.name))
        elif ext in (".jsx", ".tsx"):
            section_groups.update(parse_css(chunk, path.name))
            section_groups.update(parse_react(chunk, path.name))
        elif ext == ".ts":
            section_groups.update(parse_ts(chunk))
        elif ext == ".html":
            section_groups.update(parse_css(chunk, path.name))
            section_groups.update(parse_html(chunk))

        if section_groups:
            groups[section_name] = section_groups

    return groups


# ── Directory mode: parse each file normally ──────────────────────────

def parse_file(path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    ext    = path.suffix.lower()
    groups = OrderedDict()

    if ext in (".css", ".scss"):
        groups.update(parse_css(text, path.name))
    elif ext in (".jsx", ".tsx"):
        groups.update(parse_css(text, path.name))
        groups.update(parse_react(text, path.name))
    elif ext == ".ts":
        groups.update(parse_ts(text))
    elif ext == ".html":
        groups.update(parse_css(text, path.name))
        groups.update(parse_html(text))

    return groups


# ── Report 1: per file (directory) ───────────────────────────────────

def build_report1_directory(all_data):
    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  REPORT 1 — PER FILE → selector → properties                    ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")

    for filename, groups in sorted(all_data.items()):
        if not groups:
            continue
        lines.append(f"\n{'═'*70}")
        lines.append(f"  {filename}")
        lines.append('═'*70)
        for selector, props in groups.items():
            lines.append(f"\n  {selector} {{")
            for prop, values in props.items():
                for v in sorted(set(values)):
                    lines.append(f"    {prop}: {v}")
            lines.append("  }")

    return "\n".join(lines)


# ── Report 1: single file (by section) ───────────────────────────────

def build_report1_single(filename, section_data):
    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append(f"║  REPORT 1 — {filename:<53}║")
    lines.append("║  Grouped by section comment header                               ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")

    for section_name, groups in section_data.items():
        lines.append(f"\n{'═'*70}")
        lines.append(f"  ── {section_name}")
        lines.append('═'*70)
        for selector, props in groups.items():
            lines.append(f"\n  {selector} {{")
            for prop, values in props.items():
                for v in sorted(set(values)):
                    lines.append(f"    {prop}: {v}")
            lines.append("  }")

    return "\n".join(lines)


# ── Report 2: per property (both modes) ──────────────────────────────

def build_report2(flat_data):
    """
    flat_data: dict of filename → {selector → {prop → [values]}}
    """
    prop_map = defaultdict(lambda: defaultdict(set))

    for filename, groups in flat_data.items():
        for selector, props in groups.items():
            for prop, values in props.items():
                for v in values:
                    prop_map[prop][v].add(filename)

    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  REPORT 2 — PER PROPERTY: values across all files               ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")

    for prop in sorted(prop_map.keys()):
        values = prop_map[prop]
        lines.append(f"\n{'─'*70}")
        lines.append(f"  {prop}  ({len(values)} unique value{'s' if len(values) != 1 else ''})")
        lines.append('─'*70)
        for value, files in sorted(values.items()):
            files_str = ", ".join(sorted(files))
            lines.append(f"  {value:<45} ← {files_str}")

    return "\n".join(lines)


# ── Pipeline file builder ─────────────────────────────────────────────
#
# Combines Report 1 and Report 2 data into one deduplicated file
# that generate_decs.py reads. Format is simple and unambiguous:
#
#   PIPELINE FILE — generated by scan_params.py
#   ═══════════════════
#   prop        | value              | source_label
#   ─────────────────────────────────────────────
#   border-radius | 25px             | pop-button
#   border-radius | 1px              | static-card
#   color         | #ffffff          | Card / card-header
#
# Deduplication key: prop + value + source_label (all three must match)
# ─────────────────────────────────────────────────────────────────────

def build_pipeline(flat_data_r1, flat_data_r2=None):
    """
    flat_data_r1: the same flat dict used for Report 2 in single/directory mode
                  { filename: { "section / selector": { prop: [values] } } }
    flat_data_r2: optional second source (not needed here — we derive both
                  from the same scan, so one flat_data is enough)

    Returns (content_string, entry_count)
    """
    # Collect all (prop, value, source_label) from flat_data
    # source_label is "section / selector" or just "selector"
    seen    = set()
    entries = []

    for filename, groups in flat_data_r1.items():
        for source_label, props in groups.items():
            for prop, values in props.items():
                for value in values:
                    value = value.strip()
                    if not prop or not value:
                        continue
                    key = (prop.strip().lower(), value.lower(), source_label.strip().lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append((prop.strip(), value, source_label.strip()))

    # Sort by prop → source_label → value for readable output
    entries.sort(key=lambda e: (e[0], e[2], e[1]))

    # Build file content
    col1 = max((len(e[0]) for e in entries), default=10) + 2
    col2 = max((len(e[1]) for e in entries), default=10) + 2

    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  PIPELINE FILE — for generate_decs.py                            ║")
    lines.append("║  Deduplicated · prop + value + source                            ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H-%M-%S')}")
    lines.append(f"  Entries:   {len(entries)}")
    lines.append("")
    lines.append(f"  {'PROPERTY':<{col1}}{'VALUE':<{col2}}SOURCE")
    lines.append(f"  {'─'*col1}{'─'*col2}{'─'*40}")

    current_prop = None
    for prop, value, source in entries:
        if prop != current_prop:
            lines.append("")
            lines.append(f"  {'─'*68}")
            lines.append(f"  {prop}")
            lines.append(f"  {'─'*68}")
            current_prop = prop
        lines.append(f"  {prop:<{col1}}{value:<{col2}}{source}")

    return "\n".join(lines), len(entries)


# ── Run ───────────────────────────────────────────────────────────────

def run():
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║         frontend parameter scanner                   ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Scans .jsx .tsx .ts .html .css .scss")
    print("  Single file → grouped by section comment headers")
    print("  Directory   → grouped by filename")

    mode, files = prompt_target()

    if not files:
        print("\n  No matching files found.")
        sys.exit(0)

    print(f"\n  Scanning {len(files)} file{'s' if len(files) > 1 else ''}...\n")

    timestamp  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = make_output_dir()

    if mode == "file":
        path         = files[0]
        section_data = parse_file_by_sections(path)
        print(f"  ✓  {path.name}  ({len(section_data)} sections)")

        flat = {path.name: {}}
        for section, groups in section_data.items():
            for selector, props in groups.items():
                key = f"{section} / {selector}"
                flat[path.name][key] = props

        r1 = build_report1_single(path.name, section_data)
        r2 = build_report2(flat)

        r1_path = output_dir / f"single-file_{path.stem}_{timestamp}.txt"
        r2_path = output_dir / f"single-file_{path.stem}_per-property_{timestamp}.txt"

    else:
        all_data = {}
        for f in files:
            groups = parse_file(f)
            if groups:
                all_data[f.name] = groups
                print(f"  ✓  {f.name}  ({len(groups)} groups)")
            else:
                print(f"  –  {f.name}  (nothing extracted)")

        r1 = build_report1_directory(all_data)
        r2 = build_report2(all_data)

        r1_path = output_dir / f"per-file_{timestamp}.txt"
        r2_path = output_dir / f"per-property_{timestamp}.txt"

        # Build flat dict for pipeline (directory mode)
        # key is "filename / selector" so source context is preserved
        flat = {}
        for filename, groups in all_data.items():
            flat[filename] = {}
            for selector, props in groups.items():
                flat[filename][f"{filename} / {selector}"] = props

    r1_path.write_text(r1, encoding="utf-8")
    r2_path.write_text(r2, encoding="utf-8")

    print(f"\n  ✓ Report 1 → {r1_path}")
    print(f"  ✓ Report 2 → {r2_path}")

    # ── Pipeline file prompt
    print()
    print("  ─" * 35)
    print()
    print("  Generate a pipeline file for generate_decs.py?")
    print("  (Combines both reports into one deduplicated file the writing script reads)")
    print()
    choice = input("  Generate pipeline file? (y/n): ").strip().lower()

    if choice == 'y':
        pipeline_content, entry_count = build_pipeline(flat)
        pipeline_path = output_dir / f"pipeline_{timestamp}.txt"
        pipeline_path.write_text(pipeline_content, encoding="utf-8")
        print(f"\n  ✓ Pipeline file → {pipeline_path}")
        print(f"    {entry_count} deduplicated entries ready for generate_decs.py")
    else:
        print("\n  Skipped. You can run scan_params.py again to generate it later.")

    print()


if __name__ == "__main__":
    run()