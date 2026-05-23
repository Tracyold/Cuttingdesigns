#!/usr/bin/env python3
"""
find_design_components.py
─────────────────────────
Scans a source file (.tsx / .jsx) and finds design-only blocks that could
be extracted into reusable design components -- things like styled wrappers,
repeated visual patterns, layout shells, typography combos, badge/pill/chip
patterns, avatar/icon combos, dividers, and card skins.

Does NOT modify any files. Read-only analysis only.

For each candidate:
  - Terminal output: line range, component kind, suggested name, all class names found
  - .txt output: the full extracted code block ready to use as a starting point

Usage:
  python scripts/audits/find_design_components.py [--root PATH] [--file PATH] [--out DIR]

  --root   Repo root (default: current directory)
  --file   File to scan (prompts if not provided)
  --out    Directory to write .txt extracts (default: repo root)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime


# ── Output helpers ────────────────────────────────────────────────────────────

def out(msg: str = "") -> None:
    print(msg, flush=True)

def section(title: str) -> None:
    print(f"\n{'─'*66}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'─'*66}", flush=True)

def header(title: str, extra: str = "") -> None:
    print(f"\n{'═'*66}", flush=True)
    print(f"  {title}", flush=True)
    if extra: print(f"  {extra}", flush=True)
    print(f"{'═'*66}", flush=True)

def rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return Path(path).name


# ── Design component kinds ────────────────────────────────────────────────────

KIND = {
    "styled_wrapper":  "StyledWrapper     -- repeated layout shell with consistent className pattern",
    "card_skin":       "CardSkin          -- visual card with border/shadow/radius/padding pattern",
    "badge_pill":      "Badge/Pill        -- small inline status/label chip with color + rounded classes",
    "typography":      "Typography        -- repeated heading/body text combo with font/size/color classes",
    "avatar_icon":     "AvatarIcon        -- image or icon in a shaped container (rounded, sized)",
    "divider":         "Divider           -- horizontal/vertical rule with margin/color pattern",
    "button_skin":     "ButtonSkin        -- styled button with repeated className cluster",
    "input_skin":      "InputSkin         -- styled input/textarea with border/focus/ring classes",
    "layout_shell":    "LayoutShell       -- flex/grid container with gap/padding/alignment pattern",
    "overlay_skin":    "OverlaySkin       -- semi-transparent backdrop or overlay layer",
    "icon_label":      "IconLabel         -- icon + text side by side with consistent spacing",
    "stat_block":      "StatBlock         -- number/label/sublabel display (metric/stat tile)",
    "empty_design":    "EmptyDesign       -- empty state illustration + message visual pattern",
    "tag_cluster":     "TagCluster        -- group of small tag/chip elements",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_file(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None


def extract_classnames(block: list[str]) -> list[str]:
    """
    Pull every className value from a block of lines.
    Handles: className="...", className={`...`}, className={'...'}, cn(...), clsx(...)
    Returns a deduplicated flat list of individual class tokens.
    """
    classes = []

    for line in block:
        # className="foo bar baz"
        for m in re.finditer(r'className=["\']([^"\']+)["\']', line):
            classes += m.group(1).split()

        # className={`foo bar ${...} baz`}  -- extract literal parts
        for m in re.finditer(r'className=\{`([^`]+)`\}', line):
            literal = re.sub(r'\$\{[^}]+\}', '', m.group(1))
            classes += literal.split()

        # cn("foo", "bar", condition && "baz")  or  clsx(...)
        for m in re.finditer(r'(?:cn|clsx|classNames?)\(([^)]+)\)', line):
            for token in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
                classes += token.group(1).split()

        # style={{ }} -- CSS-in-JS keys (not class names but useful context)
        # skip those -- only real class tokens

    # Deduplicate, preserve order, filter out template junk
    seen = set()
    result = []
    for c in classes:
        c = c.strip().strip('{}')
        if c and c not in seen and not c.startswith('$') and len(c) > 1:
            seen.add(c)
            result.append(c)
    return result


def find_block_end(lines: list[str], start: int, max_lines: int = 80) -> int:
    """
    Given a start line index, find the closing line of the JSX block.
    Returns end index (exclusive).
    """
    depth  = 0
    opened = False
    j      = start
    limit  = min(start + max_lines, len(lines))
    while j < limit:
        l = lines[j]
        # Count JSX tag opens/closes roughly
        opens  = len(re.findall(r'<\w', l)) + l.count('(') + l.count('{')
        closes = len(re.findall(r'</\w|/>',  l)) + l.count(')') + l.count('}')
        depth += opens - closes
        if j > start and depth <= 0:
            return j + 1
        opened = True
        j += 1
    return j


def strip_comments(lines: list[str]) -> list[str]:
    result = []
    in_block = False
    for line in lines:
        if '/*' in line:
            in_block = True
        if in_block:
            if '*/' in line:
                in_block = False
            result.append('')
            continue
        result.append(re.sub(r'//.*$', '', line))
    return result


# ── Candidate ─────────────────────────────────────────────────────────────────

class Candidate:
    def __init__(self, start: int, end: int, kind: str, name: str,
                 reason: str, classes: list[str], code: list[str]):
        self.start   = start    # 1-indexed
        self.end     = end
        self.kind    = kind
        self.name    = name
        self.reason  = reason
        self.classes = classes  # all class tokens found
        self.code    = code     # raw lines of the block

    def to_dict(self) -> dict:
        return {
            "lines":   f"{self.start}–{self.end}",
            "start":   self.start,
            "end":     self.end,
            "kind":    self.kind,
            "name":    self.name,
            "reason":  self.reason,
            "classes": self.classes,
        }


# ── Detectors ─────────────────────────────────────────────────────────────────

def detect_card_skins(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Divs with border + shadow + rounded + padding -- card patterns."""
    candidates = []
    card_re = re.compile(
        r'(?:border|shadow|rounded|ring).*(?:border|shadow|rounded|ring|p-\d|px-|py-)',
        re.IGNORECASE
    )
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<div|<article|<section|<li', line) and card_re.search(line):
            end   = find_block_end(clean, i)
            block = lines[i:end]
            classes = extract_classnames(block)
            has_card_classes = any(
                any(c.startswith(p) for p in ['border', 'shadow', 'rounded', 'ring', 'p-', 'px-', 'py-'])
                for c in classes
            )
            if has_card_classes and len(block) >= 3:
                name    = "CardSkin"
                snippet = line.strip()[:80]
                candidates.append(Candidate(
                    start   = i + 1,
                    end     = end,
                    kind    = "card_skin",
                    name    = name,
                    reason  = f"border/shadow/rounded pattern -- visual card skin spanning {len(block)} lines",
                    classes = classes,
                    code    = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_badge_pills(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Small inline elements with rounded-full + color + text-xs -- badge/pill."""
    candidates = []
    badge_re = re.compile(r'rounded-full|rounded-lg.*text-xs|text-xs.*rounded|pill|badge|chip', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<span|<div|<p', line) and badge_re.search(line):
            end   = find_block_end(clean, i, max_lines=15)
            block = lines[i:end]
            classes = extract_classnames(block)
            if any('rounded' in c for c in classes) and len(block) <= 8:
                candidates.append(Candidate(
                    start   = i + 1,
                    end     = end,
                    kind    = "badge_pill",
                    name    = "Badge",
                    reason  = f"rounded + color inline label -- extract as <Badge label='...' color='...' />",
                    classes = classes,
                    code    = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_typography(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Repeated heading + subtext combos with font/size/color classes."""
    candidates = []
    # Look for heading tags with styling followed immediately by a subtext
    heading_re = re.compile(r'<h[1-6]|font-bold|font-semibold|text-xl|text-2xl|text-3xl', re.IGNORECASE)
    sub_re     = re.compile(r'text-sm|text-xs|text-muted|opacity-|font-normal', re.IGNORECASE)
    i = 0
    while i < len(clean) - 3:
        line = clean[i]
        if heading_re.search(line):
            # Look ahead 5 lines for a subtext
            lookahead = clean[i:min(i+6, len(clean))]
            if any(sub_re.search(l) for l in lookahead[1:]):
                end   = i + len(lookahead)
                block = lines[i:end]
                classes = extract_classnames(block)
                if len(classes) >= 3:
                    candidates.append(Candidate(
                        start   = i + 1,
                        end     = end,
                        kind    = "typography",
                        name    = "TextBlock",
                        reason  = f"heading + subtext combo with {len(classes)} design classes -- extract as <TextBlock title='...' sub='...' />",
                        classes = classes,
                        code    = block,
                    ))
                    i = end
                    continue
        i += 1
    return candidates


def detect_avatar_icons(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Image or icon in a shaped/sized container."""
    candidates = []
    avatar_re = re.compile(r'rounded-full.*w-\d|w-\d.*rounded-full|<img.*rounded|avatar|profile.*img', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if avatar_re.search(line):
            end   = find_block_end(clean, i, max_lines=12)
            block = lines[i:end]
            classes = extract_classnames(block)
            if any('rounded' in c for c in classes) and any(
                re.match(r'w-\d|h-\d', c) for c in classes
            ):
                candidates.append(Candidate(
                    start   = i + 1,
                    end     = end,
                    kind    = "avatar_icon",
                    name    = "Avatar",
                    reason  = f"rounded + sized image container -- extract as <Avatar src='...' size='...' />",
                    classes = classes,
                    code    = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_layout_shells(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Flex/grid containers with consistent gap/padding/alignment -- repeated layout shells."""
    candidates = []
    layout_re  = re.compile(r'flex.*gap-|grid.*gap-|flex.*justify-|flex.*items-|grid-cols', re.IGNORECASE)

    # Fingerprint approach -- find structurally identical layout wrappers
    fingerprints: dict[str, list[int]] = defaultdict(list)
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<div|<section|<main|<ul|<ol', line) and layout_re.search(line):
            classes = extract_classnames([line])
            if len(classes) >= 3:
                # Fingerprint: sort classes to catch reordered duplicates
                fp = tuple(sorted(c for c in classes if any(
                    c.startswith(p) for p in
                    ['flex', 'grid', 'gap', 'justify', 'items', 'p-', 'px-', 'py-', 'space-']
                )))
                if len(fp) >= 2:
                    fingerprints[fp].append(i)
        i += 1

    reported = set()
    for fp, positions in fingerprints.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                end   = find_block_end(clean, pos)
                block = lines[pos:end]
                classes = extract_classnames(block)
                candidates.append(Candidate(
                    start   = pos + 1,
                    end     = end,
                    kind    = "layout_shell",
                    name    = "LayoutShell",
                    reason  = f"flex/grid layout pattern appears {len(positions)}x -- extract as a shared layout wrapper",
                    classes = classes,
                    code    = block,
                ))

    return candidates


def detect_button_skins(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Styled buttons with repeated className clusters."""
    candidates = []
    btn_re     = re.compile(r'<button|<Button', re.IGNORECASE)
    style_re   = re.compile(r'px-|py-|rounded|bg-|text-|border|hover:', re.IGNORECASE)

    fingerprints: dict[tuple, list[int]] = defaultdict(list)
    i = 0
    while i < len(clean):
        line = clean[i]
        if btn_re.search(line) and style_re.search(line):
            classes = extract_classnames([line])
            design_classes = tuple(sorted(c for c in classes if any(
                c.startswith(p) for p in
                ['px-', 'py-', 'rounded', 'bg-', 'text-', 'border', 'hover:', 'focus:', 'font-']
            )))
            if len(design_classes) >= 3:
                fingerprints[design_classes].append(i)
        i += 1

    reported = set()
    for fp, positions in fingerprints.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                end   = find_block_end(clean, pos, max_lines=10)
                block = lines[pos:end]
                classes = extract_classnames(block)
                candidates.append(Candidate(
                    start   = pos + 1,
                    end     = end,
                    kind    = "button_skin",
                    name    = "ButtonSkin",
                    reason  = f"identical button className cluster appears {len(positions)}x -- extract as <Button variant='...' />",
                    classes = classes,
                    code    = block,
                ))

    return candidates


def detect_input_skins(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Styled inputs with border/ring/focus classes."""
    candidates = []
    input_re   = re.compile(r'<input|<textarea|<select|<Input|<Textarea|<Select', re.IGNORECASE)
    style_re   = re.compile(r'border|ring|focus:|rounded|px-|bg-', re.IGNORECASE)

    fingerprints: dict[tuple, list[int]] = defaultdict(list)
    i = 0
    while i < len(clean):
        line = clean[i]
        if input_re.search(line) and style_re.search(line):
            classes = extract_classnames([line])
            design_classes = tuple(sorted(c for c in classes if any(
                c.startswith(p) for p in
                ['border', 'ring', 'focus:', 'rounded', 'px-', 'py-', 'bg-', 'text-', 'outline']
            )))
            if len(design_classes) >= 2:
                fingerprints[design_classes].append(i)
        i += 1

    reported = set()
    for fp, positions in fingerprints.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                end   = find_block_end(clean, pos, max_lines=10)
                block = lines[pos:end]
                classes = extract_classnames(block)
                candidates.append(Candidate(
                    start   = pos + 1,
                    end     = end,
                    kind    = "input_skin",
                    name    = "InputSkin",
                    reason  = f"identical input className cluster appears {len(positions)}x -- extract as <Input variant='...' />",
                    classes = classes,
                    code    = block,
                ))

    return candidates


def detect_icon_labels(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Icon + text side by side."""
    candidates = []
    icon_re    = re.compile(r'<svg|<Icon|<.*Icon\s|lucide|heroicon|FaIcon|MdIcon', re.IGNORECASE)
    i = 0
    while i < len(clean) - 2:
        line = clean[i]
        if icon_re.search(line):
            lookahead = clean[i:min(i+5, len(clean))]
            has_text  = any(re.search(r'<span|<p|<h\d|className.*text-', l) for l in lookahead[1:])
            if has_text:
                end   = i + len(lookahead)
                block = lines[i:end]
                classes = extract_classnames(block)
                candidates.append(Candidate(
                    start   = i + 1,
                    end     = end,
                    kind    = "icon_label",
                    name    = "IconLabel",
                    reason  = f"icon + text combo -- extract as <IconLabel icon={{...}} label='...' />",
                    classes = classes,
                    code    = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_stat_blocks(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Number/metric + label display blocks."""
    candidates = []
    stat_re    = re.compile(r'text-\d+xl|font-bold.*\d|stat|metric|count|total|amount', re.IGNORECASE)
    label_re   = re.compile(r'text-sm|text-xs|text-muted|opacity-|uppercase|tracking-', re.IGNORECASE)
    i = 0
    while i < len(clean) - 2:
        line = clean[i]
        if stat_re.search(line):
            lookahead = clean[i:min(i+6, len(clean))]
            has_label = any(label_re.search(l) for l in lookahead[1:])
            if has_label:
                end   = i + len(lookahead)
                block = lines[i:end]
                classes = extract_classnames(block)
                if len(classes) >= 2:
                    candidates.append(Candidate(
                        start   = i + 1,
                        end     = end,
                        kind    = "stat_block",
                        name    = "StatBlock",
                        reason  = f"large number + label display -- extract as <StatBlock value='...' label='...' />",
                        classes = classes,
                        code    = block,
                    ))
                    i = end
                    continue
        i += 1
    return candidates


def detect_overlay_skins(lines: list[str], clean: list[str]) -> list[Candidate]:
    """Semi-transparent backdrops and overlay layers."""
    candidates = []
    overlay_re = re.compile(r'bg-black.*opacity|bg-opacity|backdrop|inset-0.*bg-|fixed.*inset', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if overlay_re.search(line):
            end   = find_block_end(clean, i, max_lines=20)
            block = lines[i:end]
            classes = extract_classnames(block)
            candidates.append(Candidate(
                start   = i + 1,
                end     = end,
                kind    = "overlay_skin",
                name    = "Overlay",
                reason  = f"semi-transparent overlay/backdrop pattern -- extract as <Overlay onClick={{onClose}} />",
                classes = classes,
                code    = block,
            ))
            i = end
            continue
        i += 1
    return candidates


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda c: c.start)
    result     = [candidates[0]]
    for c in candidates[1:]:
        last    = result[-1]
        overlap = max(0, min(c.end, last.end) - max(c.start, last.start))
        if overlap < 3:
            result.append(c)
        else:
            if (c.end - c.start) > (last.end - last.start):
                result[-1] = c
    return result


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(candidates: list[Candidate], file_path: Path, root: Path) -> None:
    file_rel = rel(str(file_path), root)

    if not candidates:
        out()
        out(f"  No reusable design component candidates found in {file_rel}.")
        return

    section(f"REUSABLE DESIGN COMPONENT CANDIDATES -- {len(candidates)} found in {file_rel}")
    out()
    out("  These are design-only blocks that COULD be extracted into reusable")
    out("  design components. No changes have been made.")
    out()

    for i, c in enumerate(candidates, 1):
        span = c.end - c.start + 1
        out(f"  ┌─ [{i}] <{c.name} />")
        out(f"  │   Lines:    {c.start}–{c.end}  ({span} lines)")
        out(f"  │   Kind:     {KIND.get(c.kind, c.kind)}")
        out(f"  │   Reason:   {c.reason}")
        if c.classes:
            # Wrap class list at 60 chars
            class_str = "  ".join(c.classes)
            out(f"  │   Classes:  {class_str}")
        out(f"  └{'─'*62}")
        out()

    section("KIND LEGEND")
    out()
    for k, v in KIND.items():
        out(f"  {v}")
    out()


# ── TXT extract writer ────────────────────────────────────────────────────────

def write_extracts(candidates: list[Candidate], file_path: Path,
                   out_dir: Path, root: Path) -> Path:
    """
    Write all candidate code extracts into a single .txt file.
    Each extract is separated with a clear header.
    """
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"design-extracts-{file_path.stem}-{ts}.txt"

    lines_out = []
    lines_out.append(f"DESIGN COMPONENT EXTRACTS")
    lines_out.append(f"Source: {rel(str(file_path), root)}")
    lines_out.append(f"Generated: {datetime.now()}")
    lines_out.append(f"Candidates: {len(candidates)}")
    lines_out.append("=" * 66)
    lines_out.append("")

    for i, c in enumerate(candidates, 1):
        lines_out.append(f"[{i}] <{c.name} />")
        lines_out.append(f"    Lines:   {c.start}–{c.end}")
        lines_out.append(f"    Kind:    {KIND.get(c.kind, c.kind)}")
        lines_out.append(f"    Reason:  {c.reason}")
        lines_out.append(f"    Classes: {' '.join(c.classes)}")
        lines_out.append("")
        lines_out.append("    ── Extracted Code ──────────────────────────────────────")
        lines_out.append("")
        for line in c.code:
            lines_out.append("    " + line)
        lines_out.append("")
        lines_out.append("    ── Suggested Component Shell ───────────────────────────")
        lines_out.append("")
        lines_out.append(f"    // {out_path.parent}/components/design/{c.name}.tsx")
        lines_out.append(f"    type {c.name}Props = {{")
        lines_out.append(f"      children?: React.ReactNode")
        lines_out.append(f"      className?: string")
        lines_out.append(f"    }}")
        lines_out.append("")
        lines_out.append(f"    export function {c.name}({{ children, className }}: {c.name}Props) {{")
        lines_out.append(f"      return (")
        lines_out.append(f"        // paste extracted code here, replace hardcoded values with props")
        lines_out.append(f"      )")
        lines_out.append(f"    }}")
        lines_out.append("")
        lines_out.append("─" * 66)
        lines_out.append("")

    out_path.write_text("\n".join(lines_out), encoding="utf-8")
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find design blocks that could be extracted into reusable design components."
    )
    parser.add_argument("--root", default=".", help="Repo root (default: current directory)")
    parser.add_argument("--file", default=None,  help="File to scan (prompts if not provided)")
    parser.add_argument("--out",  default=None,  help="Directory to write .txt extracts (default: repo root)")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    # ── Locate file ───────────────────────────────────────────────────────────

    if args.file:
        target = Path(args.file).resolve()
    else:
        print(f"\n  Enter file to scan (relative to {root}):")
        print(f"  > ", end="", flush=True)
        raw    = input().strip()
        target = (root / raw).resolve()
        if not target.exists():
            matches = list(root.rglob(raw))
            matches = [m for m in matches
                       if "node_modules" not in str(m) and ".next" not in str(m)]
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                print(f"\n  Multiple matches:")
                for i, m in enumerate(matches):
                    print(f"  [{i}] {m.relative_to(root)}")
                print(f"  Choose [0-{len(matches)-1}]: ", end="", flush=True)
                idx    = int(input().strip())
                target = matches[idx]

    if not target.exists():
        print(f"\n  [ERROR] File not found: {target}")
        sys.exit(1)

    out_dir = Path(args.out).resolve() if args.out else root

    header(
        f"DESIGN COMPONENT AUDIT: {target.relative_to(root)}",
        f"Root: {root}"
    )
    out()

    lines = read_file(target)
    if not lines:
        print(f"\n  [ERROR] Could not read file: {target}")
        sys.exit(1)

    out(f"  File:   {rel(str(target), root)}")
    out(f"  Lines:  {len(lines)}")
    out()
    out("  Running design pattern detectors...")
    out()

    clean = strip_comments(lines)

    # Run all detectors
    candidates: list[Candidate] = []
    candidates += detect_card_skins(lines, clean)
    candidates += detect_badge_pills(lines, clean)
    candidates += detect_typography(lines, clean)
    candidates += detect_avatar_icons(lines, clean)
    candidates += detect_layout_shells(lines, clean)
    candidates += detect_button_skins(lines, clean)
    candidates += detect_input_skins(lines, clean)
    candidates += detect_icon_labels(lines, clean)
    candidates += detect_stat_blocks(lines, clean)
    candidates += detect_overlay_skins(lines, clean)

    candidates = deduplicate(candidates)
    candidates = sorted(candidates, key=lambda c: c.start)

    print_report(candidates, target, root)

    if candidates:
        txt_path = write_extracts(candidates, target, out_dir, root)
        out(f"  Code extracts saved → {txt_path}")
        out()

    out(f"  Run: python scripts/audits/find_design_components.py --root {root} --file {target.relative_to(root)}")
    out()


if __name__ == "__main__":
    main()