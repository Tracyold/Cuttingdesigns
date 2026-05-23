#!/usr/bin/env python3
"""
fix_duplicates.py

Finds duplicate raw value groups in .scss files and fixes them by:
  1. Backing up every file that will be modified
  2. Generating a $name for each duplicate raw group
  3. Inserting the declaration at the top of the file
  4. Replacing all occurrences with the $name

Run normally:
  python3 fix_duplicates.py

Restore a previous backup:
  python3 fix_duplicates.py --restore
"""

import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime
from io import StringIO
from colorsys import rgb_to_hsv


# ── Skip list ────────────────────────────────────────────────────────
SKIP_FILES = {"colors.scss"}

# ── Backup root ──────────────────────────────────────────────────────
BACKUP_ROOT = Path("scripts") / "data" / "backups" / "fix-duplicates"


# ── Color classification ──────────────────────────────────────────────

def hex_to_rgb(hex_str):
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return None


def classify_color_rgb(r, g, b, a=1.0):
    if a < 0.15:
        return 'trn'
    rn, gn, bn = r/255, g/255, b/255
    h, s, v = rgb_to_hsv(rn, gn, bn)
    h_deg = h * 360
    if v < 0.15:
        return 'blk'
    if v > 0.85 and s < 0.15:
        return 'wht'
    if s < 0.15:
        return 'gry'
    if h_deg < 15 or h_deg >= 345:  return 'red'
    elif h_deg < 45:                 return 'orn'
    elif h_deg < 75:                 return 'yel'
    elif h_deg < 150:                return 'grn'
    elif h_deg < 195:                return 'cyn'
    elif h_deg < 255:                return 'blu'
    elif h_deg < 285:                return 'pur'
    elif h_deg < 345:                return 'pnk'
    return 'gry'


def classify_hex(hex_str):
    rgb = hex_to_rgb(hex_str)
    if rgb is None:
        return 'col'
    return classify_color_rgb(*rgb)


def classify_rgba(rgba_str):
    m = re.match(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)', rgba_str)
    if not m:
        return 'col'
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = float(m.group(4)) if m.group(4) else 1.0
    return classify_color_rgb(r, g, b, a)


# ── Number words ─────────────────────────────────────────────────────

WORDS = ['one','two','three','four','five','six','seven','eight',
         'nine','ten','eleven','twelve','thirteen','fourteen','fifteen',
         'sixteen','seventeen','eighteen','nineteen','twenty']

def num_word(n):
    if 1 <= n <= len(WORDS):
        return WORDS[n-1]
    return str(n)


# ── Name generation ──────────────────────────────────────────────────

def generate_name(raw_group, filename_stem, existing_names):
    raw = raw_group.strip()

    if re.match(r'^#[0-9a-fA-F]{3,8}$', raw):
        base = classify_hex(raw)
    elif re.match(r'^rgba?\(', raw):
        base = classify_rgba(raw)
    elif re.match(r'^[a-zA-Z]', raw):
        base = re.sub(r'[^a-zA-Z]', '', raw)[:3].lower()
    else:
        base = filename_stem[:3].lower()

    stem = f'$_{base}'
    if stem not in existing_names:
        return stem

    for i in range(1, 100):
        candidate = f'$_{base}-{num_word(i)}'
        if candidate not in existing_names:
            return candidate

    return f'$_{base}-x'


# ── Value extraction ─────────────────────────────────────────────────

DECL_LINE = re.compile(r'^\s*(\$[a-zA-Z0-9_-]+)\s*:(.*?)(?:;|$)', re.DOTALL)
VAR_REF   = re.compile(r'\$[a-zA-Z0-9_-]+')
COMMENT   = re.compile(r'//.*$')


def extract_raw_groups(value_str):
    value_str = COMMENT.sub('', value_str).strip().rstrip(';').strip()
    if not value_str:
        return []
    segments = re.split(r',\s*(?=\$)', value_str)
    raw_groups = []
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        cleaned = VAR_REF.sub('', segment).strip()
        cleaned = re.sub(r'^[,\s]+', '', cleaned)
        cleaned = re.sub(r'[,\s]+$', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if cleaned:
            raw_groups.append(cleaned)
    return raw_groups


def find_duplicates(lines):
    group_map = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('*'):
            i += 1
            continue
        m = DECL_LINE.match(line)
        if not m:
            i += 1
            continue
        var_name  = m.group(1)
        value_str = m.group(2)
        while not re.search(r';', lines[i]) and i + 1 < len(lines):
            i += 1
            ns = lines[i].strip()
            if ns.startswith('//') or ns.startswith('$'):
                break
            value_str += ' ' + lines[i].strip()
        for group in extract_raw_groups(value_str):
            normalized = re.sub(r'\s+', ' ', group).strip().lower()
            if not normalized:
                continue
            group_map.setdefault(normalized, {'display': group, 'occurrences': []})
            group_map[normalized]['occurrences'].append((var_name, i))
        i += 1
    return {k: v for k, v in group_map.items() if len(v['occurrences']) > 1}


def find_insertion_point(lines):
    in_block_comment = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('/*'):
            in_block_comment = True
        if in_block_comment and '*/' in s:
            in_block_comment = False
            continue
        if in_block_comment:
            continue
        if s.startswith('//'):
            continue
        return i
    return len(lines)


def fix_file(text, filename_stem):
    lines = list(text.splitlines(keepends=True))
    stripped_lines = [l.rstrip('\n') for l in lines]
    duplicates = find_duplicates(stripped_lines)
    if not duplicates:
        return text, []

    existing_names = set(re.findall(r'\$[a-zA-Z0-9_-]+', text))
    changes = []
    new_declarations = []
    replacements = {}

    for norm, data in sorted(duplicates.items()):
        raw = data['display']
        name = generate_name(raw, filename_stem, existing_names)
        existing_names.add(name)
        replacements[raw] = name
        new_declarations.append(f'{name}:  {raw};')
        changes.append({
            'name': name,
            'raw': raw,
            'vars': [occ[0] for occ in data['occurrences']]
        })

    insert_at = find_insertion_point(stripped_lines)

    decl_block = '\n// ── auto-named raw values ──────────────────────────────────────\n'
    for decl in new_declarations:
        decl_block += decl + '\n'
    decl_block += '\n'

    new_lines = []
    for i, line in enumerate(stripped_lines):
        if i == insert_at:
            new_lines.append(decl_block)
        m = DECL_LINE.match(line)
        if m:
            prefix     = line[:m.start(2)]
            value_part = m.group(2)
            suffix_match = re.search(r';.*$', line[m.start(2):])
            suffix = suffix_match.group(0) if suffix_match else ''
            for raw, name in sorted(replacements.items(), key=lambda x: -len(x[0])):
                escaped = re.escape(raw)
                value_part = re.sub(
                    r'(?<![a-zA-Z0-9_$-])' + escaped + r'(?![a-zA-Z0-9_-])',
                    name,
                    value_part
                )
            new_lines.append(prefix + value_part + suffix)
        else:
            new_lines.append(line)

    return '\n'.join(new_lines), changes


# ── Backup & restore ─────────────────────────────────────────────────

def create_backup(files_to_backup, timestamp):
    backup_dir = BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in files_to_backup:
        dest = backup_dir / f.name
        shutil.copy2(f, dest)
    return backup_dir


def restore_mode():
    print('\n  ┌─────────────────────────────────────────┐')
    print('  │           RESTORE BACKUP                │')
    print('  └─────────────────────────────────────────┘\n')

    if not BACKUP_ROOT.exists():
        print(f'  No backups found at {BACKUP_ROOT}')
        sys.exit(0)

    backups = sorted(BACKUP_ROOT.iterdir(), reverse=True)
    if not backups:
        print('  No backups found.')
        sys.exit(0)

    print('  Available backups:\n')
    for i, b in enumerate(backups):
        files = list(b.glob('*.scss'))
        print(f'  [{i+1}]  {b.name}  ({len(files)} files)')

    print()
    choice = input('  Enter number to restore (or q to quit): ').strip()
    if choice.lower() == 'q':
        sys.exit(0)

    try:
        idx = int(choice) - 1
        backup_dir = backups[idx]
    except (ValueError, IndexError):
        print('  Invalid choice.')
        sys.exit(1)

    target = input('\n  Restore to which directory? (e.g. src/styles/decs): ').strip()
    target_path = Path(target)
    if not target_path.is_dir():
        print(f'  Directory not found: {target}')
        sys.exit(1)

    restored = 0
    for f in backup_dir.glob('*.scss'):
        dest = target_path / f.name
        shutil.copy2(f, dest)
        print(f'  ✓  restored {f.name}')
        restored += 1

    print(f'\n  ✓ Restored {restored} files from {backup_dir.name}\n')


# ── Helpers ──────────────────────────────────────────────────────────

def prompt_dir(label):
    while True:
        path = input(f"\n{label}: ").strip().strip('"').strip("'")
        if os.path.isdir(path):
            return Path(path)
        print(f"  ✗ Directory not found: {path}")


def scss_files(directory):
    return sorted(directory.rglob("*.scss"))


def make_report_path():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"fix-duplicates_{timestamp}.txt"
    audit_dir = Path.cwd() / "scripts" / "data" / "audits" / "duplicates"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / filename


class Tee:
    def __init__(self):
        self.buf = StringIO()
    def write(self, text):
        print(text, end="")
        self.buf.write(text)
    def line(self, text=""):
        self.write(text + "\n")
    def section(self, title):
        self.line()
        self.line("═" * 70)
        self.line(f"  {title}")
        self.line("═" * 70)
    def get(self):
        return self.buf.getvalue()


# ── Run ───────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════════════╗")
    out.line("║         duplicate raw value auto-fixer           ║")
    out.line("╚══════════════════════════════════════════════════╝")
    out.line()
    out.line("  Run with --restore to restore a previous backup.")
    out.line(f"  Skipping: {', '.join(SKIP_FILES)}")

    target_dir = prompt_dir("\nDirectory to fix (e.g. src/styles/decs)")

    dry_run_input = input("\n  Dry run? Changes shown but NOT written (y/n): ").strip().lower()
    dry_run = dry_run_input != 'n'

    if dry_run:
        out.line("\n  ── DRY RUN — no files will be modified ──")
    else:
        out.line("\n  ── LIVE RUN — files will be modified ──")

    files = scss_files(target_dir)
    if not files:
        out.line(f"\n  No .scss files found in {target_dir}")
        sys.exit(0)

    # ── Identify files that need fixing ──────────────────────────────
    files_to_fix = []
    for f in files:
        if f.name in SKIP_FILES:
            continue
        text = f.read_text(encoding='utf-8')
        lines = [l.rstrip('\n') for l in text.splitlines(keepends=True)]
        dups = find_duplicates(lines)
        if dups:
            files_to_fix.append(f)

    # ── Backup before touching anything ──────────────────────────────
    backup_dir = None
    if not dry_run and files_to_fix:
        timestamp  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = create_backup(files_to_fix, timestamp)
        out.line(f"\n  ✓ Backup created → {backup_dir}")
        out.line(f"    Run with --restore to undo all changes.\n")

    # ── Process files ─────────────────────────────────────────────────
    out.line()
    out.line(f"  Processing {len(files)} files...\n")

    total_fixes = 0
    files_fixed = 0
    files_clean = 0
    all_changes = {}

    for f in files:
        if f.name in SKIP_FILES:
            print(f"  –  {f.name}  (skipped)")
            continue

        text = f.read_text(encoding='utf-8')
        new_text, changes = fix_file(text, f.stem)

        if changes:
            files_fixed += 1
            total_fixes += len(changes)
            all_changes[f.name] = changes
            print(f"  ✓  {f.name}  ({len(changes)} fix{'es' if len(changes) > 1 else ''})")
            if not dry_run:
                f.write_text(new_text, encoding='utf-8')
        else:
            files_clean += 1
            print(f"  ·  {f.name}  (clean)")

    # ── Detail ────────────────────────────────────────────────────────

    out.section("CHANGES MADE")

    if not all_changes:
        out.line("\n  ✓ No duplicates found — nothing to fix.")
    else:
        for filename, changes in sorted(all_changes.items()):
            out.line()
            out.line(f"  ── {filename} {'─' * max(1, 50 - len(filename))}")
            for change in changes:
                out.line()
                out.line(f"    {change['name']}:  {change['raw']};")
                out.line(f"    replaces raw value in:")
                for var in change['vars']:
                    out.line(f"      {var}")

    # ── Summary ───────────────────────────────────────────────────────

    out.section("SUMMARY")
    out.line(f"  Files processed  : {len(files)}")
    out.line(f"  Files fixed      : {files_fixed}")
    out.line(f"  Files clean      : {files_clean}")
    out.line(f"  Total fixes      : {total_fixes}")
    out.line(f"  Dry run          : {'yes — no files modified' if dry_run else 'no — files updated'}")
    if backup_dir:
        out.line(f"  Backup location  : {backup_dir}")
    out.line()

    report_path = make_report_path()
    report_path.write_text(out.get(), encoding='utf-8')
    print(f"\n  ✓ Report saved → {report_path}\n")


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if '--restore' in sys.argv:
        restore_mode()
    else:
        run()