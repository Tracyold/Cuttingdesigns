#!/usr/bin/env python3
"""
find_components.py
──────────────────
Scans a source file (.tsx / .jsx) and finds blocks of code that could be
extracted into their own reusable component files.

Does NOT modify any files. Read-only analysis only.

Detects:
  - Repeated JSX blocks (same structure appearing 2+ times)
  - Large inline JSX subtrees (deep nesting that could be its own component)
  - Inline map() renders (list items that should be a ListItem component)
  - Repeated className patterns (styled wrappers that could be a component)
  - Conditional render blocks (if/ternary blocks large enough to extract)
  - Inline form fields (input/textarea/select groups that repeat)
  - Modal / drawer patterns (fixed overlays that could be a Shell component)
  - Icon + label combos (repeated icon+text patterns)

Output:
  - File, line range, candidate component name, component kind, reason

Usage:
  python scripts/audits/find_components.py [--root PATH] [--file PATH] [--json PATH]

  --root   Repo root (default: current directory)
  --file   File to scan (prompts if not provided)
  --json   Write results to this JSON path
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


# ── Component kind labels ─────────────────────────────────────────────────────

KIND = {
    "list_item":    "ListItem component       -- one item in a repeated list render",
    "map_render":   "List component           -- the whole map() render block",
    "form_field":   "FormField component      -- reusable labeled input/select/textarea",
    "modal_shell":  "Modal/Drawer shell       -- overlay + backdrop + close button",
    "card":         "Card component           -- repeated card/tile structure",
    "conditional":  "ConditionalBlock         -- large if/ternary render worth extracting",
    "icon_label":   "IconLabel component      -- repeated icon + text combo",
    "styled_wrap":  "StyledWrapper component  -- repeated className pattern / layout shell",
    "section":      "Section component        -- large self-contained JSX subtree",
    "button_group": "ButtonGroup component    -- repeated cluster of related buttons",
    "empty_state":  "EmptyState component     -- empty/loading/error fallback UI",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_file(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None


def tag_depth(line: str) -> int:
    """Rough JSX nesting depth estimate from leading whitespace."""
    return (len(line) - len(line.lstrip())) // 2


def strip_comments(lines: list[str]) -> list[str]:
    """Remove single-line // comments and blank lines for analysis."""
    out = []
    in_block = False
    for line in lines:
        if "/*" in line:
            in_block = True
        if in_block:
            if "*/" in line:
                in_block = False
            out.append("")
            continue
        out.append(re.sub(r'//.*$', '', line))
    return out


# ── Candidate dataclass ───────────────────────────────────────────────────────

class Candidate:
    def __init__(self, start: int, end: int, kind: str, name: str, reason: str, snippet: str = ""):
        self.start   = start    # 1-indexed line number
        self.end     = end
        self.kind    = kind
        self.name    = name
        self.reason  = reason
        self.snippet = snippet  # short excerpt

    def to_dict(self) -> dict:
        return {
            "lines":   f"{self.start}–{self.end}",
            "start":   self.start,
            "end":     self.end,
            "kind":    self.kind,
            "name":    self.name,
            "reason":  self.reason,
            "snippet": self.snippet,
        }


# ── Detectors ─────────────────────────────────────────────────────────────────

def detect_map_renders(lines: list[str]) -> list[Candidate]:
    """
    Find .map( calls that render JSX.
    The map body is a candidate list item component.
    """
    candidates = []
    clean = strip_comments(lines)

    i = 0
    while i < len(clean):
        line = clean[i]
        # Match things like: items.map((item) => ( or items.map(item => (
        m = re.search(r'(\w+)\.map\(\s*[\(\[]?(\w+)', line)
        if m:
            collection = m.group(1)
            item_var   = m.group(2)
            start_line = i + 1  # 1-indexed

            # Find the JSX block inside the map
            depth  = 0
            opened = False
            j      = i
            while j < len(clean):
                for ch in clean[j]:
                    if ch in "({[":
                        depth += 1
                        opened = True
                    elif ch in ")}]":
                        depth -= 1
                if opened and depth <= 0:
                    break
                j += 1

            end_line  = j + 1
            span      = end_line - start_line

            if span >= 4:
                # Suggest name from collection name
                singular = collection.rstrip('s').rstrip('e') if collection.endswith('s') else collection
                singular = singular[0].upper() + singular[1:] if singular else "Item"
                name     = f"{singular}Item"

                snippet = lines[i].strip()[:80]
                candidates.append(Candidate(
                    start   = start_line,
                    end     = end_line,
                    kind    = "list_item",
                    name    = name,
                    reason  = f"'{collection}.map({item_var} => ...)' renders {span} lines -- extract the item body as <{name} />",
                    snippet = snippet,
                ))
        i += 1
    return candidates


def detect_repeated_blocks(lines: list[str]) -> list[Candidate]:
    """
    Find JSX blocks of 5+ lines that appear 2+ times (structurally similar).
    Uses normalized line fingerprints.
    """
    candidates = []
    clean      = strip_comments(lines)

    # Build fingerprints: strip variable names/values, keep structure
    def fingerprint(line: str) -> str:
        l = re.sub(r'"[^"]*"', '"_"', line)
        l = re.sub(r"'[^']*'", "'_'", l)
        l = re.sub(r'\{[^}]{0,30}\}', '{_}', l)
        l = re.sub(r'\b\w+Id\b|\bid\b', '_id', l)
        return l.strip()

    fps = [fingerprint(l) for l in clean]

    # Sliding window -- look for repeated runs of 5+ lines
    window = 6
    seen: dict[tuple, list[int]] = defaultdict(list)

    for i in range(len(fps) - window):
        key = tuple(fps[i:i+window])
        if any(k.strip() for k in key):  # skip all-blank windows
            seen[key].append(i)

    reported: set[int] = set()
    for key, positions in seen.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                # Find full block extent
                start = pos + 1
                end   = pos + window

                # Try to infer a name from the opening tag
                opening = lines[pos].strip()
                tag_m   = re.search(r'<(\w+)', opening)
                name    = (tag_m.group(1) + "Block") if tag_m else "RepeatedBlock"
                name    = name[0].upper() + name[1:]

                candidates.append(Candidate(
                    start   = start,
                    end     = end,
                    kind    = "card",
                    name    = name,
                    reason  = f"Similar {window}-line block appears {len(positions)}x -- extract as <{name} /> with props",
                    snippet = opening[:80],
                ))

    return candidates


def detect_large_conditionals(lines: list[str]) -> list[Candidate]:
    """
    Find ternary or && conditional renders that span 8+ lines.
    """
    candidates = []
    clean      = strip_comments(lines)

    i = 0
    while i < len(clean):
        line = clean[i]
        # Ternary: condition ? ( or && (
        if re.search(r'\?\s*\(|&&\s*\(|\?\s*<', line):
            start_line = i + 1
            depth  = 0
            opened = False
            j      = i
            while j < len(clean):
                for ch in clean[j]:
                    if ch in "({":
                        depth += 1
                        opened = True
                    elif ch in ")}":
                        depth -= 1
                if opened and depth <= 0:
                    break
                j += 1
            end_line = j + 1
            span     = end_line - start_line

            if span >= 8:
                snippet = line.strip()[:80]
                # Try to name from surrounding context
                cond_m = re.search(r'(\w+)\s*[?&]', line)
                name   = (cond_m.group(1)[0].upper() + cond_m.group(1)[1:] + "View") if cond_m else "ConditionalView"
                candidates.append(Candidate(
                    start   = start_line,
                    end     = end_line,
                    kind    = "conditional",
                    name    = name,
                    reason  = f"Conditional render spans {span} lines -- extract each branch as its own component",
                    snippet = snippet,
                ))
        i += 1
    return candidates


def detect_form_fields(lines: list[str]) -> list[Candidate]:
    """
    Find label + input/select/textarea groups that repeat.
    """
    candidates = []
    clean      = strip_comments(lines)

    field_pattern = re.compile(r'<(input|textarea|select|Input|Select|Textarea)', re.IGNORECASE)
    label_pattern = re.compile(r'<(label|Label)', re.IGNORECASE)

    i = 0
    while i < len(clean):
        line = clean[i]
        if label_pattern.search(line) or field_pattern.search(line):
            start_line = i + 1
            # Look ahead for a field group (label + input within 8 lines)
            group_end = min(i + 8, len(clean))
            block     = clean[i:group_end]
            has_label = any(label_pattern.search(l) for l in block)
            has_field = any(field_pattern.search(l) for l in block)

            if has_label and has_field:
                # Find the field type
                for bl in block:
                    fm = field_pattern.search(bl)
                    if fm:
                        field_type = fm.group(1).lower()
                        break
                else:
                    field_type = "input"

                name    = f"FormField"
                snippet = line.strip()[:80]
                candidates.append(Candidate(
                    start   = start_line,
                    end     = start_line + len(block) - 1,
                    kind    = "form_field",
                    name    = name,
                    reason  = f"label + {field_type} group -- extract as <FormField label='...' type='{field_type}' />",
                    snippet = snippet,
                ))
                i += len(block)
                continue
        i += 1
    return candidates


def detect_modal_patterns(lines: list[str]) -> list[Candidate]:
    """
    Find modal/drawer/overlay patterns -- fixed position divs with backdrop/close.
    """
    candidates = []
    clean      = strip_comments(lines)

    overlay_re = re.compile(r'position.*fixed|inset-0|backdrop|overlay|z-\[|z-50|slide-panel|drawer', re.IGNORECASE)
    close_re   = re.compile(r'onClose|closeModal|setOpen.*false|onClick.*close', re.IGNORECASE)

    i = 0
    while i < len(clean):
        line = clean[i]
        if overlay_re.search(line):
            start_line = i + 1
            # Scan ahead for close button pattern
            look_ahead = clean[i:min(i+60, len(clean))]
            has_close  = any(close_re.search(l) for l in look_ahead)

            if has_close:
                # Find block extent
                depth  = 0
                opened = False
                j      = i
                while j < min(i + 80, len(clean)):
                    for ch in clean[j]:
                        if ch in "({<":
                            depth += 1
                            opened = True
                        elif ch in ")}>" :
                            depth -= 1
                    if opened and depth <= 0:
                        break
                    j += 1
                end_line = min(j + 1, len(lines))
                span     = end_line - start_line

                if span >= 10:
                    snippet = line.strip()[:80]
                    candidates.append(Candidate(
                        start   = start_line,
                        end     = end_line,
                        kind    = "modal_shell",
                        name    = "ModalShell",
                        reason  = f"Fixed overlay + close handler spanning {span} lines -- extract shell, pass children",
                        snippet = snippet,
                    ))
                    i = j
                    continue
        i += 1
    return candidates


def detect_empty_states(lines: list[str]) -> list[Candidate]:
    """
    Find empty state / loading / error fallback blocks.
    """
    candidates = []
    clean      = strip_comments(lines)

    empty_re = re.compile(
        r'\.length\s*===?\s*0|isEmpty|no\s+\w+\s+found|nothing\s+here|'
        r'isLoading|loading\.\.\.|spinner|skeleton|Skeleton|Loading|'
        r'isError|error\s+occurred|something went wrong',
        re.IGNORECASE
    )

    i = 0
    while i < len(clean):
        line = clean[i]
        if empty_re.search(line):
            start_line = i + 1
            # Look ahead for the JSX block
            depth  = 0
            opened = False
            j      = i
            while j < min(i + 20, len(clean)):
                for ch in clean[j]:
                    if ch in "({":
                        depth += 1
                        opened = True
                    elif ch in ")}":
                        depth -= 1
                if opened and depth <= 0:
                    break
                j += 1
            end_line = j + 1
            span     = end_line - start_line

            if span >= 3:
                # Determine type
                if re.search(r'isLoading|loading|spinner|skeleton', line, re.IGNORECASE):
                    name   = "LoadingState"
                    reason = f"Loading fallback UI spanning {span} lines -- extract as <LoadingState />"
                elif re.search(r'isError|error', line, re.IGNORECASE):
                    name   = "ErrorState"
                    reason = f"Error fallback UI spanning {span} lines -- extract as <ErrorState message='...' />"
                else:
                    name   = "EmptyState"
                    reason = f"Empty state UI spanning {span} lines -- extract as <EmptyState message='...' />"

                snippet = line.strip()[:80]
                candidates.append(Candidate(
                    start   = start_line,
                    end     = end_line,
                    kind    = "empty_state",
                    name    = name,
                    reason  = reason,
                    snippet = snippet,
                ))
                i = j
                continue
        i += 1
    return candidates


def detect_button_groups(lines: list[str]) -> list[Candidate]:
    """
    Find clusters of 3+ buttons within 10 lines of each other.
    """
    candidates = []
    clean      = strip_comments(lines)

    btn_re = re.compile(r'<(button|Button|btn)', re.IGNORECASE)

    i = 0
    while i < len(clean):
        line = clean[i]
        if btn_re.search(line):
            # Count buttons in next 12 lines
            window    = clean[i:min(i+12, len(clean))]
            btn_count = sum(1 for l in window if btn_re.search(l))
            if btn_count >= 3:
                start_line = i + 1
                end_line   = min(i + 12, len(lines))
                snippet    = line.strip()[:80]
                candidates.append(Candidate(
                    start   = start_line,
                    end     = end_line,
                    kind    = "button_group",
                    name    = "ButtonGroup",
                    reason  = f"{btn_count} buttons within {len(window)} lines -- extract as <ButtonGroup> or individual action components",
                    snippet = snippet,
                ))
                i += len(window)
                continue
        i += 1
    return candidates


def detect_large_sections(lines: list[str]) -> list[Candidate]:
    """
    Find self-contained JSX subtrees of 25+ lines rooted at a single tag.
    These are good candidates for named section components.
    """
    candidates = []
    clean      = strip_comments(lines)

    # Look for top-level div/section/article opens at low indent
    section_re = re.compile(r'^\s{0,8}<(div|section|article|aside|header|nav|main|ul|ol)\b')

    i = 0
    while i < len(clean):
        line = clean[i]
        m    = section_re.match(line)
        if m:
            tag        = m.group(1)
            start_line = i + 1
            base_depth = tag_depth(line)

            # Find closing tag at same depth
            depth  = 0
            opened = False
            j      = i
            while j < len(clean):
                l = clean[j]
                opens  = len(re.findall(r'<\w', l))
                closes = len(re.findall(r'</\w|/>', l))
                depth += opens - closes
                if j > i and depth <= 0:
                    break
                j += 1

            end_line = j + 1
            span     = end_line - start_line

            if span >= 25:
                # Try to name from className or id
                class_m = re.search(r'className=["\']([^"\']+)["\']', line)
                id_m    = re.search(r'\bid=["\']([^"\']+)["\']', line)
                if class_m:
                    raw  = class_m.group(1).split()[0]
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                elif id_m:
                    raw  = id_m.group(1)
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                else:
                    name = f"{tag.capitalize()}Section"

                snippet = line.strip()[:80]
                candidates.append(Candidate(
                    start   = start_line,
                    end     = end_line,
                    kind    = "section",
                    name    = name,
                    reason  = f"<{tag}> block spanning {span} lines -- large enough to be its own named section component",
                    snippet = snippet,
                ))
                i = j
                continue
        i += 1
    return candidates


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    """Remove candidates whose line ranges overlap significantly."""
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda c: c.start)
    result     = [candidates[0]]
    for c in candidates[1:]:
        last = result[-1]
        # Allow if starts after last ends, or overlap is less than 3 lines
        overlap = max(0, min(c.end, last.end) - max(c.start, last.start))
        if overlap < 3:
            result.append(c)
        else:
            # Keep the one with longer span (more informative)
            if (c.end - c.start) > (last.end - last.start):
                result[-1] = c
    return result


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(candidates: list[Candidate], file_path: Path, root: Path) -> None:
    file_rel = rel(str(file_path), root)

    if not candidates:
        out()
        out(f"  No reusable component candidates found in {file_rel}.")
        return

    section(f"REUSABLE COMPONENT CANDIDATES -- {len(candidates)} found in {file_rel}")
    out()
    out("  These are blocks of code that COULD be extracted into their own")
    out("  reusable component files. No changes have been made.")
    out()

    for i, c in enumerate(candidates, 1):
        span = c.end - c.start + 1
        out(f"  ┌─ [{i}] <{c.name} />")
        out(f"  │   Lines:   {c.start}–{c.end}  ({span} lines)")
        out(f"  │   Kind:    {KIND.get(c.kind, c.kind)}")
        out(f"  │   Reason:  {c.reason}")
        if c.snippet:
            out(f"  │   At:      {c.snippet}")
        out(f"  └{'─'*62}")
        out()

    section("KIND LEGEND")
    out()
    for k, v in KIND.items():
        out(f"  {v}")
    out()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find blocks of code that could be extracted into reusable components."
    )
    parser.add_argument("--root", default=".", help="Repo root (default: current directory)")
    parser.add_argument("--file", default=None,  help="File to scan (prompts if not provided)")
    parser.add_argument("--json", default=None,  help="Write results to this JSON path")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    # ── Locate file (same pattern as other audit scripts) ─────────────────────

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

    if target.suffix not in {".tsx", ".jsx", ".ts", ".js"}:
        print(f"\n  [WARN] Expected a .tsx/.jsx file, got {target.suffix} -- results may be limited.")

    header(
        f"COMPONENT EXTRACTOR AUDIT: {target.relative_to(root)}",
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
    out("  Running detectors...")
    out()

    # Run all detectors
    candidates: list[Candidate] = []
    candidates += detect_map_renders(lines)
    candidates += detect_repeated_blocks(lines)
    candidates += detect_large_conditionals(lines)
    candidates += detect_form_fields(lines)
    candidates += detect_modal_patterns(lines)
    candidates += detect_empty_states(lines)
    candidates += detect_button_groups(lines)
    candidates += detect_large_sections(lines)

    # Deduplicate overlapping results
    candidates = deduplicate(candidates)
    candidates = sorted(candidates, key=lambda c: c.start)

    print_report(candidates, target, root)

    # ── JSON output ───────────────────────────────────────────────────────────

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if args.json:
        out_path = Path(args.json).resolve()
    else:
        out_path = root / f"components-{target.stem}-{ts}.json"

    serializable = {
        "file":       rel(str(target), root),
        "line_count": len(lines),
        "candidates": [c.to_dict() for c in candidates],
        "generated":  datetime.now().__str__(),
    }

    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    out(f"  Results saved → {out_path}")
    out(f"  Run: python scripts/audits/find_components.py --root {root} --file {target.relative_to(root)}")
    out()


if __name__ == "__main__":
    main()