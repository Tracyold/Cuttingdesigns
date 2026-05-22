#!/usr/bin/env python3
"""
audit_scss_usage.py
───────────────────
1. Finds all CSS/SCSS class names defined across component files
2. Cross-references against ALL source files for usage:
     className="..."    className={...}    clsx(...)    cn(...)
     classList.add()    dynamic strings    data-* attrs
3. Extracts unused classes → clones to unused-css-<date>.scss
4. Deletes unused classes from their original files
5. Interactive mode: pick a file, see class usage stats

Usage:
  python scripts/audits/audit_scss_usage.py [--root PATH]
  python scripts/audits/audit_scss_usage.py --interactive
  python scripts/audits/audit_scss_usage.py --scan-only

  --root         Repo root (default: current directory)
  --scan-only    Report only, do not extract or delete
  --interactive  Skip extraction, go straight to interactive file analyzer
  --dry-run      Show what would change without writing
"""

import argparse
import re
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))


DATA_DIR = Path(__file__).parent.parent.parent / "data" / "audits" / "scss-usage"

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__"}
SKIP_FILES = {"shared.scss", "shared.css", "index.scss", "index.css", "tokens.scss", "tokens.css"}
STYLE_EXTENSIONS = {".scss", ".css"}
SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".html"}


# ── File helpers ──────────────────────────────────────────────────────────────

def walk(root: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        f for f in root.rglob("*")
        if f.is_file()
        and f.suffix in extensions
        and not any(s in f.parts for s in SKIP_DIRS)
        and f.name not in SKIP_FILES
    )


def read(f: Path) -> str:
    return f.read_text(encoding="utf-8", errors="replace")


def write(f: Path, text: str) -> None:
    f.write_text(text, encoding="utf-8")


# ── Output helpers ────────────────────────────────────────────────────────────

def out(msg: str = "", indent: int = 0) -> None:
    print("  " * indent + msg, flush=True)

def bar(current: int, total: int, label: str = "") -> None:
    width = 36
    filled = int(width * current / total) if total else width
    b = "█" * filled + "░" * (width - filled)
    print(f"\r  [{b}] {current}/{total}  {label[:28]:<28}", end="", flush=True)

def section(title: str) -> None:
    print(f"\n{'─'*66}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'─'*66}", flush=True)

def header(title: str) -> None:
    print(f"\n{'═'*66}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'═'*66}", flush=True)


# ── Arrow key menu ────────────────────────────────────────────────────────────

def arrow_menu(title: str, options: list[str]) -> int:
    selected = 0

    def render():
        sys.stdout.write(f"\033[{len(options) + 2}A")
        sys.stdout.write(f"\r\033[K  {title}\n")
        for i, opt in enumerate(options):
            prefix = "  ▶ " if i == selected else "    "
            sys.stdout.write(f"\r\033[K{prefix}{opt}\n")
        sys.stdout.flush()

    print(f"\n  {title}")
    for i, opt in enumerate(options):
        prefix = "  ▶ " if i == 0 else "    "
        print(f"{prefix}{opt}")
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                c2 = sys.stdin.read(1)
                c3 = sys.stdin.read(1)
                if c2 == '[':
                    if c3 == 'A': selected = (selected - 1) % len(options); render()
                    elif c3 == 'B': selected = (selected + 1) % len(options); render()
            elif ch in ('\r', '\n'): break
            elif ch == 'q': selected = -1; break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print()
    return selected


# ── CSS/SCSS class extraction ─────────────────────────────────────────────────

def extract_defined_classes(style_files: list[Path]) -> dict[str, list[tuple[Path, int]]]:
    """
    Returns: class_name → [(file, line_number), ...]
    Only simple class selectors (.class-name), not pseudo or complex selectors.
    """
    defined: dict[str, list[tuple[Path, int]]] = defaultdict(list)

    for f in style_files:
        text = read(f)
        # Strip comments
        text_clean = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        text_clean = re.sub(r'//[^\n]*', '', text_clean)

        for m in re.finditer(r'^\s*(\.[\w][\w-]*)\s*\{', text_clean, re.MULTILINE):
            class_name = m.group(1)
            lineno = text[:m.start()].count('\n') + 1
            # Skip if it's inside an @extend or @include
            line = text_clean[m.start():m.start()+100]
            if '@extend' in line or '@include' in line:
                continue
            defined[class_name].append((f, lineno))

    return defined


def extract_class_block(text: str, class_name: str) -> str:
    """Extract the full CSS block for a class name."""
    pattern = re.compile(
        re.escape(class_name) + r'\s*\{([^}]*)\}',
        re.DOTALL
    )
    m = pattern.search(text)
    if m:
        return m.group(0)
    return ""


def remove_class_block(text: str, class_name: str) -> str:
    pattern = re.compile(
        r'\n?// [^\n]*\n' + re.escape(class_name) + r'\s*\{[^}]*\}|'
        + re.escape(class_name) + r'\s*\{[^}]*\}',
        re.DOTALL
    )
    return pattern.sub('', text).strip() + '\n'


# ── Source usage scanning ─────────────────────────────────────────────────────

def extract_used_classes(source_files: list[Path]) -> dict[str, list[tuple[Path, int]]]:
    """
    Find every class name referenced in source files.
    Returns: class_name → [(file, line_number), ...]
    Checks: className, clsx, cn, classList, dynamic strings, data-* attrs.
    """
    used: dict[str, list[tuple[Path, int]]] = defaultdict(list)

    # Patterns that yield class names
    PATTERNS = [
        # className="foo bar baz"
        r'className\s*=\s*["\']([^"\']+)["\']',
        # className={`... ${x} ...`} — grab literal parts
        r'className\s*=\s*\{`([^`]+)`\}',
        # styles.foo or styles['foo']
        r'styles\.(\w[\w-]*)',
        r"styles\[[\"'](\w[\w-]*)[\"']\]",
        # clsx(...) / cn(...) / classNames(...) — string literals inside
        r'(?:clsx|cx|cn|classNames)\s*\([^)]{0,500}\)',
        # classList.add('foo') / classList.toggle('foo')
        r'classList\.(?:add|remove|toggle|contains)\s*\(\s*["\']([^"\']+)["\']',
        # data-* attrs with class-like values
        r'data-[\w-]+\s*=\s*["\']([^"\']+)["\']',
        # Generic string literals that look like CSS classes (kebab-case / BEM)
        r'["\']([a-z][\w]*(?:[-_]{1,2}[\w]+)+)["\']',
    ]

    compiled = [re.compile(p) for p in PATTERNS]

    for f in source_files:
        text  = read(f)
        lines = text.splitlines()

        for pattern in compiled:
            for m in pattern.finditer(text):
                # Extract the matched group or full match
                content = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                lineno  = text[:m.start()].count('\n') + 1
                # Split on whitespace to get individual class names
                for cls in re.findall(r'[\w][\w-]*', content):
                    if len(cls) > 2 and re.match(r'^[a-z]', cls):
                        used[cls].append((f, lineno))
                        # Also add with dot prefix
                        used['.' + cls].append((f, lineno))

    return used


# ── Unused detection ──────────────────────────────────────────────────────────

def find_unused(
    defined: dict[str, list[tuple[Path, int]]],
    used: dict[str, list[tuple[Path, int]]],
) -> dict[str, list[tuple[Path, int]]]:
    """Return class names that are defined but never referenced in source."""
    unused = {}
    for class_name, locations in defined.items():
        # Check both with and without the leading dot
        bare = class_name.lstrip('.')
        if class_name not in used and bare not in used and '.' + bare not in used:
            unused[class_name] = locations
    return unused


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_unused(
    unused: dict[str, list[tuple[Path, int]]],
    root: Path,
    dry_run: bool,
) -> Path | None:
    """
    Clone unused classes to unused-css-<date>.scss, then delete from originals.
    Returns the path to the created file, or None if dry_run.
    """
    if not unused:
        return None

    date_str  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir  = DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"unused-css-{date_str}.scss"

    # Group by source file
    by_file: dict[Path, list[str]] = defaultdict(list)
    for class_name, locations in unused.items():
        for f, _ in locations:
            by_file[f].append(class_name)

    lines = [
        f"// unused-css-{date_str}.scss",
        f"// Classes found in source but never referenced in any component file.",
        f"// Generated by audit_scss_usage.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"// Original locations preserved as comments.",
        "",
    ]

    for f, class_names in sorted(by_file.items()):
        rel  = f.relative_to(root)
        text = read(f)
        lines.append(f"\n// ── From {rel} ────────────────────────────────")

        for class_name in sorted(set(class_names)):
            block = extract_class_block(text, class_name)
            if block:
                lines.append(block)

    if not dry_run:
        out_path.write_text('\n'.join(lines), encoding="utf-8")
        out(f"  ✓ Cloned to: {out_path.name}")

        # Now delete from originals
        for f, class_names in sorted(by_file.items()):
            text = read(f)
            for class_name in set(class_names):
                text = remove_class_block(text, class_name)
            write(f, text)
            rel = f.relative_to(root)
            out(f"  ✓ Removed {len(set(class_names))} unused class(es) from {rel}")

        return out_path
    else:
        out(f"  ~ Would clone to: {out_path.name}")
        for f, class_names in sorted(by_file.items()):
            rel = f.relative_to(root)
            out(f"  ~ Would remove {len(set(class_names))} class(es) from {rel}")
        return None


# ── Interactive file analyzer ─────────────────────────────────────────────────

def interactive_analyzer(
    style_files: list[Path],
    source_files: list[Path],
    used: dict[str, list[tuple[Path, int]]],
    root: Path,
) -> None:
    """
    Prompt: which SCSS file to analyze?
    Show: total classes, which files use each class and how many times.
    """
    while True:
        section("INTERACTIVE FILE ANALYZER")
        options  = [str(f.relative_to(root)) for f in style_files] + ["[ Exit ]"]
        idx      = arrow_menu("Select a file to analyze (↑↓ Enter, q to quit):", options)

        if idx < 0 or idx == len(options) - 1:
            out("\n  Goodbye.\n")
            break

        target = style_files[idx]
        text   = read(target)
        rel    = target.relative_to(root)

        defined_in_file = extract_defined_classes([target])
        total = len(defined_in_file)

        out()
        section(f"ANALYSIS: {rel}")
        out(f"Total class names defined: {total}\n")

        for class_name in sorted(defined_in_file.keys()):
            bare       = class_name.lstrip('.')
            references = used.get(class_name, []) + used.get(bare, []) + used.get('.' + bare, [])
            # Deduplicate by (file, line)
            unique_refs: dict[str, list[int]] = defaultdict(list)
            for src_file, lineno in references:
                unique_refs[str(src_file.relative_to(root))].append(lineno)

            total_hits = sum(len(v) for v in unique_refs.values())
            status     = "✓" if total_hits > 0 else "✗ UNUSED"

            out(f"  {status}  {class_name}  ({total_hits} reference(s))")
            for src_rel, lines_found in sorted(unique_refs.items()):
                count = len(lines_found)
                lines_str = ', '.join(str(l) for l in sorted(lines_found)[:5])
                if len(lines_found) > 5:
                    lines_str += f" +{len(lines_found)-5} more"
                out(f"      {src_rel}  ×{count}  lines: {lines_str}", 1)

        out()
        out("  Press Enter to choose another file, or q to quit...", flush=True)
        try:
            ch = input()
            if ch.lower() == 'q':
                break
        except (EOFError, KeyboardInterrupt):
            break

    out()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find unused SCSS classes, extract them, analyze file usage."
    )
    parser.add_argument("--root",        default=".", help="Repo root (default: .)")
    parser.add_argument("--scan-only",   action="store_true",
                        help="Report unused classes only, do not extract or delete")
    parser.add_argument("--interactive", action="store_true",
                        help="Skip extraction, go straight to interactive analyzer")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Show changes without writing files")
    args = parser.parse_args()

    root         = Path(args.root).resolve()
    style_files  = walk(root, STYLE_EXTENSIONS)
    source_files = walk(root, SOURCE_EXTENSIONS)

    header("SCSS USAGE AUDIT")
    out(f"Root:         {root}")
    out(f"Style files:  {len(style_files)}")
    out(f"Source files: {len(source_files)}")

    if args.interactive:
        out("\nBuilding usage index...")
        used = extract_used_classes(source_files)
        interactive_analyzer(style_files, source_files, used, root)
        sys.exit(0)

    # ── Scan defined classes ──────────────────────────────────────────────────
    section("SCANNING DEFINED CLASSES")
    out()
    defined = extract_defined_classes(style_files)
    out(f"  Found {len(defined)} class definitions across {len(style_files)} style files")

    # ── Scan used classes ─────────────────────────────────────────────────────
    section("SCANNING SOURCE FOR USAGE")
    out()
    used = extract_used_classes(source_files)
    out(f"  Found {len(used)} unique class references across {len(source_files)} source files")

    # ── Find unused ───────────────────────────────────────────────────────────
    section("UNUSED CLASSES")
    unused = find_unused(defined, used)
    out(f"\n  {len(unused)} unused class(es) found\n")

    if unused:
        by_file: dict[str, list[str]] = defaultdict(list)
        for class_name, locations in unused.items():
            for f, lineno in locations:
                by_file[str(f.relative_to(root))].append(f"{class_name} (line {lineno})")

        for rel, classes in sorted(by_file.items()):
            out(f"  📄 {rel}")
            for cls in sorted(classes):
                out(f"    ✗ {cls}", 1)

    if args.scan_only or not unused:
        if not unused:
            out("  ✓ No unused classes found.")
        sys.exit(0 if not unused else 1)

    # ── Extract & delete ──────────────────────────────────────────────────────
    section("EXTRACTING UNUSED CLASSES")
    out()
    out(f"  Step 1: Clone unused classes to unused-css-<date>.scss")
    out(f"  Step 2: Delete them from their original files")
    out()

    if not args.dry_run:
        print(f"  Proceed? (y/n) ", end="", flush=True)
        ans = input().strip().lower()
        if ans != 'y':
            out("  Cancelled.")
            sys.exit(0)

    out_file = extract_unused(unused, root, args.dry_run)

    # ── Interactive mode ──────────────────────────────────────────────────────
    section("WHAT NEXT?")
    out()
    out("  1. Analyze a specific SCSS file (class usage stats)")
    out("  2. Exit")
    out()
    print("  Choice (1/2): ", end="", flush=True)

    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        choice = "2"

    if choice == "1":
        # Rebuild style_files after deletions
        style_files = walk(root, STYLE_EXTENSIONS)
        interactive_analyzer(style_files, source_files, used, root)

    out("\n  Done.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()