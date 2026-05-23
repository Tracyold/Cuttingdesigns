#!/usr/bin/env python3
"""
rewrite_mockup.py

Transforms a messy AI mockup into a clean AnatomyMatters project structure.

Input:   one messy .tsx / .jsx mockup file
Output:
  behaviors/        — behavior components copied from _library/
  components/matr/  — clean component shells using those behaviors

Per component it:
  1. Strips behavior logic  (press/hover state, toggle, swipe, etc.)
  2. Strips inline styles   → replaces with className
  3. Detects props          → generates a typed interface
  4. Wraps root element     → in the right <Behavior> component
  5. Generates locs/ hints  → what CSS classes the component needs

Slop patterns detected and removed:
  ✗  const [pressed, setPressed] = useState(false)  → <Interactive>
  ✗  const [on, setOn] = useState(false)             → <Toggle>
  ✗  visible + animating + setTimeout                → <EnterExit>
  ✗  touchstart/touchmove drag logic                 → <SwipeDismiss>
  ✗  style={{ ... }} on every element               → className="..."
  ✗  ternary chains in style props                   → is-* classes
  ✗  magic numbers inline                            → flagged for decs/
  ✗  theme object (c.base, c.ink etc.)              → CSS custom properties
"""

import re
import sys
from pathlib import Path
from datetime import datetime
from textwrap import indent

EXTENSIONS = {'.tsx', '.jsx'}


# ── Behavior map ───────────────────────────────────────────────────────────────
# detector name → component name in library

BEHAVIOR_COMPONENT = {
    'useInteractive':  'Interactive',
    'useToggle':       'Toggle',
    'useClickOutside': 'ClickOutside',
    'useEnterExit':    'EnterExit',
    'useSwipeDismiss': 'SwipeDismiss',
    'usePinchZoomPan': 'PinchZoomPan',
    'useReadItems':    'ReadItems',
    'useTheme':        'Theme',
}

# CSS is-* classes each behavior drives
BEHAVIOR_CLASSES = {
    'Interactive':  ['is-pressed', 'is-hovered'],
    'Toggle':       ['is-open', 'is-closed'],
    'EnterExit':    ['is-visible', 'is-open', 'is-animating'],
    'SwipeDismiss': ['is-dragging'],
    'PinchZoomPan': ['is-zoomed'],
    'ReadItems':    ['is-new', 'is-read'],
    'Theme':        ['is-dark'],
}

# Slop detector patterns — (name, all_of patterns, any_of patterns)
DETECTORS = [
    ('useInteractive', [
        r'\bconst\s+\[(?:pressed|isPressed),\s*set(?:Pressed|IsPressed)\]\s*=\s*useState\(false\)',
        r'onMouseDown[^=]*=.*?set(?:Pressed|IsPressed)\(true\)',
    ], [
        r'onMouseUp[^=]*=.*?set(?:Pressed|IsPressed)\(false\)',
        r'onTouchStart[^=]*=.*?set(?:Pressed|IsPressed)\(true\)',
    ]),
    ('useToggle', [
        r'useState\(false\)',
        r'set\w+\((?:v|p|s|prev)\s*=>\s*!(?:v|p|s|prev)\)',
    ], []),
    ('useClickOutside', [
        r'useEffect\s*\(',
        r'document\.addEventListener\s*\(\s*["\']mousedown["\']',
        r'\.current\s*&&\s*!.*?\.contains\s*\(',
    ], []),
    ('useEnterExit', [
        r'const\s+\[(?:visible|isVisible),\s*set(?:Visible|IsVisible)\]\s*=\s*useState\(false\)',
        r'const\s+\[(?:animating|isAnimating),\s*set(?:Animating|IsAnimating)\]\s*=\s*useState\(false\)',
        r'setTimeout\s*\(',
    ], []),
    ('useSwipeDismiss', [
        r'addEventListener\s*\(\s*["\']touchstart["\']',
        r'addEventListener\s*\(\s*["\']touchmove["\']',
        r'translateX',
    ], [r'THRESHOLD', r'e\.touches\[0\]']),
    ('usePinchZoomPan', [
        r'e\.touches\.length\s*===?\s*2',
        r'const\s+\[scale,\s*setScale\]',
        r'const\s+\[pos,\s*setPos\]',
    ], []),
    ('useReadItems', [
        r'useState\s*\(\s*new\s+Set\s*\(',
        r'new\s+Set\s*\(\s*\[\.\.\.(?:prev|p)',
    ], []),
    ('useTheme', [
        r'const\s+\[dark,\s*setDark\]\s*=\s*useState\s*\(\s*(?:true|false)\s*\)',
        r'setDark\s*\(',
    ], []),
]


# ── Utilities ──────────────────────────────────────────────────────────────────

def to_kebab(name: str) -> str:
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '-', name)
    return s.lower()

def to_bem(comp: str, child: str) -> str:
    base = to_kebab(comp)
    child_slug = re.sub(r'[^a-zA-Z0-9]+', '-', child.lower()).strip('-')
    return f'{base}__{child_slug}'

def infer_type(prop_name: str) -> str:
    """Guess a TypeScript type from a prop name."""
    bools   = ['isNew', 'isActive', 'disabled', 'loading', 'open',
                'active', 'selected', 'checked', 'hasNew', 'dot', 'fab', 'grid']
    nums    = ['count', 'index', 'size', 'width', 'height', 'num', 'stars', 'n']
    fns     = ['onClick', 'onChange', 'onClose', 'onDismiss', 'onFav',
                'onCircleClick', 'handleClick']
    arrays  = ['items', 'gems', 'roles', 'tiles', 'options', 'list']
    if any(prop_name == b or prop_name.startswith(b) for b in bools):
        return 'boolean'
    if any(prop_name == n or prop_name.endswith(n.capitalize()) for n in nums):
        return 'number'
    if prop_name in fns or prop_name.startswith('on'):
        return '() => void'
    if prop_name in arrays or prop_name.endswith('s'):
        return 'any[]'
    return 'string'


# ── Balanced brace extractor ───────────────────────────────────────────────────

def extract_braces(text: str, start: int):
    if start >= len(text) or text[start] != '{':
        return None, start
    i, depth, in_str, td = start + 1, 1, None, 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '\\' and i + 1 < len(text): i += 2; continue
            if in_str == '`':
                if c == '$' and i+1 < len(text) and text[i+1] == '{': td += 1; i += 2; continue
                if c == '{' and td > 0: td += 1
                elif c == '}' and td > 0: td -= 1; i += 1; continue
                if c == '`' and td == 0: in_str = None
            elif c == in_str: in_str = None
        else:
            if c in ('"', "'", '`'): in_str = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: return text[start+1:i], i+1
        i += 1
    return None, len(text)


# ── Component finder ───────────────────────────────────────────────────────────

def find_components_with_body(text: str) -> list:
    """
    Find every React component function and extract its body text.
    Returns [(name, signature_text, body_text)]
    """
    result = []
    pat = re.compile(
        r'(?:export\s+default\s+|export\s+)?'
        r'function\s+([A-Z]\w+)\s*'
        r'(\([^)]{0,400}\))\s*\{',
        re.DOTALL
    )
    for m in pat.finditer(text):
        name      = m.group(1)
        signature = m.group(2)
        brace_pos = text.index('{', m.end() - 1)
        body, _   = extract_braces(text, brace_pos)
        if body:
            result.append((name, signature, body))
    return result


# ── Prop extraction ────────────────────────────────────────────────────────────

def extract_prop_names(signature: str) -> list:
    """
    Extract prop names from a function signature string.
    '({ t, c, dark = true, onClick })' → ['t', 'c', 'dark', 'onClick']
    """
    # Strip outer parens + braces
    inner = re.sub(r'^\s*\(\s*\{', '', signature)
    inner = re.sub(r'\}\s*\)\s*$', '', inner)
    props = []
    for item in inner.split(','):
        item = item.strip()
        m = re.match(r'([A-Za-z_$]\w*)', item)
        if m and m.group(1) not in ('children',):
            props.append(m.group(1))
    return props


def detect_prop_usage(body: str, prop_names: list) -> dict:
    """
    Detect sub-property access on each prop.
    body uses t.label, t.num → {t: ['label', 'num']}
    """
    usage = {}
    for prop in prop_names:
        subs = re.findall(rf'\b{prop}\.([A-Za-z_]\w*)', body)
        if subs:
            # filter out common non-data subs
            subs = [s for s in subs if s not in
                    ('current', 'target', 'preventDefault', 'stopPropagation',
                     'style', 'classList', 'offsetWidth')]
            usage[prop] = sorted(set(subs))
        else:
            usage[prop] = []
    return usage


# ── Behavior detection ─────────────────────────────────────────────────────────

def detect_behaviors(comp_body: str) -> list:
    """Return list of behavior names detected in this component body."""
    found = []
    for name, all_of, any_of in DETECTORS:
        if not all(re.search(p, comp_body, re.DOTALL) for p in all_of):
            continue
        if any_of and not any(re.search(p, comp_body, re.DOTALL) for p in any_of):
            continue
        found.append(name)
    return found


# ── JSX child label extractor ──────────────────────────────────────────────────

def extract_jsx_labels(body: str) -> list:
    """
    Extract JSX comment labels: {/* Label */} → ['label', ...]
    Also detect rendered prop paths: {t.label} → 'label'
    """
    labels = []
    # JSX comments
    for m in re.finditer(r'\{/\*+\s*(.+?)\s*\*+/\}', body):
        name = m.group(1).strip()
        if len(name) < 40 and '\n' not in name:
            labels.append(name)
    return labels


def extract_rendered_props(body: str, prop_usage: dict) -> list:
    """
    Extract flat prop paths that appear in JSX render positions.
    {t.label} → 'label', {t.num} → 'num', etc.
    """
    rendered = []
    for prop, subs in prop_usage.items():
        for sub in subs:
            # Check if this sub-prop is actually rendered (appears in JSX {})
            if re.search(rf'\{{\s*{prop}\.{sub}\s*\}}', body):
                rendered.append(sub)
    return rendered


# ── Theme variable filter ──────────────────────────────────────────────────────

THEME_PROPS = {'c', 'theme', 'colors', 'dark', 'light', 'palette'}


# ── locs/ hint generator ───────────────────────────────────────────────────────

def generate_locs_hints(css_class: str, behaviors: list, children: list) -> list:
    """Generate the locs/ comment block."""
    lines = [f'// Add to locs/{css_class}.scss:']

    # Base class
    lines.append(f'//   .{css_class} {{ }}')

    # Behavior state classes
    for beh_key in behaviors:
        bname = BEHAVIOR_COMPONENT.get(beh_key, '')
        for cls in BEHAVIOR_CLASSES.get(bname, []):
            lines.append(f'//   .{css_class}.{cls} {{ }}')

    # Child element classes
    for child in children:
        lines.append(f'//   .{css_class}__{child} {{ }}')

    return lines


# ── matr/ file generator ───────────────────────────────────────────────────────

def generate_matr(
    comp_name:  str,
    behaviors:  list,   # ['useInteractive', 'useToggle', ...]
    props:      list,   # raw prop names
    prop_usage: dict,   # {prop: [sub, ...]}
    jsx_labels: list,
    rendered:   list,
) -> str:

    css_class   = to_kebab(comp_name)
    beh_names   = [BEHAVIOR_COMPONENT[b] for b in behaviors if b in BEHAVIOR_COMPONENT]
    primary_beh = beh_names[0] if beh_names else None

    lines = []

    # ── Imports ────────────────────────────────────────────────────────────────
    lines.append("import React from 'react';")
    for bname in beh_names:
        lines.append(f"import {{ {bname} }} from '../../behaviors/{bname}';")
    lines.append('')

    # ── Header comment ─────────────────────────────────────────────────────────
    dashes = '─' * max(1, 65 - len(comp_name))
    lines.append(f'// ── {comp_name} {dashes}')
    lines.append('//')
    if beh_names:
        lines.append(f'// Behaviors:  {", ".join(beh_names)}')
    lines.append(f'// CSS class:  .{css_class}')
    lines.append('//')

    # locs/ hints
    children_slugs = [re.sub(r'[^a-z0-9]+', '-', c.lower()).strip('-')
                      for c in (jsx_labels + rendered)
                      if c and c.lower() not in ('todo', 'add child elements')]
    children_slugs = list(dict.fromkeys(children_slugs))[:8]  # dedupe, cap at 8

    for hint in generate_locs_hints(css_class, behaviors, children_slugs):
        lines.append(hint)
    lines.append(f'// {"─" * 70}')
    lines.append('')

    # ── Props interface ────────────────────────────────────────────────────────
    lines.append(f'interface {comp_name}Props {{')

    clean_props = []   # props that make it into the interface
    has_onclick  = False

    for prop in props:
        if prop in THEME_PROPS:
            lines.append(f'  // {prop} removed — use CSS custom properties')
            continue
        subs = prop_usage.get(prop, [])
        if subs:
            lines.append(f'  // {prop} object → flattened:')
            for sub in subs:
                t = infer_type(sub)
                optional = '?' if sub not in ('label', 'title', 'name', 'id') else ''
                lines.append(f'  {sub}{optional}:  {t};')
                clean_props.append(sub)
        else:
            t = infer_type(prop)
            optional = '?' if t in ('boolean', '() => void', 'any[]') else ''
            lines.append(f'  {prop}{optional}:  {t};')
            clean_props.append(prop)
            if prop == 'onClick':
                has_onclick = True

    # Ensure onClick if Interactive
    if primary_beh == 'Interactive' and not has_onclick:
        lines.append('  onClick?:  () => void;')
        clean_props.append('onClick')

    # Ensure open/onClose if EnterExit or SwipeDismiss
    if primary_beh in ('EnterExit', 'SwipeDismiss'):
        if 'open' not in clean_props:
            lines.append('  open:      boolean;')
            clean_props.insert(0, 'open')
        if 'onClose' not in clean_props and 'onDismiss' not in clean_props:
            lines.append('  onClose:   () => void;')
            clean_props.append('onClose')

    lines.append('}')
    lines.append('')

    # ── Component function ─────────────────────────────────────────────────────
    props_str = ', '.join(clean_props)
    if len(props_str) > 60:
        # Multi-line destructure
        lines.append(f'export function {comp_name}({{')
        for p in clean_props:
            lines.append(f'  {p},')
        lines.append(f'}}: {comp_name}Props) {{')
    else:
        lines.append(f'export function {comp_name}({{ {props_str} }}: {comp_name}Props) {{')

    lines.append('  return (')

    # ── JSX body ───────────────────────────────────────────────────────────────
    jsx = _generate_jsx_body(
        comp_name, primary_beh, beh_names, clean_props,
        children_slugs, css_class
    )
    for jline in jsx:
        lines.append(f'    {jline}')

    lines.append('  );')
    lines.append('}')
    lines.append('')

    return '\n'.join(lines)


def _generate_jsx_body(
    comp_name:       str,
    primary_beh:     str | None,
    all_behs:        list,
    props:           list,
    children_slugs:  list,
    css_class:       str,
) -> list:
    """Generate the JSX return body."""

    lines = []
    is_new   = 'isNew'   in props
    is_active = 'active' in props or 'isActive' in props
    has_open  = 'open'   in props

    # ── Determine className expression ────────────────────────────────────────
    extra_classes = []
    if is_new:   extra_classes.append("isNew ? 'is-new' : ''")
    if is_active: extra_classes.append("active ? 'is-active' : ''")

    if extra_classes:
        class_expr = (
            f"{{['{css_class}', {', '.join(extra_classes)}]"
            f".filter(Boolean).join(' ')}}"
        )
    else:
        class_expr = f'"{css_class}"'

    # ── Behavior wrappers ──────────────────────────────────────────────────────

    # Special case: SwipeDismiss needs a render prop structure
    if 'SwipeDismiss' in all_behs and 'EnterExit' in all_behs:
        lines.append(f'<EnterExit open={{open}} className="{css_class}-wrapper">')
        lines.append(f'  <SwipeDismiss open={{open}} onDismiss={{onClose}}>')
        lines.append(f'    {{({{ drawerRef, scrimRef }}) => (')
        lines.append(f'      <>')
        lines.append(f'        <div ref={{scrimRef}}  className="scrim"  onClick={{onClose}} />')
        lines.append(f'        <div ref={{drawerRef}} className={class_expr}>')
        for slug in children_slugs[:5]:
            lines.append(f'          {{/* TODO: {slug} */}}')
            lines.append(f'          <div className="{css_class}__{slug}" />')
        if not children_slugs:
            lines.append(f'          {{/* TODO: add children */}}')
        lines.append(f'        </div>')
        lines.append(f'      </>')
        lines.append(f'    )}}')
        lines.append(f'  </SwipeDismiss>')
        lines.append(f'</EnterExit>')
        return lines

    if 'SwipeDismiss' in all_behs:
        lines.append(f'<SwipeDismiss open={{open}} onDismiss={{onClose}}>')
        lines.append(f'  {{({{ drawerRef, scrimRef }}) => (')
        lines.append(f'    <>')
        lines.append(f'      <div ref={{scrimRef}}  className="scrim"  onClick={{onClose}} />')
        lines.append(f'      <div ref={{drawerRef}} className={class_expr}>')
        for slug in children_slugs[:5]:
            lines.append(f'        <div className="{css_class}__{slug}" />')
        lines.append(f'      </div>')
        lines.append(f'    </>')
        lines.append(f'  )}}')
        lines.append(f'</SwipeDismiss>')
        return lines

    if 'EnterExit' in all_behs:
        lines.append(f'<EnterExit open={{open}} className={class_expr}>')
        _add_children(lines, comp_name, children_slugs, css_class, props, '  ')
        lines.append(f'</EnterExit>')
        return lines

    if 'Toggle' in all_behs:
        lines.append(f'<Toggle>')
        lines.append(f'  {{({{ on, toggle, className: toggleClass }}) => (')
        lines.append(f'    <div className={class_expr}>')
        lines.append(f'      {{/* TODO: add trigger element */}}')
        lines.append(f'      <button onClick={{toggle}}>toggle</button>')
        lines.append(f'      <div className={{`{css_class}__panel ${{toggleClass}}`}}>')
        lines.append(f'        {{/* TODO: add panel content */}}')
        lines.append(f'      </div>')
        lines.append(f'    </div>')
        lines.append(f'  )}}')
        lines.append(f'</Toggle>')
        return lines

    if 'PinchZoomPan' in all_behs:
        lines.append(f'<PinchZoomPan className={class_expr}>')
        lines.append(f'  {{({{ innerStyle, innerHandlers }}) => (')
        lines.append(f'    <div style={{innerStyle}} {{...innerHandlers}}>')
        lines.append(f'      {{/* TODO: add zoomable content */}}')
        lines.append(f'    </div>')
        lines.append(f'  )}}')
        lines.append(f'</PinchZoomPan>')
        return lines

    if 'Interactive' in all_behs:
        as_tag = '"button"' if any(p in props for p in ['onClick', 'onSubmit']) else '"div"'
        lines.append(f'<Interactive')
        lines.append(f'  as={as_tag}')
        lines.append(f'  className={class_expr}')
        if 'onClick' in props:
            lines.append(f'  onClick={{onClick}}')
        lines.append(f'>')
        _add_children(lines, comp_name, children_slugs, css_class, props, '  ')
        lines.append(f'</Interactive>')
        return lines

    # No behavior — plain wrapper
    lines.append(f'<div className={class_expr}>')
    _add_children(lines, comp_name, children_slugs, css_class, props, '  ')
    lines.append(f'</div>')
    return lines


def _add_children(lines, comp_name, children_slugs, css_class, props, pad):
    """Add child element stubs to the JSX output."""
    # Render any detected data props as child spans
    data_props = [p for p in props
                  if p not in ('onClick', 'onClose', 'onDismiss', 'open',
                               'isNew', 'active', 'isActive', 'dot', 'disabled')]
    rendered = data_props[:5]  # cap at 5

    if children_slugs:
        for slug in children_slugs[:6]:
            # Map slug back to a prop name if possible
            matching = next((p for p in rendered if p.lower() == slug.replace('-', '').lower()), None)
            if matching:
                lines.append(f'{pad}<span className="{css_class}__{slug}">{{{matching}}}</span>')
            else:
                lines.append(f'{pad}<span className="{css_class}__{slug}" />')
    elif rendered:
        for prop in rendered:
            slug = to_kebab(prop)
            lines.append(f'{pad}<span className="{css_class}__{slug}">{{{prop}}}</span>')
    else:
        lines.append(f'{pad}{{/* TODO: add children */}}')


# ── Library file copier ────────────────────────────────────────────────────────

def copy_behavior(beh_name: str, library_dir: Path, dest_dir: Path) -> bool:
    comp_name = BEHAVIOR_COMPONENT.get(beh_name, '')
    if not comp_name:
        return False
    src = library_dir / f'{comp_name}.tsx'
    if not src.exists():
        return False
    dst = dest_dir / f'{comp_name}.tsx'
    dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    return True


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(results: list, source_file: str) -> str:
    lines = [
        '╔══════════════════════════════════════════════════════════════════╗',
        '║  REWRITE REPORT                                                  ║',
        f'║  Source: {source_file:<59}║',
        '╚══════════════════════════════════════════════════════════════════╝',
        '',
        f'  Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        f'  Components processed: {len(results)}',
        '',
    ]

    for comp_name, behaviors, props, beh_names, css_class in results:
        lines.append(f'{"═" * 70}')
        lines.append(f'  ── {comp_name}  →  matr/{comp_name}.tsx')
        lines.append(f'{"═" * 70}')
        lines.append('')

        # Slop removed
        slop_map = {
            'useInteractive':  'press/hover state + inline handlers',
            'useToggle':       'boolean useState + inline toggle',
            'useClickOutside': 'useEffect + document.addEventListener',
            'useEnterExit':    'visible + animating + setTimeout pattern',
            'useSwipeDismiss': 'touchstart/touchmove/touchend drag logic',
            'usePinchZoomPan': 'pinch zoom + mouse pan state',
            'useReadItems':    'useState(new Set()) tracker',
            'useTheme':        'dark boolean + inline theme object',
        }
        if behaviors:
            lines.append('  Slop removed:')
            for b in behaviors:
                lines.append(f'    ✗  {slop_map.get(b, b)}')
            lines.append('')
            lines.append('  Replaced with:')
            for bname in beh_names:
                lines.append(f'    ✓  <{bname}> from behaviors/')
            lines.append('')

        # Props
        lines.append(f'  Props interface:  {comp_name}Props')
        lines.append(f'  CSS class:        .{css_class}')
        lines.append('')

    return '\n'.join(lines)


# ── Prompts ────────────────────────────────────────────────────────────────────

def prompt_target():
    print()
    while True:
        raw = input('  File to rewrite: ').strip().strip('"\'')
        p   = Path(raw)
        if p.is_file() and p.suffix.lower() in EXTENSIONS:
            return p
        if p.is_file():
            print(f'  ✗  Unsupported extension: {p.suffix}  (need .tsx or .jsx)')
        else:
            print(f'  ✗  Not found: {raw}')

def prompt_dirs():
    print()
    raw_lib    = input('  Path to _library/ folder:       ').strip().strip('"\'')
    raw_behs   = input('  Path to save behaviors/:        ').strip().strip('"\'')
    raw_matr   = input('  Path to save matr/ components:  ').strip().strip('"\'')
    raw_report = input('  Path to save report:            ').strip().strip('"\'')

    lib_dir    = Path(raw_lib)
    behs_dir   = Path(raw_behs);   behs_dir.mkdir(parents=True, exist_ok=True)
    matr_dir   = Path(raw_matr);   matr_dir.mkdir(parents=True, exist_ok=True)
    report_dir = Path(raw_report); report_dir.mkdir(parents=True, exist_ok=True)

    if not lib_dir.is_dir():
        print(f'\n  ✗  Library folder not found: {raw_lib}')
        sys.exit(1)

    return lib_dir, behs_dir, matr_dir, report_dir


# ── Run ────────────────────────────────────────────────────────────────────────

def run():
    print()
    print('╔══════════════════════════════════════════════════════╗')
    print('║   Mockup Rewriter                                    ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    print('  Transforms a messy AI mockup into clean')
    print('  AnatomyMatters behaviors/ + matr/ files.')

    source_path                           = prompt_target()
    lib_dir, behs_dir, matr_dir, rpt_dir = prompt_dirs()

    print(f'\n  Reading {source_path.name}...\n')

    text       = source_path.read_text(encoding='utf-8', errors='ignore')
    components = find_components_with_body(text)

    if not components:
        print('  ✗  No React components found.')
        return

    print(f'  Found {len(components)} components:\n')

    results        = []
    all_behaviors  = set()
    written_matr   = []
    written_behs   = []

    for comp_name, signature, body in components:
        behaviors  = detect_behaviors(body)
        props      = extract_prop_names(signature)
        prop_usage = detect_prop_usage(body, props)
        jsx_labels = extract_jsx_labels(body)
        rendered   = extract_rendered_props(body, prop_usage)
        css_class  = to_kebab(comp_name)
        beh_names  = [BEHAVIOR_COMPONENT[b] for b in behaviors if b in BEHAVIOR_COMPONENT]

        # Terminal summary line
        beh_str = f'  [{", ".join(beh_names)}]' if beh_names else '  [no behaviors]'
        print(f'  {comp_name:<28}{beh_str}')

        # Generate matr/ file
        content  = generate_matr(
            comp_name, behaviors, props, prop_usage, jsx_labels, rendered
        )
        out_path = matr_dir / f'{comp_name}.tsx'
        out_path.write_text(content, encoding='utf-8')
        written_matr.append(comp_name)

        # Copy behavior files from library
        for beh in behaviors:
            if beh not in all_behaviors:
                if copy_behavior(beh, lib_dir, behs_dir):
                    bname = BEHAVIOR_COMPONENT.get(beh, '')
                    if bname not in written_behs:
                        written_behs.append(bname)
                all_behaviors.add(beh)

        results.append((comp_name, behaviors, props, beh_names, css_class))

    # Report
    report      = build_report(results, source_path.name)
    report_path = rpt_dir / f'rewrite_{source_path.stem}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
    report_path.write_text(report, encoding='utf-8')

    # Summary
    print(f'\n{"─" * 54}')
    print(f'  behaviors/ ({len(written_behs)} files):')
    for b in written_behs:
        print(f'    ✓  {behs_dir / b}.tsx')
    print(f'\n  matr/ ({len(written_matr)} files):')
    for c in written_matr:
        print(f'    ✓  {matr_dir / c}.tsx')
    print(f'\n  Report → {report_path}')
    print()


if __name__ == '__main__':
    run()