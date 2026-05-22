#!/usr/bin/env python3
"""
rename_css_imports.py
─────────────────────
Finds every import/require that references a .css file and updates it to .scss.
Also renames any .css files that haven't been renamed yet.

Two-pass operation:
  1. Rename .css files → .scss (skips files already named .scss)
  2. Update all import/require references in .ts/.tsx/.js/.jsx/.scss files

Usage:
  python scripts/rename_css_imports.py [--root PATH] [--dry-run] [--imports-only]

  --root          Repo root (default: current directory)
  --dry-run       Show what would change without writing
  --imports-only  Only update import references, do not rename files

Exit code:
  0 = done
  1 = errors encountered
"""

import argparse
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__"}

SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".scss", ".css"}

# Patterns that reference CSS files in imports
IMPORT_PATTERNS = [
    # import './foo.css'  or  import "../bar.css"
    r"""(import\s+(?:[\w\s{},*]+from\s+)?['"])([^'"]+\.css)(['"])""",
    # require('./foo.css')
    r"""(require\s*\(\s*['"])([^'"]+\.css)(['"\s]*\))""",
    # @import 'foo.css'  or  @import "foo.css"
    r"""(@import\s+['"])([^'"]+\.css)(['"])""",
    # @use 'foo.css'
    r"""(@use\s+['"])([^'"]+\.css)(['"])""",
]

_BACKUP_DIR: Path | None = None


def out(msg: str, indent: int = 0) -> None:
    print("  " * indent + msg, flush=True)

def section(title: str) -> None:
    print(f"\n{'─'*66}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'─'*66}", flush=True)

def header(title: str, extra: str = "") -> None:
    print(f"\n{'═'*66}", flush=True)
    print(f"  {title}", flush=True)
    if extra: print(f"  {extra}", flush=True)
    print(f"{'═'*66}", flush=True)


def walk(root: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        f for f in root.rglob("*")
        if f.is_file()
        and f.suffix in extensions
        and not any(s in f.parts for s in SKIP_DIRS)
    )


def read(f: Path) -> str:
    return f.read_text(encoding="utf-8", errors="replace")


def write(f: Path, text: str) -> None:
    f.write_text(text, encoding="utf-8")


def backup(f: Path) -> None:
    if _BACKUP_DIR and f.exists():
        dest_name = f"{f.parent.name}__{f.name}"
        dest = _BACKUP_DIR / dest_name
        (_BACKUP_DIR / (dest_name + ".origin")).write_text(str(f), encoding="utf-8")
        shutil.copy2(f, dest)


# ── Pass 1: Rename .css files → .scss ────────────────────────────────────────

def rename_files(root: Path, dry_run: bool) -> dict[str, str]:
    """
    Rename all .css files to .scss.
    Returns mapping of old_name → new_name for use in import updates.
    """
    css_files = [
        f for f in root.rglob("*.css")
        if not any(s in f.parts for s in SKIP_DIRS)
    ]

    renamed: dict[str, str] = {}  # old path str → new path str

    if not css_files:
        out("✓ No .css files found — all already .scss")
        return renamed

    out(f"Found {len(css_files)} .css file(s) to rename\n")

    for f in css_files:
        new_f = f.with_suffix(".scss")
        rel   = f.relative_to(root)
        out(f"  {'~' if dry_run else '→'} {rel}  →  {new_f.name}")
        renamed[str(f)] = str(new_f)
        if not dry_run:
            backup(f)
            f.rename(new_f)

    out(f"\n  {'Would rename' if dry_run else 'Renamed'} {len(renamed)} file(s)")
    return renamed


# ── Pass 2: Update import references ─────────────────────────────────────────

def update_imports(root: Path, dry_run: bool) -> int:
    """
    Find every import/require/use of a .css file and update to .scss.
    Returns count of files changed.
    """
    source_files = walk(root, SOURCE_EXTENSIONS)
    compiled = [re.compile(p) for p in IMPORT_PATTERNS]

    changed = 0
    total_replacements = 0

    for f in source_files:
        text = read(f)
        new_text = text
        file_changes = []

        for pattern in compiled:
            def replace_css(m):
                prefix    = m.group(1)
                css_path  = m.group(2)
                suffix    = m.group(3)
                scss_path = css_path[:-4] + ".scss"  # replace .css with .scss
                file_changes.append((css_path, scss_path))
                return prefix + scss_path + suffix

            new_text = pattern.sub(replace_css, new_text)

        if file_changes:
            rel = f.relative_to(root)
            out(f"\n  📄 {rel}  ({len(file_changes)} import(s) updated)")
            for old, new in file_changes:
                out(f"     {old}  →  {new}", 1)

            if not dry_run:
                backup(f)
                write(f, new_text)
                out(f"     ✓ Written")

            changed += 1
            total_replacements += len(file_changes)

    return changed, total_replacements


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _BACKUP_DIR

    parser = argparse.ArgumentParser(
        description="Rename .css files to .scss and update all import references."
    )
    parser.add_argument("--root",         default=".", help="Repo root (default: .)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Show changes without writing")
    parser.add_argument("--imports-only", action="store_true",
                        help="Only update import references, do not rename files")
    args = parser.parse_args()

    root    = Path(args.root).resolve()
    dry_run = args.dry_run

    # Create backup dir
    if not dry_run:
        ts          = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        skill_root  = Path(__file__).parent.parent.parent
        _BACKUP_DIR = skill_root / "data" / "fixes" / "rename" / ts
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    header(
        f"RENAME CSS → SCSS {'(DRY RUN)' if dry_run else ''}",
        f"Root: {root}"
    )

    errors = 0

    # Pass 1 — rename files
    if not args.imports_only:
        section("PASS 1 — Renaming .css files → .scss")
        try:
            rename_files(root, dry_run)
        except Exception as e:
            out(f"[ERROR] {e}")
            errors += 1

    # Pass 2 — update imports
    section("PASS 2 — Updating import references")
    try:
        files_changed, replacements = update_imports(root, dry_run)
        out(f"\n  {'Would update' if dry_run else 'Updated'} {replacements} import(s) across {files_changed} file(s)")
    except Exception as e:
        out(f"[ERROR] {e}")
        errors += 1

    header("DONE")
    if dry_run:
        out("DRY RUN — no files written.")
    else:
        if _BACKUP_DIR and any(_BACKUP_DIR.iterdir()):
            out(f"Backups: {_BACKUP_DIR}")
        else:
            if _BACKUP_DIR:
                _BACKUP_DIR.rmdir()  # clean up empty backup dir

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
