#!/usr/bin/env python3
"""
find_design_components.py
─────────────────────────
Scans a .tsx / .jsx file and finds design blocks that could be extracted
into reusable design components.

Works with ANY styling approach:
  - CSS class names (className="bubble me")
  - Inline style={{}} objects
  - Tailwind utility classes
  - CSS custom properties (var(--gold), var(--font-ui))
  - Mixed approaches

Detects:
  - Message bubbles / chat bubbles
  - Input bars (text input + send/attach buttons)
  - Date dividers / section dividers
  - Error / status banners
  - Upload overlay states
  - Empty states (no content messages)
  - Repeated map() item renders
  - Modal / overlay shells
  - Card / tile wrappers
  - Button groups
  - Icon + label combos
  - Stat / metric blocks
  - Form field groups (label + input)

For each candidate:
  Terminal: line range, kind, reason, all class names AND style keys found
  .txt file: full extracted code + suggested component shell

Usage:
  python scripts/audits/find_design_components.py [--root PATH] [--file PATH] [--out DIR]

  --root   Repo root (default: current directory)
  --file   File to scan (prompts if not provided)
  --out    Directory for .txt output (default: repo root)
"""

import argparse
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


# ── Kind labels ───────────────────────────────────────────────────────────────

KIND = {
    "chat_bubble":    "ChatBubble        -- message bubble with sender/receiver variants",
    "input_bar":      "InputBar          -- text input + action button(s) toolbar",
    "date_divider":   "DateDivider       -- date separator between message groups",
    "error_banner":   "ErrorBanner       -- dismissible error/warning notification strip",
    "upload_overlay": "UploadOverlay     -- uploading/failed state overlay on media",
    "empty_state":    "EmptyState        -- no-content message with optional illustration",
    "map_item":       "ListItem          -- repeated item body inside a .map() render",
    "modal_shell":    "ModalShell        -- overlay + backdrop + close button shell",
    "card":           "Card              -- bordered/shadowed content container",
    "button_group":   "ButtonGroup       -- cluster of related action buttons",
    "icon_label":     "IconLabel         -- icon + text side-by-side combo",
    "stat_block":     "StatBlock         -- metric value + label display",
    "form_field":     "FormField         -- label + input/select/textarea group",
    "status_badge":   "StatusBadge       -- small inline status/label indicator",
    "section_block":  "SectionBlock      -- large self-contained JSX subtree",
    "pending_item":   "PendingItem       -- optimistic/pending state UI for uploads or actions",
}


# ── Styling extractor ─────────────────────────────────────────────────────────

def extract_design_tokens(block: list[str]) -> dict:
    """
    Extract ALL design-relevant strings from a block regardless of styling approach.
    Returns dict with keys: classes, style_keys, css_vars, inline_values
    """
    classes      = []
    style_keys   = []
    css_vars     = []
    inline_vals  = []

    for line in block:
        # className="foo bar" or className='foo bar'
        for m in re.finditer(r'className=["\']([^"\']+)["\']', line):
            classes += m.group(1).split()

        # className={`foo bar ${x} baz`}
        for m in re.finditer(r'className=\{`([^`]+)`\}', line):
            literal = re.sub(r'\$\{[^}]+\}', '', m.group(1))
            classes += literal.split()

        # className={styles.foo} or className={cx('foo', 'bar')}
        for m in re.finditer(r'className=\{[^}]+\}', line):
            inner = m.group(0)
            for tok in re.finditer(r'["\']([a-z][a-z0-9-_]+)["\']', inner):
                classes.append(tok.group(1))

        # inline style keys: fontFamily, borderRadius, background, etc.
        for m in re.finditer(r'(\w+):\s*["\'{]', line):
            key = m.group(1)
            if key[0].islower() and len(key) > 2 and key not in {'src', 'alt', 'ref', 'key', 'for'}:
                style_keys.append(key)

        # CSS custom properties: var(--gold), var(--font-ui-mob)
        for m in re.finditer(r'var\((--[^)]+)\)', line):
            css_vars.append(m.group(1))

        # Notable inline values: colors, clamp(), px values
        for m in re.finditer(r'(?:rgba?\([^)]+\)|clamp\([^)]+\)|#[0-9a-fA-F]{3,6}|\d+px)', line):
            inline_vals.append(m.group(0)[:40])

    # Deduplicate preserving order
    def dedup(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]

    return {
        "classes":     dedup(classes),
        "style_keys":  dedup(style_keys),
        "css_vars":    dedup(css_vars),
        "inline_vals": dedup(inline_vals[:12]),  # cap inline vals
    }


def format_design_summary(tokens: dict) -> str:
    """Format design tokens into a readable terminal line."""
    parts = []
    if tokens["classes"]:
        parts.append("classes: " + " ".join(tokens["classes"]))
    if tokens["css_vars"]:
        parts.append("vars: " + " ".join(tokens["css_vars"]))
    if tokens["style_keys"]:
        keys = [k for k in tokens["style_keys"]
                if k not in {"display", "flex", "width", "height", "margin", "padding"}]
        if keys:
            parts.append("style: " + " ".join(keys[:8]))
    return " │ ".join(parts) if parts else "(no design tokens found)"


# ── Block finder ──────────────────────────────────────────────────────────────

def find_block_end(lines: list[str], start: int, max_lines: int = 80) -> int:
    """Find the closing line of a JSX block starting at `start`."""
    depth  = 0
    opened = False
    j      = start
    limit  = min(start + max_lines, len(lines))
    while j < limit:
        l      = lines[j]
        opens  = l.count('<') - l.count('</') - l.count('/>') + l.count('(') + l.count('{')
        closes = l.count('</') + l.count('/>') + l.count(')') + l.count('}')
        depth += opens - closes
        if j > start and depth <= 0:
            return j + 1
        opened = True
        j += 1
    return j


def strip_comments(lines: list[str]) -> list[str]:
    result   = []
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
                 reason: str, tokens: dict, code: list[str]):
        self.start  = start   # 1-indexed
        self.end    = end
        self.kind   = kind
        self.name   = name
        self.reason = reason
        self.tokens = tokens
        self.code   = code

    def to_dict(self) -> dict:
        return {
            "lines":  f"{self.start}–{self.end}",
            "start":  self.start,
            "end":    self.end,
            "kind":   self.kind,
            "name":   self.name,
            "reason": self.reason,
            **self.tokens,
        }


# ── Detectors ─────────────────────────────────────────────────────────────────

def detect_chat_bubbles(lines, clean) -> list[Candidate]:
    """bubble, msg-wrap, chat-bubble, message-bubble patterns."""
    candidates = []
    bubble_re  = re.compile(r'bubble|msg-wrap|message-wrap|chat-bubble|message-bubble', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if bubble_re.search(clean[i]):
            end   = find_block_end(clean, i, 30)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            if len(block) >= 3:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "chat_bubble",
                    name   = "ChatBubble",
                    reason = f"message bubble pattern ({len(block)} lines) -- extract with isMe/sender prop",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_input_bars(lines, clean) -> list[Candidate]:
    """input + button combos forming a send/compose bar."""
    candidates = []
    bar_re     = re.compile(r'chat-input|input-bar|compose|message-input|send-bar', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if bar_re.search(line):
            end   = find_block_end(clean, i, 25)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            has_input  = any(re.search(r'<input|<Input|<textarea', l) for l in block)
            has_button = any(re.search(r'<button|<Button', l) for l in block)
            if has_input and has_button:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "input_bar",
                    name   = "InputBar",
                    reason = f"input + button toolbar ({len(block)} lines) -- extract as <InputBar onSend={{}} onAttach={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    # Also catch unlabelled input + button bars
    i = 0
    reported = {c.start for c in candidates}
    while i < len(clean):
        line = clean[i]
        if re.search(r'<input|<Input', line) and i not in reported:
            lookahead  = clean[i:min(i+8, len(clean))]
            has_button = any(re.search(r'<button|<Button', l) for l in lookahead)
            has_send   = any(re.search(r'send|submit|attach|upload|onClick', l, re.IGNORECASE) for l in lookahead)
            if has_button and has_send:
                end   = i + len(lookahead)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "input_bar",
                    name   = "InputBar",
                    reason = f"input + send/attach button group ({len(block)} lines) -- extract as <InputBar />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_date_dividers(lines, clean) -> list[Candidate]:
    """date-divider, divider-line + divider-label patterns."""
    candidates = []
    div_re     = re.compile(r'date-divider|divider-line|divider-lbl|date-separator|section-divider', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if div_re.search(clean[i]):
            end   = find_block_end(clean, i, 12)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            candidates.append(Candidate(
                start  = i + 1,
                end    = end,
                kind   = "date_divider",
                name   = "DateDivider",
                reason = f"date/section divider pattern ({len(block)} lines) -- extract as <DateDivider label='Today' />",
                tokens = tokens,
                code   = block,
            ))
            i = end
            continue
        i += 1
    return candidates


def detect_error_banners(lines, clean) -> list[Candidate]:
    """Error/warning banners with dismiss buttons."""
    candidates = []
    err_re     = re.compile(r'error|warning|alert|banner|rgba.*68.*68|#ef4444|border.*red|text.*red', re.IGNORECASE)
    dismiss_re = re.compile(r'clearError|clearChatError|dismiss|onClose|onClick.*clear', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if err_re.search(line):
            lookahead = clean[i:min(i+20, len(clean))]
            has_dismiss = any(dismiss_re.search(l) for l in lookahead)
            if has_dismiss:
                end   = find_block_end(clean, i, 25)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "error_banner",
                    name   = "ErrorBanner",
                    reason = f"error/warning banner with dismiss ({len(block)} lines) -- extract as <ErrorBanner message={{}} onDismiss={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_upload_overlays(lines, clean) -> list[Candidate]:
    """Upload progress / failed overlays on media."""
    candidates = []
    upload_re  = re.compile(r'uploading|upload.fail|upload.error|p\.uploading|p\.error|pendingUpload', re.IGNORECASE)
    overlay_re = re.compile(r'position.*absolute|inset.*0|rgba\(0.*0.*0|background.*rgba', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if upload_re.search(line):
            lookahead   = clean[i:min(i+30, len(clean))]
            has_overlay = any(overlay_re.search(l) for l in lookahead)
            if has_overlay:
                end   = find_block_end(clean, i, 40)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "upload_overlay",
                    name   = "UploadOverlay",
                    reason = f"upload progress/error overlay ({len(block)} lines) -- extract as <UploadOverlay uploading={{}} error={{}} onDismiss={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_empty_states(lines, clean) -> list[Candidate]:
    """No-content / empty state messages."""
    candidates = []
    empty_re   = re.compile(
        r'\.length\s*===?\s*0|no messages|nothing here|say hello|no results|'
        r'isEmpty|no \w+ yet|nothing to show|get started',
        re.IGNORECASE
    )
    i = 0
    while i < len(clean):
        if empty_re.search(clean[i]):
            end   = find_block_end(clean, i, 15)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            if len(block) >= 2:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "empty_state",
                    name   = "EmptyState",
                    reason = f"empty/no-content state ({len(block)} lines) -- extract as <EmptyState message='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_map_items(lines, clean) -> list[Candidate]:
    """Item bodies inside .map() renders."""
    candidates = []
    i = 0
    while i < len(clean):
        m = re.search(r'(\w+)\.map\(\s*[\(\[]?(\w+)', clean[i])
        if m:
            collection = m.group(1)
            item_var   = m.group(2)
            start_line = i + 1
            end        = find_block_end(clean, i, 60)
            block      = lines[i:end]
            span       = end - i
            if span >= 5:
                singular = re.sub(r'(?i)(ies)$', 'y',
                           re.sub(r's$', '', collection))
                name     = singular[0].upper() + singular[1:] + "Item"
                tokens   = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = start_line,
                    end    = end,
                    kind   = "map_item",
                    name   = name,
                    reason = f"'{collection}.map({item_var} => ...)' body is {span} lines -- extract item as <{name} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_modal_shells(lines, clean) -> list[Candidate]:
    """Fixed/absolute overlay + close button patterns."""
    candidates = []
    overlay_re = re.compile(r'slide-panel|modal|drawer|overlay|position.*fixed|inset-0|z-\d+|z-\[', re.IGNORECASE)
    close_re   = re.compile(r'onClose|closeModal|setOpen.*false|panel-close|modal-close', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if overlay_re.search(line):
            lookahead   = clean[i:min(i+40, len(clean))]
            has_close   = any(close_re.search(l) for l in lookahead)
            if has_close:
                end   = find_block_end(clean, i, 80)
                block = lines[i:end]
                if len(block) >= 10:
                    tokens = extract_design_tokens(block)
                    candidates.append(Candidate(
                        start  = i + 1,
                        end    = end,
                        kind   = "modal_shell",
                        name   = "ModalShell",
                        reason = f"panel/modal/drawer shell ({len(block)} lines) -- extract wrapper, pass children + onClose",
                        tokens = tokens,
                        code   = block,
                    ))
                    i = end
                    continue
        i += 1
    return candidates


def detect_cards(lines, clean) -> list[Candidate]:
    """Card/tile wrappers -- bordered, shadowed, padded containers."""
    candidates  = []
    card_css_re = re.compile(
        r'border.*radius|borderRadius|border-radius|shadow|rounded|'
        r'card|tile|panel-card|item-card',
        re.IGNORECASE
    )
    fingerprints: dict[str, list[int]] = defaultdict(list)
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<div|<li|<article', line) and card_css_re.search(line):
            tokens = extract_design_tokens([line])
            fp     = " ".join(sorted(tokens["classes"] + tokens["style_keys"]))
            if fp.strip():
                fingerprints[fp].append(i)
        i += 1

    reported = set()
    for fp, positions in fingerprints.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                end   = find_block_end(clean, pos, 40)
                block = lines[pos:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = pos + 1,
                    end    = end,
                    kind   = "card",
                    name   = "Card",
                    reason = f"repeated card/tile structure appears {len(positions)}x -- extract as <Card />",
                    tokens = tokens,
                    code   = block,
                ))
    return candidates


def detect_button_groups(lines, clean) -> list[Candidate]:
    """3+ buttons close together."""
    candidates = []
    btn_re     = re.compile(r'<button|<Button', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if btn_re.search(clean[i]):
            window    = clean[i:min(i+10, len(clean))]
            btn_count = sum(1 for l in window if btn_re.search(l))
            if btn_count >= 3:
                end   = i + len(window)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "button_group",
                    name   = "ButtonGroup",
                    reason = f"{btn_count} buttons within {len(window)} lines -- extract as <ButtonGroup> or split into action components",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_form_fields(lines, clean) -> list[Candidate]:
    """label + input/select/textarea groups."""
    candidates = []
    label_re   = re.compile(r'<label|<Label', re.IGNORECASE)
    field_re   = re.compile(r'<input|<Input|<textarea|<Textarea|<select|<Select', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if label_re.search(line) or field_re.search(line):
            window     = clean[i:min(i+8, len(clean))]
            has_label  = any(label_re.search(l) for l in window)
            has_field  = any(field_re.search(l) for l in window)
            if has_label and has_field:
                end    = i + len(window)
                block  = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "form_field",
                    name   = "FormField",
                    reason = f"label + input group ({len(block)} lines) -- extract as <FormField label='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_status_badges(lines, clean) -> list[Candidate]:
    """Small inline status/label indicators."""
    candidates = []
    badge_re   = re.compile(
        r'status|badge|pill|chip|tag|label.*color|rounded.*small|'
        r'rounded-full|text-xs.*bg-|bg-.*text-xs',
        re.IGNORECASE
    )
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<span|<div|<p', line) and badge_re.search(line):
            end   = find_block_end(clean, i, 8)
            block = lines[i:end]
            if len(block) <= 6:
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "status_badge",
                    name   = "StatusBadge",
                    reason = f"inline status/label indicator ({len(block)} lines) -- extract as <StatusBadge status='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_pending_items(lines, clean) -> list[Candidate]:
    """Optimistic/pending state UI for uploads or mutations."""
    candidates = []
    pending_re = re.compile(r'pending|optimistic|p\.tempId|tempId|p\.uploading|isLoading.*item', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if pending_re.search(clean[i]):
            end   = find_block_end(clean, i, 40)
            block = lines[i:end]
            if len(block) >= 5:
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "pending_item",
                    name   = "PendingItem",
                    reason = f"pending/optimistic state UI ({len(block)} lines) -- extract as <PendingItem uploading={{}} error={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_large_sections(lines, clean) -> list[Candidate]:
    """Large self-contained JSX subtrees not caught by other detectors."""
    candidates = []
    section_re = re.compile(r'^\s{0,8}<(div|section|article|aside|ul|ol)\b')
    i = 0
    while i < len(clean):
        m = section_re.match(clean[i])
        if m:
            end  = find_block_end(clean, i, 100)
            span = end - i
            if span >= 30:
                block  = lines[i:end]
                tokens = extract_design_tokens(block)
                # Name from className or id
                class_m = re.search(r'className=["\']([^"\']+)["\']', lines[i])
                id_m    = re.search(r'\bid=["\']([^"\']+)["\']', lines[i])
                if class_m:
                    raw  = class_m.group(1).split()[0]
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                elif id_m:
                    raw  = id_m.group(1)
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                else:
                    name = m.group(1).capitalize() + "Section"
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "section_block",
                    name   = name,
                    reason = f"<{m.group(1)}> block spanning {span} lines -- large enough to be its own section component",
                    tokens = tokens,
                    code   = block,
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
        if overlap < 4:
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

    section(f"REUSABLE DESIGN COMPONENTS -- {len(candidates)} candidates in {file_rel}")
    out()
    out("  Design blocks that COULD be extracted into reusable components.")
    out("  No files have been changed.")
    out()

    for i, c in enumerate(candidates, 1):
        span    = c.end - c.start + 1
        summary = format_design_summary(c.tokens)
        out(f"  ┌─ [{i}] <{c.name} />")
        out(f"  │   Lines:   {c.start}–{c.end}  ({span} lines)")
        out(f"  │   Kind:    {KIND.get(c.kind, c.kind)}")
        out(f"  │   Reason:  {c.reason}")
        out(f"  │   Design:  {summary}")
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
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"design-extracts-{file_path.stem}-{ts}.txt"

    lines_out = []
    lines_out.append("DESIGN COMPONENT EXTRACTS")
    lines_out.append(f"Source:     {rel(str(file_path), root)}")
    lines_out.append(f"Generated:  {datetime.now()}")
    lines_out.append(f"Candidates: {len(candidates)}")
    lines_out.append("=" * 66)
    lines_out.append("")

    for i, c in enumerate(candidates, 1):
        summary = format_design_summary(c.tokens)
        lines_out.append(f"[{i}] <{c.name} />")
        lines_out.append(f"    Lines:   {c.start}–{c.end}")
        lines_out.append(f"    Kind:    {KIND.get(c.kind, c.kind)}")
        lines_out.append(f"    Reason:  {c.reason}")
        lines_out.append(f"    Design:  {summary}")
        lines_out.append("")
        lines_out.append("    ── Extracted Code " + "─" * 48)
        lines_out.append("")
        for line in c.code:
            lines_out.append("    " + line)
        lines_out.append("")
        lines_out.append("    ── Suggested Component Shell " + "─" * 38)
        lines_out.append("")
        lines_out.append(f"    // components/design/{c.name}.tsx")
        lines_out.append(f"    import React from 'react'")
        lines_out.append(f"")
        lines_out.append(f"    type {c.name}Props = {{")
        lines_out.append(f"      children?: React.ReactNode")
        lines_out.append(f"      className?: string")
        lines_out.append(f"      // TODO: add props for each hardcoded value above")
        lines_out.append(f"    }}")
        lines_out.append(f"")
        lines_out.append(f"    export function {c.name}({{ children, className }}: {c.name}Props) {{")
        lines_out.append(f"      return (")
        lines_out.append(f"        // paste extracted code here")
        lines_out.append(f"        // replace hardcoded values with props")
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
        description="Find design blocks that could become reusable design components."
    )
    parser.add_argument("--root", default=".", help="Repo root (default: current directory)")
    parser.add_argument("--file", default=None,  help="File to scan (prompts if not provided)")
    parser.add_argument("--out",  default=None,  help="Directory for .txt output (default: repo root)")
    args = parser.parse_args()

    root = Path(args.root).resolve()

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
        print(f"\n  [ERROR] Could not read: {target}")
        sys.exit(1)

    out(f"  File:   {rel(str(target), root)}")
    out(f"  Lines:  {len(lines)}")
    out()
    out("  Running detectors...")
    out()

    clean = strip_comments(lines)

    candidates: list[Candidate] = []
    candidates += detect_chat_bubbles(lines, clean)
    candidates += detect_input_bars(lines, clean)
    candidates += detect_date_dividers(lines, clean)
    candidates += detect_error_banners(lines, clean)
    candidates += detect_upload_overlays(lines, clean)
    candidates += detect_empty_states(lines, clean)
    candidates += detect_map_items(lines, clean)
    candidates += detect_modal_shells(lines, clean)
    candidates += detect_cards(lines, clean)
    candidates += detect_button_groups(lines, clean)
    candidates += detect_form_fields(lines, clean)
    candidates += detect_status_badges(lines, clean)
    candidates += detect_pending_items(lines, clean)
    candidates += detect_large_sections(lines, clean)

    candidates = deduplicate(candidates)
    candidates = sorted(candidates, key=lambda c: c.start)

    print_report(candidates, target, root)

    if candidates:
        txt_path = write_extracts(candidates, target, out_dir, root)
        out(f"  Extracts saved → {txt_path}")
        out()

    out(f"  Run: python scripts/audits/find_design_components.py --root {root} --file {target.relative_to(root)}")
    out()


def read_file(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None


if __name__ == "__main__":
    main()#!/usr/bin/env python3
"""
find_design_components.py
─────────────────────────
Scans a .tsx / .jsx file and finds design blocks that could be extracted
into reusable design components.

Works with ANY styling approach:
  - CSS class names (className="bubble me")
  - Inline style={{}} objects
  - Tailwind utility classes
  - CSS custom properties (var(--gold), var(--font-ui))
  - Mixed approaches

Detects:
  - Message bubbles / chat bubbles
  - Input bars (text input + send/attach buttons)
  - Date dividers / section dividers
  - Error / status banners
  - Upload overlay states
  - Empty states (no content messages)
  - Repeated map() item renders
  - Modal / overlay shells
  - Card / tile wrappers
  - Button groups
  - Icon + label combos
  - Stat / metric blocks
  - Form field groups (label + input)

For each candidate:
  Terminal: line range, kind, reason, all class names AND style keys found
  .txt file: full extracted code + suggested component shell

Usage:
  python scripts/audits/find_design_components.py [--root PATH] [--file PATH] [--out DIR]

  --root   Repo root (default: current directory)
  --file   File to scan (prompts if not provided)
  --out    Directory for .txt output (default: repo root)
"""

import argparse
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


# ── Kind labels ───────────────────────────────────────────────────────────────

KIND = {
    "chat_bubble":    "ChatBubble        -- message bubble with sender/receiver variants",
    "input_bar":      "InputBar          -- text input + action button(s) toolbar",
    "date_divider":   "DateDivider       -- date separator between message groups",
    "error_banner":   "ErrorBanner       -- dismissible error/warning notification strip",
    "upload_overlay": "UploadOverlay     -- uploading/failed state overlay on media",
    "empty_state":    "EmptyState        -- no-content message with optional illustration",
    "map_item":       "ListItem          -- repeated item body inside a .map() render",
    "modal_shell":    "ModalShell        -- overlay + backdrop + close button shell",
    "card":           "Card              -- bordered/shadowed content container",
    "button_group":   "ButtonGroup       -- cluster of related action buttons",
    "icon_label":     "IconLabel         -- icon + text side-by-side combo",
    "stat_block":     "StatBlock         -- metric value + label display",
    "form_field":     "FormField         -- label + input/select/textarea group",
    "status_badge":   "StatusBadge       -- small inline status/label indicator",
    "section_block":  "SectionBlock      -- large self-contained JSX subtree",
    "pending_item":   "PendingItem       -- optimistic/pending state UI for uploads or actions",
}


# ── Styling extractor ─────────────────────────────────────────────────────────

def extract_design_tokens(block: list[str]) -> dict:
    """
    Extract ALL design-relevant strings from a block regardless of styling approach.
    Returns dict with keys: classes, style_keys, css_vars, inline_values
    """
    classes      = []
    style_keys   = []
    css_vars     = []
    inline_vals  = []

    for line in block:
        # className="foo bar" or className='foo bar'
        for m in re.finditer(r'className=["\']([^"\']+)["\']', line):
            classes += m.group(1).split()

        # className={`foo bar ${x} baz`}
        for m in re.finditer(r'className=\{`([^`]+)`\}', line):
            literal = re.sub(r'\$\{[^}]+\}', '', m.group(1))
            classes += literal.split()

        # className={styles.foo} or className={cx('foo', 'bar')}
        for m in re.finditer(r'className=\{[^}]+\}', line):
            inner = m.group(0)
            for tok in re.finditer(r'["\']([a-z][a-z0-9-_]+)["\']', inner):
                classes.append(tok.group(1))

        # inline style keys: fontFamily, borderRadius, background, etc.
        for m in re.finditer(r'(\w+):\s*["\'{]', line):
            key = m.group(1)
            if key[0].islower() and len(key) > 2 and key not in {'src', 'alt', 'ref', 'key', 'for'}:
                style_keys.append(key)

        # CSS custom properties: var(--gold), var(--font-ui-mob)
        for m in re.finditer(r'var\((--[^)]+)\)', line):
            css_vars.append(m.group(1))

        # Notable inline values: colors, clamp(), px values
        for m in re.finditer(r'(?:rgba?\([^)]+\)|clamp\([^)]+\)|#[0-9a-fA-F]{3,6}|\d+px)', line):
            inline_vals.append(m.group(0)[:40])

    # Deduplicate preserving order
    def dedup(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]

    return {
        "classes":     dedup(classes),
        "style_keys":  dedup(style_keys),
        "css_vars":    dedup(css_vars),
        "inline_vals": dedup(inline_vals[:12]),  # cap inline vals
    }


def format_design_summary(tokens: dict) -> str:
    """Format design tokens into a readable terminal line."""
    parts = []
    if tokens["classes"]:
        parts.append("classes: " + " ".join(tokens["classes"]))
    if tokens["css_vars"]:
        parts.append("vars: " + " ".join(tokens["css_vars"]))
    if tokens["style_keys"]:
        keys = [k for k in tokens["style_keys"]
                if k not in {"display", "flex", "width", "height", "margin", "padding"}]
        if keys:
            parts.append("style: " + " ".join(keys[:8]))
    return " │ ".join(parts) if parts else "(no design tokens found)"


# ── Block finder ──────────────────────────────────────────────────────────────

def find_block_end(lines: list[str], start: int, max_lines: int = 80) -> int:
    """Find the closing line of a JSX block starting at `start`."""
    depth  = 0
    opened = False
    j      = start
    limit  = min(start + max_lines, len(lines))
    while j < limit:
        l      = lines[j]
        opens  = l.count('<') - l.count('</') - l.count('/>') + l.count('(') + l.count('{')
        closes = l.count('</') + l.count('/>') + l.count(')') + l.count('}')
        depth += opens - closes
        if j > start and depth <= 0:
            return j + 1
        opened = True
        j += 1
    return j


def strip_comments(lines: list[str]) -> list[str]:
    result   = []
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
                 reason: str, tokens: dict, code: list[str]):
        self.start  = start   # 1-indexed
        self.end    = end
        self.kind   = kind
        self.name   = name
        self.reason = reason
        self.tokens = tokens
        self.code   = code

    def to_dict(self) -> dict:
        return {
            "lines":  f"{self.start}–{self.end}",
            "start":  self.start,
            "end":    self.end,
            "kind":   self.kind,
            "name":   self.name,
            "reason": self.reason,
            **self.tokens,
        }


# ── Detectors ─────────────────────────────────────────────────────────────────

def detect_chat_bubbles(lines, clean) -> list[Candidate]:
    """bubble, msg-wrap, chat-bubble, message-bubble patterns."""
    candidates = []
    bubble_re  = re.compile(r'bubble|msg-wrap|message-wrap|chat-bubble|message-bubble', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if bubble_re.search(clean[i]):
            end   = find_block_end(clean, i, 30)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            if len(block) >= 3:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "chat_bubble",
                    name   = "ChatBubble",
                    reason = f"message bubble pattern ({len(block)} lines) -- extract with isMe/sender prop",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_input_bars(lines, clean) -> list[Candidate]:
    """input + button combos forming a send/compose bar."""
    candidates = []
    bar_re     = re.compile(r'chat-input|input-bar|compose|message-input|send-bar', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if bar_re.search(line):
            end   = find_block_end(clean, i, 25)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            has_input  = any(re.search(r'<input|<Input|<textarea', l) for l in block)
            has_button = any(re.search(r'<button|<Button', l) for l in block)
            if has_input and has_button:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "input_bar",
                    name   = "InputBar",
                    reason = f"input + button toolbar ({len(block)} lines) -- extract as <InputBar onSend={{}} onAttach={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    # Also catch unlabelled input + button bars
    i = 0
    reported = {c.start for c in candidates}
    while i < len(clean):
        line = clean[i]
        if re.search(r'<input|<Input', line) and i not in reported:
            lookahead  = clean[i:min(i+8, len(clean))]
            has_button = any(re.search(r'<button|<Button', l) for l in lookahead)
            has_send   = any(re.search(r'send|submit|attach|upload|onClick', l, re.IGNORECASE) for l in lookahead)
            if has_button and has_send:
                end   = i + len(lookahead)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "input_bar",
                    name   = "InputBar",
                    reason = f"input + send/attach button group ({len(block)} lines) -- extract as <InputBar />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_date_dividers(lines, clean) -> list[Candidate]:
    """date-divider, divider-line + divider-label patterns."""
    candidates = []
    div_re     = re.compile(r'date-divider|divider-line|divider-lbl|date-separator|section-divider', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if div_re.search(clean[i]):
            end   = find_block_end(clean, i, 12)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            candidates.append(Candidate(
                start  = i + 1,
                end    = end,
                kind   = "date_divider",
                name   = "DateDivider",
                reason = f"date/section divider pattern ({len(block)} lines) -- extract as <DateDivider label='Today' />",
                tokens = tokens,
                code   = block,
            ))
            i = end
            continue
        i += 1
    return candidates


def detect_error_banners(lines, clean) -> list[Candidate]:
    """Error/warning banners with dismiss buttons."""
    candidates = []
    err_re     = re.compile(r'error|warning|alert|banner|rgba.*68.*68|#ef4444|border.*red|text.*red', re.IGNORECASE)
    dismiss_re = re.compile(r'clearError|clearChatError|dismiss|onClose|onClick.*clear', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if err_re.search(line):
            lookahead = clean[i:min(i+20, len(clean))]
            has_dismiss = any(dismiss_re.search(l) for l in lookahead)
            if has_dismiss:
                end   = find_block_end(clean, i, 25)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "error_banner",
                    name   = "ErrorBanner",
                    reason = f"error/warning banner with dismiss ({len(block)} lines) -- extract as <ErrorBanner message={{}} onDismiss={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_upload_overlays(lines, clean) -> list[Candidate]:
    """Upload progress / failed overlays on media."""
    candidates = []
    upload_re  = re.compile(r'uploading|upload.fail|upload.error|p\.uploading|p\.error|pendingUpload', re.IGNORECASE)
    overlay_re = re.compile(r'position.*absolute|inset.*0|rgba\(0.*0.*0|background.*rgba', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if upload_re.search(line):
            lookahead   = clean[i:min(i+30, len(clean))]
            has_overlay = any(overlay_re.search(l) for l in lookahead)
            if has_overlay:
                end   = find_block_end(clean, i, 40)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "upload_overlay",
                    name   = "UploadOverlay",
                    reason = f"upload progress/error overlay ({len(block)} lines) -- extract as <UploadOverlay uploading={{}} error={{}} onDismiss={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_empty_states(lines, clean) -> list[Candidate]:
    """No-content / empty state messages."""
    candidates = []
    empty_re   = re.compile(
        r'\.length\s*===?\s*0|no messages|nothing here|say hello|no results|'
        r'isEmpty|no \w+ yet|nothing to show|get started',
        re.IGNORECASE
    )
    i = 0
    while i < len(clean):
        if empty_re.search(clean[i]):
            end   = find_block_end(clean, i, 15)
            block = lines[i:end]
            tokens = extract_design_tokens(block)
            if len(block) >= 2:
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "empty_state",
                    name   = "EmptyState",
                    reason = f"empty/no-content state ({len(block)} lines) -- extract as <EmptyState message='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_map_items(lines, clean) -> list[Candidate]:
    """Item bodies inside .map() renders."""
    candidates = []
    i = 0
    while i < len(clean):
        m = re.search(r'(\w+)\.map\(\s*[\(\[]?(\w+)', clean[i])
        if m:
            collection = m.group(1)
            item_var   = m.group(2)
            start_line = i + 1
            end        = find_block_end(clean, i, 60)
            block      = lines[i:end]
            span       = end - i
            if span >= 5:
                singular = re.sub(r'(?i)(ies)$', 'y',
                           re.sub(r's$', '', collection))
                name     = singular[0].upper() + singular[1:] + "Item"
                tokens   = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = start_line,
                    end    = end,
                    kind   = "map_item",
                    name   = name,
                    reason = f"'{collection}.map({item_var} => ...)' body is {span} lines -- extract item as <{name} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_modal_shells(lines, clean) -> list[Candidate]:
    """Fixed/absolute overlay + close button patterns."""
    candidates = []
    overlay_re = re.compile(r'slide-panel|modal|drawer|overlay|position.*fixed|inset-0|z-\d+|z-\[', re.IGNORECASE)
    close_re   = re.compile(r'onClose|closeModal|setOpen.*false|panel-close|modal-close', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if overlay_re.search(line):
            lookahead   = clean[i:min(i+40, len(clean))]
            has_close   = any(close_re.search(l) for l in lookahead)
            if has_close:
                end   = find_block_end(clean, i, 80)
                block = lines[i:end]
                if len(block) >= 10:
                    tokens = extract_design_tokens(block)
                    candidates.append(Candidate(
                        start  = i + 1,
                        end    = end,
                        kind   = "modal_shell",
                        name   = "ModalShell",
                        reason = f"panel/modal/drawer shell ({len(block)} lines) -- extract wrapper, pass children + onClose",
                        tokens = tokens,
                        code   = block,
                    ))
                    i = end
                    continue
        i += 1
    return candidates


def detect_cards(lines, clean) -> list[Candidate]:
    """Card/tile wrappers -- bordered, shadowed, padded containers."""
    candidates  = []
    card_css_re = re.compile(
        r'border.*radius|borderRadius|border-radius|shadow|rounded|'
        r'card|tile|panel-card|item-card',
        re.IGNORECASE
    )
    fingerprints: dict[str, list[int]] = defaultdict(list)
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<div|<li|<article', line) and card_css_re.search(line):
            tokens = extract_design_tokens([line])
            fp     = " ".join(sorted(tokens["classes"] + tokens["style_keys"]))
            if fp.strip():
                fingerprints[fp].append(i)
        i += 1

    reported = set()
    for fp, positions in fingerprints.items():
        if len(positions) >= 2:
            for pos in positions:
                if pos in reported:
                    continue
                reported.add(pos)
                end   = find_block_end(clean, pos, 40)
                block = lines[pos:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = pos + 1,
                    end    = end,
                    kind   = "card",
                    name   = "Card",
                    reason = f"repeated card/tile structure appears {len(positions)}x -- extract as <Card />",
                    tokens = tokens,
                    code   = block,
                ))
    return candidates


def detect_button_groups(lines, clean) -> list[Candidate]:
    """3+ buttons close together."""
    candidates = []
    btn_re     = re.compile(r'<button|<Button', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if btn_re.search(clean[i]):
            window    = clean[i:min(i+10, len(clean))]
            btn_count = sum(1 for l in window if btn_re.search(l))
            if btn_count >= 3:
                end   = i + len(window)
                block = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "button_group",
                    name   = "ButtonGroup",
                    reason = f"{btn_count} buttons within {len(window)} lines -- extract as <ButtonGroup> or split into action components",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_form_fields(lines, clean) -> list[Candidate]:
    """label + input/select/textarea groups."""
    candidates = []
    label_re   = re.compile(r'<label|<Label', re.IGNORECASE)
    field_re   = re.compile(r'<input|<Input|<textarea|<Textarea|<select|<Select', re.IGNORECASE)
    i = 0
    while i < len(clean):
        line = clean[i]
        if label_re.search(line) or field_re.search(line):
            window     = clean[i:min(i+8, len(clean))]
            has_label  = any(label_re.search(l) for l in window)
            has_field  = any(field_re.search(l) for l in window)
            if has_label and has_field:
                end    = i + len(window)
                block  = lines[i:end]
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "form_field",
                    name   = "FormField",
                    reason = f"label + input group ({len(block)} lines) -- extract as <FormField label='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_status_badges(lines, clean) -> list[Candidate]:
    """Small inline status/label indicators."""
    candidates = []
    badge_re   = re.compile(
        r'status|badge|pill|chip|tag|label.*color|rounded.*small|'
        r'rounded-full|text-xs.*bg-|bg-.*text-xs',
        re.IGNORECASE
    )
    i = 0
    while i < len(clean):
        line = clean[i]
        if re.search(r'<span|<div|<p', line) and badge_re.search(line):
            end   = find_block_end(clean, i, 8)
            block = lines[i:end]
            if len(block) <= 6:
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "status_badge",
                    name   = "StatusBadge",
                    reason = f"inline status/label indicator ({len(block)} lines) -- extract as <StatusBadge status='...' />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_pending_items(lines, clean) -> list[Candidate]:
    """Optimistic/pending state UI for uploads or mutations."""
    candidates = []
    pending_re = re.compile(r'pending|optimistic|p\.tempId|tempId|p\.uploading|isLoading.*item', re.IGNORECASE)
    i = 0
    while i < len(clean):
        if pending_re.search(clean[i]):
            end   = find_block_end(clean, i, 40)
            block = lines[i:end]
            if len(block) >= 5:
                tokens = extract_design_tokens(block)
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "pending_item",
                    name   = "PendingItem",
                    reason = f"pending/optimistic state UI ({len(block)} lines) -- extract as <PendingItem uploading={{}} error={{}} />",
                    tokens = tokens,
                    code   = block,
                ))
                i = end
                continue
        i += 1
    return candidates


def detect_large_sections(lines, clean) -> list[Candidate]:
    """Large self-contained JSX subtrees not caught by other detectors."""
    candidates = []
    section_re = re.compile(r'^\s{0,8}<(div|section|article|aside|ul|ol)\b')
    i = 0
    while i < len(clean):
        m = section_re.match(clean[i])
        if m:
            end  = find_block_end(clean, i, 100)
            span = end - i
            if span >= 30:
                block  = lines[i:end]
                tokens = extract_design_tokens(block)
                # Name from className or id
                class_m = re.search(r'className=["\']([^"\']+)["\']', lines[i])
                id_m    = re.search(r'\bid=["\']([^"\']+)["\']', lines[i])
                if class_m:
                    raw  = class_m.group(1).split()[0]
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                elif id_m:
                    raw  = id_m.group(1)
                    name = ''.join(w.capitalize() for w in re.split(r'[-_]', raw))
                else:
                    name = m.group(1).capitalize() + "Section"
                candidates.append(Candidate(
                    start  = i + 1,
                    end    = end,
                    kind   = "section_block",
                    name   = name,
                    reason = f"<{m.group(1)}> block spanning {span} lines -- large enough to be its own section component",
                    tokens = tokens,
                    code   = block,
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
        if overlap < 4:
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

    section(f"REUSABLE DESIGN COMPONENTS -- {len(candidates)} candidates in {file_rel}")
    out()
    out("  Design blocks that COULD be extracted into reusable components.")
    out("  No files have been changed.")
    out()

    for i, c in enumerate(candidates, 1):
        span    = c.end - c.start + 1
        summary = format_design_summary(c.tokens)
        out(f"  ┌─ [{i}] <{c.name} />")
        out(f"  │   Lines:   {c.start}–{c.end}  ({span} lines)")
        out(f"  │   Kind:    {KIND.get(c.kind, c.kind)}")
        out(f"  │   Reason:  {c.reason}")
        out(f"  │   Design:  {summary}")
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
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"design-extracts-{file_path.stem}-{ts}.txt"

    lines_out = []
    lines_out.append("DESIGN COMPONENT EXTRACTS")
    lines_out.append(f"Source:     {rel(str(file_path), root)}")
    lines_out.append(f"Generated:  {datetime.now()}")
    lines_out.append(f"Candidates: {len(candidates)}")
    lines_out.append("=" * 66)
    lines_out.append("")

    for i, c in enumerate(candidates, 1):
        summary = format_design_summary(c.tokens)
        lines_out.append(f"[{i}] <{c.name} />")
        lines_out.append(f"    Lines:   {c.start}–{c.end}")
        lines_out.append(f"    Kind:    {KIND.get(c.kind, c.kind)}")
        lines_out.append(f"    Reason:  {c.reason}")
        lines_out.append(f"    Design:  {summary}")
        lines_out.append("")
        lines_out.append("    ── Extracted Code " + "─" * 48)
        lines_out.append("")
        for line in c.code:
            lines_out.append("    " + line)
        lines_out.append("")
        lines_out.append("    ── Suggested Component Shell " + "─" * 38)
        lines_out.append("")
        lines_out.append(f"    // components/design/{c.name}.tsx")
        lines_out.append(f"    import React from 'react'")
        lines_out.append(f"")
        lines_out.append(f"    type {c.name}Props = {{")
        lines_out.append(f"      children?: React.ReactNode")
        lines_out.append(f"      className?: string")
        lines_out.append(f"      // TODO: add props for each hardcoded value above")
        lines_out.append(f"    }}")
        lines_out.append(f"")
        lines_out.append(f"    export function {c.name}({{ children, className }}: {c.name}Props) {{")
        lines_out.append(f"      return (")
        lines_out.append(f"        // paste extracted code here")
        lines_out.append(f"        // replace hardcoded values with props")
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
        description="Find design blocks that could become reusable design components."
    )
    parser.add_argument("--root", default=".", help="Repo root (default: current directory)")
    parser.add_argument("--file", default=None,  help="File to scan (prompts if not provided)")
    parser.add_argument("--out",  default=None,  help="Directory for .txt output (default: repo root)")
    args = parser.parse_args()

    root = Path(args.root).resolve()

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
        print(f"\n  [ERROR] Could not read: {target}")
        sys.exit(1)

    out(f"  File:   {rel(str(target), root)}")
    out(f"  Lines:  {len(lines)}")
    out()
    out("  Running detectors...")
    out()

    clean = strip_comments(lines)

    candidates: list[Candidate] = []
    candidates += detect_chat_bubbles(lines, clean)
    candidates += detect_input_bars(lines, clean)
    candidates += detect_date_dividers(lines, clean)
    candidates += detect_error_banners(lines, clean)
    candidates += detect_upload_overlays(lines, clean)
    candidates += detect_empty_states(lines, clean)
    candidates += detect_map_items(lines, clean)
    candidates += detect_modal_shells(lines, clean)
    candidates += detect_cards(lines, clean)
    candidates += detect_button_groups(lines, clean)
    candidates += detect_form_fields(lines, clean)
    candidates += detect_status_badges(lines, clean)
    candidates += detect_pending_items(lines, clean)
    candidates += detect_large_sections(lines, clean)

    candidates = deduplicate(candidates)
    candidates = sorted(candidates, key=lambda c: c.start)

    print_report(candidates, target, root)

    if candidates:
        txt_path = write_extracts(candidates, target, out_dir, root)
        out(f"  Extracts saved → {txt_path}")
        out()

    out(f"  Run: python scripts/audits/find_design_components.py --root {root} --file {target.relative_to(root)}")
    out()


def read_file(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None


if __name__ == "__main__":
    main()