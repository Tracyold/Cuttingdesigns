#!/usr/bin/env python3
"""
generate_decs.py

Reads scan_params.py Report 2 (per-property) output.
Generates one SCSS decs file per real CSS property type.

NAMING RULES (source of truth priority):
  Rule 1: param inside a named className     → className is source of truth
  Rule 2: no className                       → use the section header above it
  Rule 3: has BOTH className + header        → use className, add header as comment
  Rule 4: inline style with no className/header → nearest className or header before it

ALIAS NAMING:
  Use the LAST word of the className + full parameter name
  e.g.  pop-button  → button     → $button-border-radius
        static-card → card       → $card-font-size

COLORS.SCSS — always created first:
  - All unique color values get a CSS custom property: --a, --b, --c ...
  - Written inside :root { }
  - If source has light/dark theme → also writes :root-light and :root-dark
  - The "color" parameter aliases live in this file, using var(--x)
  - Every other color-type file (background-color, border-color, box-shadow...)
    also uses var(--x) — never hardcoded hex/rgba

NON-COLOR FILES:
  - Use the $_a_ $_b_ primitive system (unchanged)

Output: scripts/data/generated/decs/
"""

import re
import sys
import string
from pathlib import Path
from datetime import datetime
from collections import defaultdict, OrderedDict
from itertools import product


# ── Paths ─────────────────────────────────────────────────────────────

PARAMS_DIR = Path.cwd() / "scripts" / "data" / "audits" / "parameters"
# OUTPUT_DIR is set at runtime by prompt_output_dir()


# ── Which properties are "color type" ────────────────────────────────
# These use var(--x) references instead of $_a_ primitives.

COLOR_PROPS = {
    "color", "background-color", "border-color",
    "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color",
    "outline-color", "box-shadow", "text-shadow",
    "fill", "stroke", "caret-color", "column-rule-color",
    "text-decoration-color",
}

# Values that look like colors (hex, rgba, rgb, hsl, named colors)
COLOR_VALUE_RE = re.compile(
    r'^(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\)|'
    r'black|white|transparent|currentColor|inherit|'
    r'red|blue|green|yellow|orange|purple|pink|gray|grey|'
    r'navy|teal|silver|gold|lime|aqua|fuchsia|maroon|olive|'
    r'[a-zA-Z][\w-]*color[a-zA-Z]*)$'
)

def is_color_value(v):
    return bool(COLOR_VALUE_RE.match(v.strip()))


# ── Real CSS properties whitelist ─────────────────────────────────────

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
    "color","background","background-color","background-image","background-size",
    "background-position","background-repeat","background-attachment",
    "filter","backdrop-filter","mix-blend-mode",
    "border","border-top","border-right","border-bottom","border-left",
    "border-width","border-style","border-color","border-radius",
    "border-top-left-radius","border-top-right-radius",
    "border-bottom-left-radius","border-bottom-right-radius",
    "border-image","outline","outline-offset","outline-color","outline-width",
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
    "--*",
}

def is_valid_css_prop(prop):
    p = prop.strip().lower()
    if p.startswith('--'):
        return True
    return p in VALID_CSS_PROPS


# ── Primitive name generators ─────────────────────────────────────────

def gen_primitive_names():
    """Generates $_a_  $_b_  ...  $_z_  $_aa_  ..."""
    letters = string.ascii_lowercase
    length  = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '$_' + ''.join(combo) + '_'
        length += 1

def gen_var_names():
    """Generates --a  --b  ...  --z  --aa  ..."""
    letters = string.ascii_lowercase
    length  = 1
    while True:
        for combo in product(letters, repeat=length):
            yield '--' + ''.join(combo)
        length += 1


# ── Name helpers ──────────────────────────────────────────────────────

def last_word(name):
    """
    Returns the LAST word of a className/CSS selector for alias naming.

    JSX classNames:
      pop-button        → button
      static-card       → card
      static-card.hover → hover   (.hover is a state, not a file extension)
      drop-down-modal   → modal
      MobileAccount.tsx → account  (camelCase split)
      AdminPanel.tsx    → panel

    CSS selectors:
      .pop-button           → button
      .static-card          → card
      .static-card.hover    → hover
      .static-card .child   → child
      .btn-primary:focus    → primary   (pseudo-classes stripped)
      .card-header::before  → header    (pseudo-elements stripped)
      nav > .menu-item      → item      (combinators stripped)
      #my-id                → id

    Always:
      [inline style #1]     → unknown
      #2                    → unknown   (bare number/hash labels)
    """
    name = name.strip()
    # Bracketed fallback labels (old format, kept for backward compat)
    if name.startswith('['):
        return 'unknown'
    # Spelled-out inline style fallbacks: inline-style-one, inline-style-two etc
    if re.match(r'^inline-style-', name):
        return 'unknown'
    # Bare #N labels (e.g. "#2", "#4") — inline style fallbacks that lost their brackets
    if re.match(r'^#?\d+$', name):
        return 'unknown'
    # Strip real file extensions (.jsx .tsx .ts .js .css .scss .html)
    name = re.sub(r'\.(jsx|tsx|ts|js|css|scss|html)$', '', name, flags=re.IGNORECASE)
    # Strip pseudo-elements (::before, ::after) and pseudo-classes (:focus, :hover)
    # BEFORE splitting on dots, so .hover (state class) is preserved
    name = re.sub(r'::?[\w-]+', '', name)
    # Strip leading . or # from CSS selectors
    name = re.sub(r'^[.#]+', '', name)
    # Split camelCase BEFORE other splitting so MobileAccount → ['Mobile', 'Account']
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', '-', name)
    # Split on dash, underscore, space, dot, CSS combinators (> + ~ spaces)
    parts = re.split(r'[\s\-_.\>\+\~]+', name)
    parts = [p for p in parts if p and not re.match(r'^\d+$', p)]  # drop pure number parts
    return parts[-1].lower() if parts else 'unknown'


def prop_to_varfrag(prop):
    """border-radius → border_radius"""
    return prop.strip().lower().replace('-', '_')


def make_alias(source_name, prop):
    """$button-border-radius  $card-font-size"""
    lw = last_word(source_name)
    return f'${lw}-{prop.strip().lower()}'


def normalize(v):
    return re.sub(r'\s+', ' ', v.strip().lower())


def is_scss_safe(value):
    """Return False if value contains JS/TS expressions that are not valid SCSS."""
    v = value.strip()
    if not v:                                          return False
    if '${' in v:                                      return False   # JS template literal
    if '=>' in v:                                      return False   # JS arrow function
    if 'true' == v.lower():                            return False   # JS boolean
    if 'false' == v.lower():                           return False   # JS boolean
    if v.startswith('c.'):                             return False   # JS object access
    if re.search(r'\bc\.[a-zA-Z]', v):                return False   # JS object e.g. c.base
    if 'COPIES' in v:                                  return False   # JS constant
    if re.search(r'\b(const|let|var|function|return)\b', v): return False
    if re.search(r'[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(', v) and \
       not re.match(r'^(rgba?|hsla?|linear-gradient|radial-gradient|'
                    r'conic-gradient|calc|clamp|min|max|env|var)\s*\(', v):
        return False   # unknown JS function call
    return True


# ── Report type detector ──────────────────────────────────────────────

def detect_report_type(path):
    """
    Reads the first few lines to decide which report format this is.
    Returns 'pipeline', 'report1', or 'report2'.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines()[:6]:
        if 'PIPELINE FILE' in line:
            return 'pipeline'
        if 'REPORT 2' in line or 'PER PROPERTY' in line:
            return 'report2'
        if 'REPORT 1' in line:
            return 'report1'
    return 'report2'   # default fallback


# ── Pipeline file parser ──────────────────────────────────────────────

def parse_pipeline(path):
    """
    Parse the pipeline file format produced by scan_params.py:

      border-radius   | 25px    | pop-button
      border-radius   | 1px     | Card / card-header

    Returns list of (prop, value, source_label) — same shape as the
    other parsers so the rest of the script needs no changes.
    """
    text    = path.read_text(encoding="utf-8", errors="ignore")
    entries = []

    for line in text.splitlines():
        s = line.strip()

        # Skip headers, separators, blank lines
        if not s:
            continue
        if s.startswith('╔') or s.startswith('║') or s.startswith('╚'):
            continue
        if set(s) <= set('─═ '):
            continue
        if s.startswith('Generated') or s.startswith('Entries') or s.startswith('PROPERTY'):
            continue

        # Data line: "  border-radius   25px   pop-button"
        # Columns are space-aligned, not pipe-separated
        # prop is the first token(s) matching a known CSS prop,
        # source is everything at the end after the value

        # Split into tokens and match known prop at start
        parts = s.split()
        if len(parts) < 3:
            continue

        # Try to find the CSS property (may be multi-word like border-top-left-radius)
        prop = None
        rest_start = 0
        for length in range(min(4, len(parts)), 0, -1):
            candidate = '-'.join(parts[:length]).lower()
            if is_valid_css_prop(candidate):
                prop       = candidate
                rest_start = length
                break

        if not prop:
            continue

        # Everything after the prop is "value   source"
        # Value ends at the source label — source may contain spaces
        # The columns are padded so value is the next whitespace-separated
        # token(s) and source is the remainder
        # Since values are never empty and sources follow, split conservatively:
        # value = parts[rest_start], source = rest joined
        if rest_start >= len(parts):
            continue

        value  = parts[rest_start]
        source = ' '.join(parts[rest_start + 1:]).strip() if rest_start + 1 < len(parts) else 'unknown'

        if value and is_valid_css_prop(prop) and is_scss_safe(value):
            entries.append((prop, value, source))

    return entries


# ── Report 1 parser ───────────────────────────────────────────────────

def parse_report1(path):
    """
    Parse Report 1 format — handles both single-file and directory modes.

    Single-file mode (grouped by section comment header):
      ══...  ── Card  ══...
        card-header {
          border-radius: 0.25rem
        }
      → entries as (prop, value, selector, section_name)

    Directory mode (grouped by filename):
      ══...  card.jsx  ══...
        .pop-button {
          border-radius: 25px
        }
      → entries as (prop, value, selector, filename)

    In both cases the selector is the source of truth (Rule 1).
    The section_name/filename becomes the header note (Rule 3).

    Returns list of (prop, value, source_label) — same shape as parse_report2
    so the rest of the script needs no changes.

    source_label is the selector.
    When a section note exists it is embedded as "note / selector"
    so split_label() can unpack it exactly as Rule 3 requires.
    """
    text    = path.read_text(encoding="utf-8", errors="ignore")
    entries = []

    current_section  = None   # section header or filename
    current_selector = None   # .pop-button / card-header / etc.
    inside_block     = False
    waiting_section  = False  # True = next real line is a section name

    for line in text.splitlines():
        s = line.strip()

        # Skip box-drawing header lines
        if not s or s.startswith('╔') or s.startswith('║') or s.startswith('╚'):
            continue

        # ══ section separator — next meaningful line will be the section name
        if set(s) <= set('═ '):
            waiting_section  = True
            current_selector = None
            inside_block     = False
            continue

        # Opening brace line — always takes priority, even if waiting_section
        # e.g. the second ══ line right before ".pop-button {" would misread it
        m = re.match(r'^(.+?)\s*\{', s)
        if m and not inside_block:
            # If we were waiting for a section name but got a block opener,
            # the previous ══ was just a visual separator — don't update section
            waiting_section  = False
            current_selector = m.group(1).strip()
            inside_block     = True
            continue

        # Section name line — no { present, appears right after ══
        if waiting_section:
            candidate = re.sub(r'^[─\s]+', '', s).strip()
            if candidate:
                current_section = candidate
            waiting_section = False
            continue

        # Closing brace — end of block
        if s == '}' and inside_block:
            inside_block     = False
            current_selector = None
            continue

        # Property line inside a block:  "border-radius: 0.25rem"
        if inside_block and current_selector and ':' in s:
            parts = s.split(':', 1)
            prop  = parts[0].strip()
            value = parts[1].strip()
            if prop and value and is_valid_css_prop(prop) and is_scss_safe(value):
                # Build source label:
                #   If we have a section note → embed as "note / selector" (Rule 3)
                #   Otherwise just the selector (Rule 1)
                if current_section:
                    source_label = f"{current_section} / {current_selector}"
                else:
                    source_label = current_selector
                entries.append((prop, value, source_label))

    return entries


# ── Report 2 parser ───────────────────────────────────────────────────

def parse_report2(path):
    """
    Parse Report 2 (per-property) format:

    ──────────────────────────────────────
      border-radius  (7 unique values)
    ──────────────────────────────────────
      16px                  ← card.jsx
      8px                   ← button.jsx

    Returns list of (prop, value, source_label)
    where source_label is the className/header/section captured by scan_params.py
    """
    text    = path.read_text(encoding="utf-8", errors="ignore")
    entries = []

    current_prop = None

    for line in text.splitlines():
        s = line.strip()

        if not s or s.startswith('╔') or s.startswith('║') or s.startswith('╚'):
            continue
        if set(s) <= set('─═ '):
            continue

        # Property header: "border-radius  (7 unique values)"
        m = re.match(r'^([\w\-]+)\s+\(\d+\s+unique\s+value', s)
        if m:
            current_prop = m.group(1).strip()
            continue

        # Value line: "  16px    ← card.jsx, button.jsx"
        if current_prop and '←' in s:
            parts = s.split('←', 1)
            value = parts[0].strip()
            files = [f.strip() for f in parts[1].split(',')]
            if value and is_valid_css_prop(current_prop) and is_scss_safe(value):
                for f in files:
                    entries.append((current_prop, value, f))
            continue

        if current_prop and s and not s.startswith('─') and not s.startswith('═'):
            if is_valid_css_prop(current_prop) and is_scss_safe(s):
                entries.append((current_prop, s, 'unknown'))

    return entries


# ── Source label helpers ──────────────────────────────────────────────

def split_label(label):
    """
    The label stored in the report may be:
      "card-header"                        → className only       → ('card-header', None)
      "Card / card-header"                 → header / className   → ('card-header', 'Card')
      "[inline style #1]"                  → bracketed fallback   → ('[inline style #1]', None)
      "Main / [inline style #2]"           → header / bracketed   → ('Main', None)
        ↑ right side is bracketed so we fall back to using the
          LEFT side (section name) as the source, with no note

    Returns (class_name, header_note)
      class_name  → used for last_word() → alias prefix
      header_note → written as a comment next to the alias (Rule 3)
    """
    if ' / ' in label:
        parts      = label.split(' / ', 1)
        left       = parts[0].strip()   # section name / filename
        right      = parts[1].strip()   # selector / className

        # If the right side is a bracketed fallback like [inline style #2]
        # or a spelled-out fallback like inline-style-three,
        # it has no real className — use the left (section) as source instead
        if right.startswith('[') or re.match(r'^inline-style-', right):
            return left, None

        return right, left

    # Pure bracketed fallback — no className, no note
    if label.startswith('['):
        return label, None

    # Plain className or selector
    return label, None


# ── colors.scss builder ───────────────────────────────────────────────

def build_colors_scss(color_entries, light_dark_theme):
    """
    color_entries: list of (prop, value, source_label)
                   where prop is in COLOR_PROPS

    light_dark_theme: dict with keys 'light' and 'dark'
                      each a dict of --varname → raw_value
                      (empty if no theme detected)

    Returns the full colors.scss content string.
    """
    # Collect all unique color raw values across ALL color properties
    unique_colors = OrderedDict()
    for prop, value, source in color_entries:
        norm = normalize(value)
        if norm and norm not in unique_colors:
            unique_colors[norm] = value.strip()

    # Assign --a, --b, --c ... to each unique color
    var_gen  = gen_var_names()
    var_map  = {norm: next(var_gen) for norm in unique_colors}   # norm → --a

    lines = []
    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/colors.scss')
    lines.append(f'// Master color contract for the entire system.')
    lines.append(f'// All other color files reference these var(--x) aliases.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # ── :root block
    if light_dark_theme:
        # Write :root-light and :root-dark
        for theme_name, theme_vars in light_dark_theme.items():
            lines.append(f':root-{theme_name} {{')
            for varname, raw in theme_vars.items():
                lines.append(f'  {varname}: {raw};')
            lines.append('}')
            lines.append('')
    else:
        lines.append(':root {')
        max_var_len = max(len(v) for v in var_map.values()) if var_map else 4
        for norm, var in var_map.items():
            display = unique_colors[norm]
            pad     = max(1, max_var_len + 4 - len(var))
            lines.append(f'  {var}:{" " * pad}{display};')
        lines.append('}')
        lines.append('')

    # ── "color" parameter aliases (the CSS property literally named "color")
    color_prop_entries = [(p, v, s) for p, v, s in color_entries if p == 'color']

    if color_prop_entries:
        lines.append('')
        lines.append(f'// {"─" * 44}')
        lines.append(f'// color parameter aliases')
        lines.append(f'// {"─" * 44}')
        lines.append('')

        # Group by source
        # seen_aliases: alias → var already assigned
        source_groups = defaultdict(list)
        seen_aliases  = {}
        for prop, value, source in color_prop_entries:
            norm  = normalize(value)
            var   = var_map.get(norm)
            if not var:
                continue
            class_name, header = split_label(source)
            alias = make_alias(class_name, prop)

            if alias in seen_aliases:
                if seen_aliases[alias] == var:
                    # Same alias, same value → discard silently
                    continue
                else:
                    # Same alias, different value → number it
                    base  = alias
                    count = 2
                    while alias in seen_aliases and seen_aliases[alias] != var:
                        alias = f'{base}-{count}'
                        count += 1
                    if alias in seen_aliases:
                        continue

            seen_aliases[alias] = var
            fw = last_word(class_name)
            source_groups[fw].append((alias, var, header))

        for group_key, alias_list in sorted(source_groups.items()):
            lines.append(f'// ── {group_key} {"─" * max(1, 46 - len(group_key))}')
            max_alias_len = max(len(a) for a, _, _ in alias_list)
            for alias, var, header in sorted(alias_list):
                pad     = max(1, max_alias_len + 4 - len(alias))
                comment = f'  // {header}' if header else ''
                lines.append(f'{alias}:{" " * pad}var({var});{comment}')
            lines.append('')

    return '\n'.join(lines), var_map


# ── Color property file builder ───────────────────────────────────────

def build_color_prop_scss(prop, entries, var_map):
    """
    Builds a color-type dec file (background-color.scss, border-color.scss, etc.)
    Uses var(--x) from colors.scss — no hardcoded values.

    entries: list of (value, source_label)
    var_map: norm → --varname  (from colors.scss)
    """
    prop_kebab = prop.strip().lower()
    lines      = []

    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/{prop_kebab}.scss')
    lines.append(f'// Every {prop_kebab} in the system.')
    lines.append(f'// Uses var(--x) from colors.scss — never hardcoded values.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Group aliases by source word
    # seen_aliases: alias → var already assigned, so we can detect value conflicts
    source_groups = defaultdict(list)
    seen_aliases  = {}

    for value, source in entries:
        norm  = normalize(value)
        var   = var_map.get(norm)
        if not var:
            continue
        class_name, header = split_label(source)
        alias = make_alias(class_name, prop)

        if alias in seen_aliases:
            if seen_aliases[alias] == var:
                # Same alias, same value → exact duplicate, discard silently
                continue
            else:
                # Same alias, different value → number it
                base  = alias
                count = 2
                while alias in seen_aliases and seen_aliases[alias] != var:
                    alias = f'{base}-{count}'
                    count += 1
                if alias in seen_aliases:
                    continue

        seen_aliases[alias] = var
        fw = last_word(class_name)
        source_groups[fw].append((alias, var, header))

    if not source_groups:
        return None

    max_alias_len = max(
        len(a) for group in source_groups.values() for a, _, _ in group
    )

    for group_key, alias_list in sorted(source_groups.items()):
        # Use header as section comment if available
        headers = [h for _, _, h in alias_list if h]
        section_label = f'{group_key.upper()} — {headers[0]}' if headers else group_key.upper()
        lines.append(f'// ── {section_label} {"─" * max(1, 46 - len(section_label))}')
        for alias, var, header in sorted(alias_list):
            pad     = max(1, max_alias_len + 4 - len(alias))
            comment = f'  // {header}' if header else ''
            lines.append(f'{alias}:{" " * pad}var({var});{comment}')
        lines.append('')

    return '\n'.join(lines)


# ── Non-color dec file builder (unchanged primitive system) ───────────

def build_noncolor_scss(prop, entries):
    """
    Builds a non-color dec file using $_a_ $_b_ primitives.
    entries: list of (value, source_label)
    """
    # Unique raw values in order of first appearance
    unique_vals = OrderedDict()
    for value, source in entries:
        norm = normalize(value)
        if norm and norm not in unique_vals:
            unique_vals[norm] = value.strip()

    if not unique_vals:
        return None

    name_gen = gen_primitive_names()
    prim_map = {norm: next(name_gen) for norm in unique_vals}

    # Build aliases
    # seen_aliases: alias → primitive already assigned
    aliases      = OrderedDict()
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
                # Same alias, same primitive → exact duplicate, discard silently
                continue
            else:
                # Same alias, different value → number it
                base  = alias
                count = 2
                while alias in seen_aliases and seen_aliases[alias] != prim:
                    alias = f'{base}-{count}'
                    count += 1
                if alias in seen_aliases:
                    continue

        seen_aliases[alias] = prim
        aliases[alias] = (prim, header)

    prop_kebab = prop.strip().lower()
    lines      = []

    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/{prop_kebab}.scss')
    lines.append(f'// Every {prop_kebab} value in the system.')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'//')
    lines.append(f'// Usage: @use "tokens" as *;')
    lines.append(f'// Then:  {prop_kebab}: $component-{prop_kebab};')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Primitives block
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

    # Aliases block grouped by source word
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 44}')
    lines.append('')

    source_groups = defaultdict(list)
    for alias, (prim, header) in aliases.items():
        fw = last_word(alias.lstrip('$').split('-')[0])
        # alias is like $button-border-radius → group key is "button"
        parts = alias.lstrip('$').split('-')
        fw = parts[0]
        source_groups[fw].append((alias, prim, header))

    max_alias_len = max(len(a) for a in aliases) if aliases else 10

    for group_key, alias_list in sorted(source_groups.items()):
        headers = [h for _, _, h in alias_list if h]
        section_label = f'{group_key.upper()} — {headers[0]}' if headers else group_key.upper()
        lines.append(f'// ── {section_label} {"─" * max(1, 46 - len(section_label))}')
        for alias, prim, header in sorted(alias_list):
            pad     = max(1, max_alias_len + 4 - len(alias))
            comment = f'  // {header}' if header else ''
            lines.append(f'{alias}:{" " * pad}{prim};{comment}')
        lines.append('')

    return '\n'.join(lines)




# ── Box-shadow dec file builder ──────────────────────────────────────
#
# box-shadow is special — two separate components per layer:
#   1. position/blur/spread  →  generic $_a_ primitives (no component name)
#   2. color                 →  var(--x) from colors.scss (already declared)
#
# Declared separately first, then combined into a named alias:
#
#   $_a_: 0px 4px 5px;
#   $_b_: 3px 0px 0px;
#
#   $button-box-shadow: $_a_ var(--e);
#   $hover-box-shadow:  $_a_ var(--e), $_b_ var(--f), $_c_ var(--g);
#
# Pairing is positional — first position value scanned for a component
# pairs with first color scanned for that same component, etc.
# ─────────────────────────────────────────────────────────────────────

BS_COLOR_RE = re.compile(
    r'''(?x)^(
        \#[0-9a-fA-F]{3,8}|
        rgba?\([^)]+\)|
        hsla?\([^)]+\)|
        black|white|transparent|currentColor|
        red|blue|green|yellow|orange|purple|pink|
        gray|grey|navy|teal|silver|gold|lime|aqua|
        fuchsia|maroon|olive
    )$''',
    re.IGNORECASE | re.VERBOSE
)

def is_shadow_color(token):
    return bool(BS_COLOR_RE.match(token.strip()))


def split_shadow_layers(raw_value):
    """Split a raw box-shadow string into individual comma-separated layers,
    ignoring commas inside parentheses (rgba etc)."""
    layers, depth, current = [], 0, ''
    for ch in raw_value:
        if ch == '(': depth += 1; current += ch
        elif ch == ')': depth -= 1; current += ch
        elif ch == ',' and depth == 0:
            layers.append(current.strip()); current = ''
        else:
            current += ch
    if current.strip():
        layers.append(current.strip())
    return layers


def split_layer_parts(layer):
    """Split one shadow layer into (position_string, color_or_None).
    The color token, if present, is always last."""
    tokens, depth, current = [], 0, ''
    for ch in layer:
        if ch == '(': depth += 1; current += ch
        elif ch == ')': depth -= 1; current += ch
        elif ch == ' ' and depth == 0:
            if current.strip(): tokens.append(current.strip()); current = ''
        else:
            current += ch
    if current.strip():
        tokens.append(current.strip())
    if tokens and is_shadow_color(tokens[-1]):
        return ' '.join(tokens[:-1]), tokens[-1]
    return ' '.join(tokens), None


def build_boxshadow_scss(all_shadow_entries, var_map):
    """
    all_shadow_entries: list of (value, source_label) for box-shadow property.
    var_map: norm → --varname from colors.scss.

    Each raw value may be a multi-layer shadow string.
    Splits layers, separates position from color, assigns primitives to
    positions, looks up var(--x) for colors, then builds combined aliases.
    """
    from collections import OrderedDict as OD

    # ── Build per-source ordered layer list
    source_layers = OD()
    for raw_value, source in all_shadow_entries:
        layers = split_shadow_layers(raw_value)
        for layer in layers:
            pos, color = split_layer_parts(layer)
            if pos or color:
                source_layers.setdefault(source, []).append((pos, color))

    # ── Collect all unique position strings → $_a_ primitives
    unique_positions = OD()
    for layers in source_layers.values():
        for pos, color in layers:
            if pos:
                norm = normalize(pos)
                if norm and norm not in unique_positions:
                    unique_positions[norm] = pos

    name_gen = gen_primitive_names()
    prim_map = {norm: next(name_gen) for norm in unique_positions}

    # ── Build file content
    lines = []
    lines.append(f'// {"═" * 67}')
    lines.append(f'// decs/box-shadow.scss')
    lines.append(f'// Every box-shadow in the system.')
    lines.append(f'// Position/blur/spread → generic $_x_ primitives')
    lines.append(f'// Colors              → var(--x) from colors.scss')
    lines.append(f'// Combined aliases    → $component-box-shadow: $_x_ var(--x), ...')
    lines.append(f'// Generated {datetime.now().strftime("%Y-%m-%d")} by generate_decs.py')
    lines.append(f'// {"═" * 67}')
    lines.append('')

    # Position primitives
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Raw position / blur / spread values')
    lines.append(f'// {"─" * 44}')
    lines.append('')
    max_prim_len = max((len(p) for p in prim_map.values()), default=6)
    for norm, prim in prim_map.items():
        pad = max(1, max_prim_len + 4 - len(prim))
        lines.append(f'{prim}:{" " * pad}{unique_positions[norm]};')
    lines.append('')
    lines.append('')

    # Combined aliases
    lines.append(f'// {"─" * 44}')
    lines.append(f'// Component aliases')
    lines.append(f'// {"─" * 44}')
    lines.append('')

    seen_aliases = {}
    combined     = []

    for source, layers in source_layers.items():
        class_name, header = split_label(source)
        alias = make_alias(class_name, 'box-shadow')

        layer_parts = []
        for pos, color in layers:
            prim = prim_map.get(normalize(pos), '') if pos else ''
            var  = var_map.get(normalize(color), '') if color else ''
            if prim and var:
                layer_parts.append(f'{prim} var({var})')
            elif prim:
                layer_parts.append(prim)
            elif var:
                layer_parts.append(f'var({var})')

        if not layer_parts:
            continue

        rendered = ', '.join(layer_parts)

        if alias in seen_aliases:
            if seen_aliases[alias] == rendered:
                continue
            else:
                base, count = alias, 2
                while alias in seen_aliases and seen_aliases[alias] != rendered:
                    alias = f'{base}-{count}'
                    count += 1
                if alias in seen_aliases:
                    continue

        seen_aliases[alias] = rendered
        fw      = last_word(class_name)
        comment = f'  // {header}' if header else ''
        max_alias_len = max((len(a) for a in seen_aliases), default=10)
        pad = max(1, max_alias_len + 4 - len(alias))
        lines.append(f'// ── {fw.upper()} {"─" * max(1, 46 - len(fw))}')
        lines.append(f'{alias}:{" " * pad}{rendered};{comment}')
        lines.append('')
        combined.append(alias)

    # $shadow-N block at the bottom
    if combined:
        lines.append('')
        lines.append(f'// {"─" * 44}')
        lines.append(f'// Combined — $shadow-N references alias names only')
        lines.append(f'// {"─" * 44}')
        lines.append('')
        for i, alias in enumerate(combined, 1):
            lines.append(f'$shadow-{i}:  {alias};')
        lines.append('')

    return '\n'.join(lines)

# ── Light/dark theme detector ─────────────────────────────────────────

def detect_light_dark(report_path):
    """
    Look in the same directory for any source files that define
    :root-light / :root-dark or [data-theme=light] / [data-theme=dark].
    Returns dict: {'light': {--var: value}, 'dark': {--var: value}}
    or empty dict if not found.
    """
    # Not implemented in this version — would require scanning source files
    # directly. Returns empty so :root is always used unless added later.
    return {}


# ── Main ──────────────────────────────────────────────────────────────

def prompt_output_dir():
    """
    Ask the user where to place the styles/ folder.
    Creates:  <chosen_path>/decs/
              <chosen_path>/effs/
    Returns the decs Path (effs is handled by move_effs).
    """
    print()
    print("  Where should the styles folder be created?")
    print("  (decs/ and effs/ will be placed inside this directory)")
    print()
    print(f"  Default: {Path.cwd() / 'styles'}")
    print()

    while True:
        raw = input("  Path (or Enter for default): ").strip().strip('"').strip("'")
        if not raw:
            base = Path.cwd() / "styles"
        else:
            base = Path(raw)

        # If they gave a path ending in decs/ or styles/ unwrap it
        if base.name in ("decs", "effs"):
            base = base.parent

        try:
            decs_dir = base / "decs"
            decs_dir.mkdir(parents=True, exist_ok=True)
            effs_dir = base / "effs"
            effs_dir.mkdir(parents=True, exist_ok=True)
            print(f"\n  ✓ Output root → {base}")
            print(f"    decs/ and effs/ ready\n")
            return decs_dir
        except Exception as e:
            print(f"  ✗ Could not create directory: {e}")


def prompt_report():
    print()
    print(f"  Looking in: {PARAMS_DIR}")

    if not PARAMS_DIR.exists():
        print("  ✗ Parameters directory not found. Run scan_params.py first.")
        sys.exit(1)

    reports = sorted(PARAMS_DIR.glob("*.txt"))
    if not reports:
        print("  ✗ No reports found. Run scan_params.py first.")
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
    print("║         decs file generator                          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Reads a scan_params.py Report 1 OR Report 2 file.")
    print("  Auto-detects which format and parses accordingly.")
    print("  Generates one .scss decs file per CSS property.")
    print()
    print("  Naming:  LAST word of className + full property name")
    print("    pop-button → $button-border-radius")
    print("    static-card → $card-font-size")
    print()
    print("  Colors:  var(--a) system via colors.scss")
    print("  Other:   $_a_ primitive system")

    report_path  = prompt_report()
    report_type  = detect_report_type(report_path)
    type_label   = {
        'pipeline': 'Pipeline file (recommended)',
        'report1':  'Report 1 (per selector)',
        'report2':  'Report 2 (per property)',
    }.get(report_type, report_type)

    print(f"\n  Using: {report_path.name}")
    print(f"  Detected: {type_label}\n")

    if report_type == 'pipeline':
        entries = parse_pipeline(report_path)
    elif report_type == 'report1':
        entries = parse_report1(report_path)
    else:
        entries = parse_report2(report_path)

    if not entries:
        print("  ✗ No entries found.")
        print("  Make sure the file is a Report 1 or Report 2 output from scan_params.py.")
        sys.exit(1)

    print(f"  Found {len(entries)} property entries across "
          f"{len(set(e[0] for e in entries))} CSS properties.\n")

    # Pull box-shadow out first — it has its own special builder
    shadow_entries   = [(v, s) for p, v, s in entries if p == 'box-shadow']
    color_entries    = [(p, v, s) for p, v, s in entries if p in COLOR_PROPS and p != 'box-shadow']
    noncolor_entries = [(p, v, s) for p, v, s in entries if p not in COLOR_PROPS and p != 'box-shadow']

    # Group non-color by property
    by_prop = defaultdict(list)
    for prop, value, source in noncolor_entries:
        by_prop[prop].append((value, source))

    OUTPUT_DIR = prompt_output_dir()
    generated  = 0

    # ── 1. colors.scss FIRST (mandatory)
    light_dark = detect_light_dark(report_path)
    colors_content, var_map = build_colors_scss(color_entries, light_dark)
    colors_path = OUTPUT_DIR / "colors.scss"
    colors_path.write_text(colors_content, encoding="utf-8")
    generated += 1
    print(f"  ✓  colors.scss  (master color contract — {len(var_map)} unique colors)")

    # ── 2. box-shadow.scss (special builder)
    if shadow_entries:
        shadow_content = build_boxshadow_scss(shadow_entries, var_map)
        shadow_path    = OUTPUT_DIR / "box-shadow.scss"
        shadow_path.write_text(shadow_content, encoding="utf-8")
        generated += 1
        unique_shadows = len(set(normalize(v) for v, _ in shadow_entries))
        print(f"  ✓  box-shadow.scss                   {unique_shadows} unique values")

    # ── 3. Color-type property files
    color_by_prop = defaultdict(list)
    for prop, value, source in color_entries:
        if prop != 'color':    # 'color' aliases already live in colors.scss
            color_by_prop[prop].append((value, source))

    for prop, prop_entries in sorted(color_by_prop.items()):
        content = build_color_prop_scss(prop, prop_entries, var_map)
        if not content:
            print(f"  –  {prop}.scss  (no usable values)")
            continue
        filename = prop.strip().lower() + ".scss"
        out_path = OUTPUT_DIR / filename
        out_path.write_text(content, encoding="utf-8")
        generated += 1
        print(f"  ✓  {filename:<35} (color file — uses var(--x))")

    # ── 4. Non-color property files
    for prop, prop_entries in sorted(by_prop.items()):
        content = build_noncolor_scss(prop, prop_entries)
        if not content:
            print(f"  –  {prop}.scss  (no usable values)")
            continue
        filename = prop.strip().lower().replace(' ', '-') + ".scss"
        out_path = OUTPUT_DIR / filename
        out_path.write_text(content, encoding="utf-8")
        generated += 1
        unique_count = len(set(normalize(v) for v, _ in prop_entries))
        print(f"  ✓  {filename:<35} {unique_count} unique values")

    print(f"\n  ✓ Generated {generated} decs files")
    print(f"  ✓ Saved to → {OUTPUT_DIR}\n")

    # ── 5. Auto-move eff files into styles/effs/
    move_effs(OUTPUT_DIR)


# ── Eff properties + mover ────────────────────────────────────────────
#
# After all dec files are written, any file whose property name belongs
# to the "behavior/motion" category gets moved from decs/ into effs/.
# The $_a_ primitive names are local to each file so there is no conflict.
# ─────────────────────────────────────────────────────────────────────

EFF_PROPS = {
    # Transform & motion
    "transform", "transform-origin",
    # Transition
    "transition", "transition-property", "transition-duration",
    "transition-delay", "transition-timing-function",
    # Animation
    "animation", "animation-name", "animation-duration",
    "animation-delay", "animation-timing-function",
    "animation-iteration-count", "animation-direction",
    "animation-fill-mode", "animation-play-state",
    # Flex behavior
    "flex", "flex-direction", "flex-wrap", "flex-grow",
    "flex-shrink", "flex-basis", "flex-flow", "order",
    "place-items", "place-content", "place-self",
    # Interaction
    "cursor", "pointer-events", "user-select", "appearance",
    "touch-action", "resize", "will-change",
    # Visibility / layer
    "opacity", "isolation", "visibility",
    # Scroll behavior
    "scroll-behavior", "scroll-snap-type", "scroll-snap-align",
    "overscroll-behavior",
    # Outline (interaction feedback, not decoration)
    "outline", "outline-width", "outline-offset",
    # Background motion
    "background-size", "background-position",
    "background-attachment", "background-repeat",
    # Object
    "object-fit", "object-position",
    # Offset / clip path
    "clip-path", "offset-path",
}


def move_effs(decs_dir):
    """
    Scan decs_dir for .scss files whose stem matches an eff property name.
    Move them into the sibling effs/ directory.
    """
    effs_dir = decs_dir.parent / "effs"
    effs_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for scss_file in sorted(decs_dir.glob("*.scss")):
        prop = scss_file.stem.lower()   # e.g. "transform", "flex-direction"
        if prop in EFF_PROPS:
            dest = effs_dir / scss_file.name
            scss_file.rename(dest)
            moved += 1
            print(f"  → effs/  {scss_file.name}")

    if moved:
        print(f"\n  ✓ Moved {moved} eff file{'s' if moved != 1 else ''} → {effs_dir}")
    else:
        print(f"  –  No eff files found to move")


if __name__ == "__main__":
    run()