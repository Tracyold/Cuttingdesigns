#!/usr/bin/env python3
"""
scan_effects.py

Scan a single file OR an entire directory of:
  .jsx .tsx .ts .html .css .scss

Extracts every motion-related CSS property and value:
  - transition (duration, timing, delay, property)
  - animation (name, duration, timing, delay, iteration, fill, direction)
  - transform (scale, translate, rotate, skew, matrix, perspective)
  - keyframes (@keyframes blocks)
  - will-change
  - scroll-behavior, scroll-snap-type, scroll-snap-align
  - overscroll-behavior
  - SCSS effs variables ($dur-*, $ease-*, $tf-*, $aname-*, etc.)
  - React inline motion props (transition, transform, animation)

Three output reports:

  REPORT 1 — Per file / per section:
    Shows every motion property grouped by selector or section header

  REPORT 2 — Per motion property across all files:
    transition-duration → 150ms → files using it

  REPORT 3 — Grouped by motion category:
    DURATIONS     — all ms/s values
    EASINGS       — all cubic-bezier, ease-*, linear values
    TRANSFORMS    — all scale, translate, rotate values
    DELAYS        — all delay values
    ANIMATIONS    — all animation-name values
    KEYFRAMES     — all @keyframes blocks found
    SCROLL        — all scroll behavior values
    WILL-CHANGE   — all will-change values

Saves to scripts/data/audits/effects/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict, OrderedDict


# ── Output path ───────────────────────────────────────────────────────

def make_output_dir():
    d = Path.cwd() / "scripts" / "data" / "audits" / "effects"
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


# ── Motion property definitions ───────────────────────────────────────

MOTION_PROPS = {
    # Transition
    "transition", "transition-property", "transition-duration",
    "transition-timing-function", "transition-delay",
    # Animation
    "animation", "animation-name", "animation-duration",
    "animation-timing-function", "animation-delay",
    "animation-iteration-count", "animation-direction",
    "animation-fill-mode", "animation-play-state",
    # Transform
    "transform", "transform-origin", "transform-style",
    "perspective", "perspective-origin",
    # Scroll motion
    "scroll-behavior", "scroll-snap-type", "scroll-snap-align",
    "scroll-padding", "scroll-padding-left", "scroll-padding-top",
    "overscroll-behavior", "overscroll-behavior-x", "overscroll-behavior-y",
    # Performance hints
    "will-change",
    # Opacity (often used in transitions)
    "opacity",
    # Motion-related
    "offset-path", "offset-distance", "offset-rotate",
}

# Categories for Report 3
def categorize_prop(prop, value):
    p = prop.lower()
    v = value.lower()

    if "duration" in p:
        return "durations"
    if "timing-function" in p or "easing" in p:
        return "easings"
    if p == "transition-delay" or p == "animation-delay":
        return "delays"
    if p == "transition" and re.search(r'\d+m?s', v):
        return "transitions-shorthand"
    if p == "transition":
        return "transitions-shorthand"
    if p.startswith("animation-name") or (p == "animation" and not re.search(r'\d', v)):
        return "animation-names"
    if p.startswith("animation"):
        return "animations-shorthand"
    if "transform" in p:
        return "transforms"
    if "will-change" in p:
        return "will-change"
    if "scroll" in p or "overscroll" in p:
        return "scroll-behavior"
    if p == "opacity":
        return "opacity"
    return "other-motion"


# ── Value pattern detectors ───────────────────────────────────────────

DURATION_RE   = re.compile(r'\b(\d+(?:\.\d+)?m?s)\b')
EASING_RE     = re.compile(
    r'(cubic-bezier\([^)]+\)|ease(?:-in)?(?:-out)?|linear|'
    r'step-start|step-end|steps\([^)]+\)|spring\([^)]*\))'
)
TRANSFORM_RE  = re.compile(
    r'((?:translate|scale|rotate|skew|matrix|perspective)'
    r'(?:X|Y|Z|3d)?\([^)]+\))'
)
KEYFRAME_RE   = re.compile(
    r'@keyframes\s+(\w+)\s*\{(.*?)\}',
    re.DOTALL
)

def extract_durations(value):
    return DURATION_RE.findall(value)

def extract_easings(value):
    return EASING_RE.findall(value)

def extract_transforms(value):
    return TRANSFORM_RE.findall(value)


# ── Helpers ───────────────────────────────────────────────────────────

def clean_value(v):
    return v.strip().rstrip(";,").strip('"').strip("'").strip()

def to_kebab(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower()

COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)


# ── CSS / SCSS parser ─────────────────────────────────────────────────

CSS_BLOCK = re.compile(r'([^{}@/][^{}]*?)\{([^{}]*?)\}', re.DOTALL)
CSS_PROP  = re.compile(r'([\w-]+)\s*:\s*([^;}{]+?)(?:;|$)', re.MULTILINE)


def parse_css(text, filename):
    groups   = OrderedDict()
    clean    = COMMENT_BLOCK.sub('', text)
    keyframes = {}

    # Extract @keyframes
    for kf in KEYFRAME_RE.finditer(text):
        name = kf.group(1)
        body = kf.group(2).strip()
        keyframes[name] = body
    if keyframes:
        groups["[@keyframes]"] = {f"@keyframes {k}": [v] for k, v in keyframes.items()}

    # SCSS effs variables — $dur-*, $ease-*, $tf-*, $aname-*, $iter-*, $fill-*, $delay-*
    scss_motion = OrderedDict()
    for line in clean.splitlines():
        m = re.match(r'^\s*(\$[\w-]+)\s*:\s*(.+?);', line.strip())
        if m:
            varname = m.group(1)
            value   = clean_value(m.group(2))
            # Capture all effs-style vars
            if any(varname.startswith(f'${p}') for p in [
                '$dur', '$ease', '$tf', '$aname', '$iter', '$fill',
                '$delay', '$spin', '$pulse', '$skeleton', '$fast',
                '$slow', '$base', '$micro', '$snappy', '$spring',
                '$bounce', '$linear', '$out', '$in-out',
            ]):
                scss_motion[varname] = value
            # Also capture any var whose value looks like motion
            elif (DURATION_RE.search(value) or EASING_RE.search(value)
                  or TRANSFORM_RE.search(value)):
                scss_motion[varname] = value
    if scss_motion:
        groups["[scss motion variables]"] = {k: [v] for k, v in scss_motion.items()}

    # CSS rule blocks — only motion properties
    for match in CSS_BLOCK.finditer(clean):
        selector = match.group(1).strip().replace('\n', ' ')
        selector = re.sub(r'\s+', ' ', selector).strip()
        if not selector or selector.startswith('@'):
            continue
        body  = match.group(2)
        props = OrderedDict()
        for p in CSS_PROP.finditer(body):
            prop  = p.group(1).strip().lower()
            value = clean_value(p.group(2))
            if prop in MOTION_PROPS and value:
                props.setdefault(prop, []).append(value)
        if props:
            groups.setdefault(selector, OrderedDict())
            for k, v in props.items():
                groups[selector].setdefault(k, []).extend(v)

    return groups


# ── React inline style parser ─────────────────────────────────────────

STYLE_OBJ  = re.compile(r'style=\{\{(.*?)\}\}', re.DOTALL)
REACT_STR  = re.compile(r'(\w+)\s*:\s*["`\']([^"`\']+)["`\']')
REACT_TMPL = re.compile(r'(\w+)\s*:\s*`([^`]+)`')
REACT_NUM  = re.compile(r'(\w+)\s*:\s*(-?\d[\d.]*(?:px|em|rem|vh|vw|%|ms|s)?)\b')


def parse_react(text, filename):
    groups = OrderedDict()
    for i, match in enumerate(STYLE_OBJ.finditer(text)):
        body  = match.group(1)
        label = f"[inline style #{i+1}]"
        props = OrderedDict()
        for m in REACT_STR.finditer(body):
            prop  = to_kebab(m.group(1))
            value = clean_value(m.group(2))
            if prop in MOTION_PROPS and value:
                props.setdefault(prop, []).append(value)
        for m in REACT_TMPL.finditer(body):
            prop  = to_kebab(m.group(1))
            value = clean_value(m.group(2))
            if prop in MOTION_PROPS and value:
                props.setdefault(prop, []).append(value)
        for m in REACT_NUM.finditer(body):
            prop  = to_kebab(m.group(1))
            value = m.group(2)
            if prop in MOTION_PROPS and value:
                props.setdefault(prop, []).append(value)
        if props:
            groups[label] = props
    return groups


# ── Section header extraction ─────────────────────────────────────────

HEADER_RE = re.compile(
    r'(?:^|\n)\s*(?:/\*+\s*(.*?)\s*\*+/|//\s*[─═\-]+\s*(.+?)\s*[─═\-]*\s*$)',
    re.MULTILINE
)

def extract_sections(text):
    sections  = []
    headers   = list(HEADER_RE.finditer(text))
    prev_name = "[top level]"
    prev_end  = 0
    for h in headers:
        name = (h.group(1) or h.group(2) or "").strip()
        name = re.sub(r'[─═\-]+', '', name).strip()
        if not name: continue
        chunk = text[prev_end:h.start()]
        if chunk.strip(): sections.append((prev_name, chunk))
        prev_name = name
        prev_end  = h.end()
    chunk = text[prev_end:]
    if chunk.strip(): sections.append((prev_name, chunk))
    return sections if sections else [("[all]", text)]


# ── File parsers ──────────────────────────────────────────────────────

def parse_chunk(chunk, ext, filename):
    groups = OrderedDict()
    if ext in (".css", ".scss"):
        groups.update(parse_css(chunk, filename))
    elif ext in (".jsx", ".tsx"):
        groups.update(parse_css(chunk, filename))
        groups.update(parse_react(chunk, filename))
    elif ext in (".ts",):
        groups.update(parse_css(chunk, filename))
    elif ext == ".html":
        groups.update(parse_css(chunk, filename))
    return groups

def parse_file_by_sections(path):
    try: text = path.read_text(encoding="utf-8", errors="ignore")
    except: return {}
    ext = path.suffix.lower()
    result = OrderedDict()
    for section_name, chunk in extract_sections(text):
        groups = parse_chunk(chunk, ext, path.name)
        if groups: result[section_name] = groups
    return result

def parse_file(path):
    try: text = path.read_text(encoding="utf-8", errors="ignore")
    except: return {}
    return parse_chunk(text, path.suffix.lower(), path.name)


# ── Category collector ────────────────────────────────────────────────

def collect_categories(flat_data):
    """
    Returns dict: category → [(value, prop, filename)]
    """
    cats = defaultdict(list)

    for filename, groups in flat_data.items():
        for selector, props in groups.items():
            for prop, values in props.items():
                for value in values:
                    cat = categorize_prop(prop, value)
                    cats[cat].append((value, prop, filename))

                    # Also extract sub-values from shorthand transition/animation
                    for dur in extract_durations(value):
                        cats["durations"].append((dur, f"{prop} [extracted]", filename))
                    for ease in extract_easings(value):
                        cats["easings"].append((ease, f"{prop} [extracted]", filename))
                    for tf in extract_transforms(value):
                        cats["transforms"].append((tf, f"{prop} [extracted]", filename))

    return cats


# ── Report builders ───────────────────────────────────────────────────

def build_report1_directory(all_data):
    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  REPORT 1 — PER FILE → selector → motion properties             ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")
    for filename, groups in sorted(all_data.items()):
        if not groups: continue
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


def build_report1_single(filename, section_data):
    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append(f"║  REPORT 1 — {filename:<53}║")
    lines.append("║  Grouped by section comment header — motion properties only      ║")
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


def build_report2(flat_data):
    prop_map = defaultdict(lambda: defaultdict(set))
    for filename, groups in flat_data.items():
        for selector, props in groups.items():
            for prop, values in props.items():
                for v in values:
                    prop_map[prop][v].add(filename)

    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  REPORT 2 — PER MOTION PROPERTY: values across all files        ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")
    for prop in sorted(prop_map.keys()):
        values = prop_map[prop]
        lines.append(f"\n{'─'*70}")
        lines.append(f"  {prop}  ({len(values)} unique value{'s' if len(values)!=1 else ''})")
        lines.append('─'*70)
        for value, files in sorted(values.items()):
            lines.append(f"  {value:<50} ← {', '.join(sorted(files))}")
    return "\n".join(lines)


def build_report3(flat_data):
    cats = collect_categories(flat_data)

    CAT_ORDER = [
        "durations", "easings", "delays",
        "transforms", "transitions-shorthand", "animations-shorthand",
        "animation-names", "keyframes", "will-change",
        "scroll-behavior", "opacity", "other-motion",
    ]

    CAT_LABELS = {
        "durations":              "DURATIONS",
        "easings":                "EASINGS",
        "delays":                 "DELAYS",
        "transforms":             "TRANSFORMS",
        "transitions-shorthand":  "TRANSITIONS (shorthand)",
        "animations-shorthand":   "ANIMATIONS (shorthand)",
        "animation-names":        "ANIMATION NAMES",
        "keyframes":              "KEYFRAMES",
        "will-change":            "WILL-CHANGE",
        "scroll-behavior":        "SCROLL BEHAVIOR",
        "opacity":                "OPACITY",
        "other-motion":           "OTHER MOTION",
    }

    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║  REPORT 3 — ALL EFFECTS grouped by category                     ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")

    all_cats = CAT_ORDER + [c for c in cats if c not in CAT_ORDER]

    for cat in all_cats:
        entries = cats.get(cat)
        if not entries: continue

        value_map = defaultdict(lambda: {"props": set(), "files": set()})
        for value, prop, filename in entries:
            value_map[value]["props"].add(prop)
            value_map[value]["files"].add(filename)

        label = CAT_LABELS.get(cat, cat.upper())
        lines.append(f"\n{'═'*70}")
        lines.append(f"  {label}  ({len(value_map)} unique values)")
        lines.append('═'*70)
        for value, meta in sorted(value_map.items()):
            props_str = ", ".join(sorted(meta["props"]))
            files_str = ", ".join(sorted(meta["files"]))
            lines.append(f"\n  {value}")
            lines.append(f"    props : {props_str}")
            lines.append(f"    files : {files_str}")

    return "\n".join(lines)


# ── Run ───────────────────────────────────────────────────────────────

def run():
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║         frontend effects scanner                     ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Scans .jsx .tsx .ts .html .css .scss")
    print("  Captures: transitions, animations, transforms,")
    print("            keyframes, easings, durations, delays,")
    print("            scroll motion, will-change, opacity")
    print()
    print("  Outputs 3 reports:")
    print("    1 — per file / per section")
    print("    2 — per motion property across all files")
    print("    3 — all effects grouped by category")

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
                flat[path.name][f"{section} / {selector}"] = props

        r1 = build_report1_single(path.name, section_data)
        r2 = build_report2(flat)
        r3 = build_report3(flat)

        stem    = path.stem
        r1_path = output_dir / f"single_{stem}_per-section_{timestamp}.txt"
        r2_path = output_dir / f"single_{stem}_per-property_{timestamp}.txt"
        r3_path = output_dir / f"single_{stem}_effects_{timestamp}.txt"

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
        r3 = build_report3(all_data)

        r1_path = output_dir / f"dir_per-file_{timestamp}.txt"
        r2_path = output_dir / f"dir_per-property_{timestamp}.txt"
        r3_path = output_dir / f"dir_effects_{timestamp}.txt"

    r1_path.write_text(r1, encoding="utf-8")
    r2_path.write_text(r2, encoding="utf-8")
    r3_path.write_text(r3, encoding="utf-8")

    print(f"\n  ✓ Report 1 (per file/section)  → {r1_path}")
    print(f"  ✓ Report 2 (per property)      → {r2_path}")
    print(f"  ✓ Report 3 (effects by category) → {r3_path}\n")


if __name__ == "__main__":
    run()