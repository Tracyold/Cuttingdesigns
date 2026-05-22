#!/usr/bin/env python3
"""
fix_color_refs.py

Reads a saved audit file from check_colors.py and fixes every bare $varname
reference listed in Report 5 by prepending the color prefix so it becomes
$color-varname (or whatever prefix the audit recorded).

Prompts for:
  1. Path to the audits folder  (scripts/data/audits/color-match)
     Lists every .txt file inside and asks which one to use.
  2. Path to the locs directory  (src/locs)
     Where the actual .scss files that need fixing live.
  3. Mode — dry-run / fix / restore

Modes:
  dry-run   Show every replacement that would be made. Nothing is written.
  fix       Back up every affected file then apply all replacements.
  restore   Pick a previous backup and revert files to their saved state.
"""

import re
import shutil
from pathlib import Path
from datetime import datetime
from io import StringIO


BACKUP_ROOT = Path.cwd() / "scripts" / "data" / "backups" / "fix-color-refs"
LOG_DIR     = Path.cwd() / "scripts" / "data" / "audits"  / "fix-color-refs"


# ─────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────

def prompt_dir(label: str) -> Path:
    while True:
        raw = input(f"\n{label}: ").strip().strip('"').strip("'")
        p   = Path(raw)
        if p.is_dir():
            return p
        print(f"  x  Directory not found: {raw}")


def prompt_choice(prompt: str, options: list) -> str:
    while True:
        print(f"\n{prompt}")
        for i, opt in enumerate(options, 1):
            print(f"  {i}.  {opt}")
        raw = input("\n  Enter number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  Please enter a number from the list.")


def pick_audit_file(audit_dir: Path) -> Path:
    """List every .txt file in the audit dir and let the user pick one."""
    files = sorted(audit_dir.glob("*.txt"), reverse=True)
    if not files:
        print(f"\n  x  No audit files found in {audit_dir}")
        raise SystemExit(1)
    names  = [f.name for f in files]
    chosen = prompt_choice("Which audit file do you want to use?", names)
    return audit_dir / chosen


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# Parse the audit file
# ─────────────────────────────────────────────────────────────

def parse_audit(text: str) -> tuple:
    """
    Pull two things from the audit file:

      prefix  — from the header line:
                    Color prefix     :  $color-
                → "color"

      bare    — every entry from Report 5:
                    button.scss
                      line   12  $card-background   ...
                → {"button.scss": [(12, "card-background"), ...]}
    """
    # ── prefix ───────────────────────────────────────────────
    prefix = None
    m = re.search(r"Color prefix\s*:\s*\$([a-zA-Z0-9_-]+)-", text, re.IGNORECASE)
    if m:
        prefix = m.group(1)

    # ── Report 5 section ─────────────────────────────────────
    section_re = re.compile(
        r"5\s*[—–-]\s*BARE \$VARIABLE REFS.*?\n"
        r"[=\u2550]+\n"
        r"(.*?)"
        r"(?=[=\u2550]{10}|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    sm = section_re.search(text)
    if not sm:
        return prefix, {}

    section_text = sm.group(1)

    filename_re = re.compile(r"^  ([a-zA-Z0-9_\-\.]+\.scss)\s*$", re.MULTILINE)
    entry_re    = re.compile(r"^\s+line\s+(\d+)\s+(\$[a-zA-Z0-9_-]+)", re.MULTILINE)

    bare     = {}
    sections = list(filename_re.finditer(section_text))

    for i, fm in enumerate(sections):
        fname = fm.group(1)
        start = fm.end()
        end   = sections[i + 1].start() if i + 1 < len(sections) else len(section_text)
        block = section_text[start:end]

        entries = [
            (int(em.group(1)), em.group(2).lstrip("$"))
            for em in entry_re.finditer(block)
        ]
        if entries:
            bare[fname] = entries

    return prefix, bare


# ─────────────────────────────────────────────────────────────
# Build and apply replacements
# ─────────────────────────────────────────────────────────────

def build_replacements(file_path: Path, entries: list, prefix: str) -> list:
    """
    Check each recorded (line_num, varname) against the current file content.
    Returns replacement records only for lines where the bare ref still exists.
    Skips anything already fixed.
    """
    lines = file_path.read_text(encoding="utf-8").splitlines()
    reps  = []

    for line_num, varname in entries:
        idx = line_num - 1
        if idx >= len(lines):
            continue
        line    = lines[idx]
        pattern = re.compile(
            r"(?<![a-zA-Z0-9_-])\$" + re.escape(varname) + r"(?![a-zA-Z0-9_-])"
        )
        if not pattern.search(line):
            continue
        reps.append({
            "line_num":  line_num,
            "old_ref":   f"${varname}",
            "new_ref":   f"${prefix}-{varname}",
            "line_text": line.strip(),
        })

    return reps


def apply_to_file(file_path: Path, entries: list, prefix: str):
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)

    for line_num, varname in entries:
        idx = line_num - 1
        if idx >= len(lines):
            continue
        pattern    = re.compile(
            r"(?<![a-zA-Z0-9_-])\$" + re.escape(varname) + r"(?![a-zA-Z0-9_-])"
        )
        lines[idx] = pattern.sub(f"${prefix}-{varname}", lines[idx])

    file_path.write_text("".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# Backup and restore
# ─────────────────────────────────────────────────────────────

def make_backup_dir() -> Path:
    d = BACKUP_ROOT / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_backups(file_paths: list, backup_dir: Path) -> dict:
    mapping = {}
    for path in file_paths:
        dest = backup_dir / path.name
        if dest.exists():
            dest = backup_dir / (path.stem + "_" + str(abs(hash(str(path))))[-6:] + path.suffix)
        shutil.copy2(path, dest)
        mapping[str(path)] = dest
    return mapping


def write_manifest(mapping: dict, backup_dir: Path):
    lines = ["# fix_color_refs backup manifest", f"# created: {datetime.now()}", ""]
    for original, backed_up in mapping.items():
        lines.append(f"{backed_up.name}  <-  {original}")
    (backup_dir / "manifest.txt").write_text("\n".join(lines), encoding="utf-8")


def list_backups() -> list:
    if not BACKUP_ROOT.exists():
        return []
    return sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()], reverse=True)


def read_manifest(backup_dir: Path) -> dict:
    manifest = backup_dir / "manifest.txt"
    if not manifest.exists():
        return {}
    mapping = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("  <-  ", 1)
        if len(parts) == 2:
            mapping[parts[0].strip()] = parts[1].strip()
    return mapping


# ─────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────

class Tee:
    def __init__(self):
        self.buf = StringIO()

    def write(self, text: str):
        print(text, end="")
        self.buf.write(text)

    def line(self, text: str = ""):
        self.write(text + "\n")

    def section(self, title: str):
        self.line()
        self.line("=" * 68)
        self.line(f"  {title}")
        self.line("=" * 68)

    def get(self) -> str:
        return self.buf.getvalue()


def make_log_path(mode: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return LOG_DIR / f"fix-color-refs_{mode}_{ts}.txt"


# ─────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────

def mode_dry_run(out, bare, prefix, locs_dir):
    out.section("DRY RUN — no files will be changed")
    out.line()

    total_files   = 0
    total_changes = 0

    for fname, entries in sorted(bare.items()):
        file_path = locs_dir / fname
        if not file_path.exists():
            out.line(f"  x  {fname}  — file not found in {locs_dir}")
            continue

        reps = build_replacements(file_path, entries, prefix)
        if not reps:
            out.line(f"  {fname}  — nothing to fix (already correct)")
            continue

        total_files   += 1
        total_changes += len(reps)

        out.line(f"  {fname}  ({len(reps)} change{'s' if len(reps) != 1 else ''})")
        for r in reps:
            out.line(f"    line {r['line_num']:>4}  {r['old_ref']:<30}  ->  {r['new_ref']}")
            out.line(f"           {r['line_text'][:72]}")
        out.line()

    if total_changes == 0:
        out.line("  Nothing to fix — all color references already use the correct prefix.")
    else:
        out.line(f"  Would change  {total_changes} reference{'s' if total_changes != 1 else ''}"
                 f"  across  {total_files} file{'s' if total_files != 1 else ''}.")
        out.line()
        out.line("  Run again and choose 'fix' to apply these changes.")


def mode_fix(out, bare, prefix, locs_dir):
    out.section("FIX — preparing changes")
    out.line()

    files_to_fix = []
    all_reps     = {}

    for fname, entries in sorted(bare.items()):
        file_path = locs_dir / fname
        if not file_path.exists():
            out.line(f"  x  {fname}  — file not found in {locs_dir}, skipping")
            continue
        reps = build_replacements(file_path, entries, prefix)
        if reps:
            files_to_fix.append(file_path)
            all_reps[file_path] = (entries, reps)

    if not files_to_fix:
        out.line("  Nothing to fix — all color references already use the correct prefix.")
        return

    total_changes = sum(len(v[1]) for v in all_reps.values())
    out.line(f"  {total_changes} change{'s' if total_changes != 1 else ''} "
             f"across {len(files_to_fix)} file{'s' if len(files_to_fix) != 1 else ''}.")

    # Write backups before touching anything
    backup_dir = make_backup_dir()
    out.line(f"\n  Writing backups to:  {backup_dir}")
    try:
        mapping = write_backups(files_to_fix, backup_dir)
        write_manifest(mapping, backup_dir)
    except Exception as e:
        out.line(f"\n  ABORT — backup failed: {e}")
        out.line("  No source files were modified.")
        return

    out.line(f"  Backup complete — {len(mapping)} file{'s' if len(mapping) != 1 else ''} saved.")

    # Apply changes
    out.section("APPLYING CHANGES")
    out.line()
    for file_path in files_to_fix:
        entries, reps = all_reps[file_path]
        apply_to_file(file_path, entries, prefix)
        out.line(f"  {file_path.name}  ({len(reps)} change{'s' if len(reps) != 1 else ''})")
        for r in reps:
            out.line(f"    line {r['line_num']:>4}  {r['old_ref']:<30}  ->  {r['new_ref']}")

    out.line()
    out.line(f"  Done.  Backup stored at:")
    out.line(f"  {backup_dir}")
    out.line()
    out.line("  Run again and choose 'restore' if you need to undo these changes.")


def mode_restore(out):
    out.section("RESTORE — revert files from a backup")
    out.line()

    backups = list_backups()
    if not backups:
        out.line(f"  No backups found under {BACKUP_ROOT}")
        return

    chosen_name = prompt_choice(
        "Choose a backup to restore from:",
        [d.name for d in backups],
    )
    backup_dir = BACKUP_ROOT / chosen_name
    mapping    = read_manifest(backup_dir)

    if not mapping:
        out.line(f"  No manifest found in {backup_dir} — cannot restore.")
        return

    out.line(f"\n  Backup  :  {backup_dir}")
    out.line(f"  Files   :  {len(mapping)}")
    out.line()
    for backed_up_name, original_path in mapping.items():
        out.line(f"  {backed_up_name:<40}  ->  {original_path}")

    confirm = input("\n  Restore these files? (yes / no): ").strip().lower()
    if confirm not in ("yes", "y"):
        out.line("\n  Restore cancelled.")
        return

    out.line()
    errors = []
    for backed_up_name, original_path in mapping.items():
        src  = backup_dir / backed_up_name
        dest = Path(original_path)
        if not src.exists():
            errors.append(f"  MISSING  {src}")
            continue
        try:
            shutil.copy2(src, dest)
            out.line(f"  Restored  {dest}")
        except Exception as e:
            errors.append(f"  FAILED  {dest}: {e}")

    if errors:
        out.line()
        out.line("  Errors during restore:")
        for err in errors:
            out.line(err)
    else:
        out.line()
        out.line(f"  All {len(mapping)} file{'s' if len(mapping) != 1 else ''} restored successfully.")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def run():
    out = Tee()

    out.line()
    out.line("╔══════════════════════════════════════════════════╗")
    out.line("║       fix color variable references              ║")
    out.line("╚══════════════════════════════════════════════════╝")

    mode = prompt_choice("Choose a mode:", ["dry-run", "fix", "restore"])

    if mode == "restore":
        mode_restore(out)
        log_path = make_log_path("restore")
        log_path.write_text(out.get(), encoding="utf-8")
        print(f"\n  Log saved -> {log_path}\n")
        return

    audit_dir  = prompt_dir("1. Path to the color-match audits folder"
                            "  (e.g. scripts/data/audits/color-match)")
    audit_file = pick_audit_file(audit_dir)
    locs_dir   = prompt_dir("2. Path to the locs directory  (e.g. src/locs)")

    audit_text = audit_file.read_text(encoding="utf-8")
    prefix, bare = parse_audit(audit_text)

    if not prefix:
        out.line("\n  x  Could not find the color prefix in the audit file.")
        out.line("     Make sure this is a file produced by check_colors.py.")
        raise SystemExit(1)

    if not bare:
        out.line("\n  Report 5 in the audit file contains no bare variable references.")
        out.line("  Nothing to fix.")
        raise SystemExit(0)

    out.line()
    out.line(f"  Mode         :  {mode}")
    out.line(f"  Audit file   :  {audit_file.name}")
    out.line(f"  Prefix       :  ${prefix}-")
    out.line(f"  Locs dir     :  {locs_dir}")
    out.line(f"  Files to fix :  {len(bare)}")

    if mode == "dry-run":
        mode_dry_run(out, bare, prefix, locs_dir)
    elif mode == "fix":
        mode_fix(out, bare, prefix, locs_dir)

    log_path = make_log_path(mode)
    log_path.write_text(out.get(), encoding="utf-8")
    print(f"\n  Log saved -> {log_path}\n")


if __name__ == "__main__":
    run()