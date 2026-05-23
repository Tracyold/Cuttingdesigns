#!/usr/bin/env python3
"""
find_skeletons.py
─────────────────
Cascade scan for shared CSS property groups across component SCSS files.

Scans from tier 7 down to tier 3. Each tier finds classes sharing exactly
that many identical property-value pairs across 3+ files. Any property group
already claimed by a higher tier is excluded from lower tiers -- no double counting.

For each skeleton found:
  1. Adds one class to existing shared.scss named from first letters of each file
     e.g. card.scss + tile.scss + button.scss → .ctb-skeleton
  2. Replaces the class in each component file with @extend
  3. Preserves properties unique to each file on top of the @extend

Usage:
  skeleton                 run full cascade
  skeleton --dry-run       preview only
  skeleton --scan-only     report skeletons, no changes
  skeleton --recover       interactive recovery menu
  skeleton --recover latest
  skeleton --min 3         minimum tier (default: 3)
  skeleton --max 7         maximum tier (default: 7)
"""

import argparse
import re
import sys
import shutil
import termios
import tty
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "fix-types"))
from log_reader import read_file, write_file, find_files, log_change, write_change_log

SKIP_FILES = {
    "App.scss", "App.css", "index.scss", "index.css",
    "tokens.scss", "tokens.css", "shared.scss", "shared.css"
}
SKIP_DIRS  = {"node_modules", ".git", "dist", "build", ".next", "backups"}

BACKUP_ROOT = Path(__file__).parent / "backups"
_BACKUP_DIR: Path | None = None


# ── Backup & recovery ─────────────────────────────────────────────────────────

def create_backup_dir() -> Path:
    ts  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    bak = BACKUP_ROOT / ts
    bak.mkdir(parents=True, exist_ok=True)
    return bak


def backup(file_path: Path) -> None:
    if _BACKUP_DIR is None or not file_path.exists():
        return
    dest_name = f"{file_path.parent.name}__{file_path.name}"
    dest = _BACKUP_DIR / dest_name
    (_BACKUP_DIR / (dest_name + ".origin")).write_text(str(file_path), encoding="utf-8")
    shutil.copy2(file_path, dest)


def list_backup_dirs() -> list[Path]:
    if not BACKUP_ROOT.exists():
        return []
    return sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()], reverse=True)


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


def recover(mode: str) -> None:
    dirs = list_backup_dirs()
    if not dirs:
        print("\n  No backups found.\n")
        sys.exit(0)

    target = dirs[0] if mode == "latest" else dirs[
        arrow_menu("Select backup (↑↓ Enter, q to quit):", [d.name for d in dirs])
    ]

    if isinstance(target, int):
        if target < 0:
            print("\n  Cancelled.\n"); sys.exit(0)
        target = dirs[target]

    print(f"\n  Recovering: {target.name}")
    for of in sorted(target.glob("*.origin")):
        original = Path(of.read_text().strip())
        copy     = target / of.stem
        if copy.exists():
            print(f"  ↩ {original.name}")
            shutil.copy2(copy, original)

    print(f"\n  ✓ Restored. Delete backup? (y/n) ", end="", flush=True)
    if input().strip().lower() == 'y':
        shutil.rmtree(target)
        print(f"  ✓ Deleted.")
    print()
    sys.exit(0)


# ── Output helpers ────────────────────────────────────────────────────────────

def out(msg: str = "", indent: int = 0) -> None:
    print("  " * indent + msg, flush=True)

def step(msg: str) -> None:
    print(f"    → {msg}", end="", flush=True)

def ok() -> None:
    print("  ✓", flush=True)

def skip_msg(r: str = "dry run") -> None:
    print(f"  ~ {r}", flush=True)

def section(title: str) -> None:
    print(f"\n{'─'*66}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'─'*66}", flush=True)

def header(title: str, extra: str = "") -> None:
    print(f"\n{'═'*66}", flush=True)
    print(f"  {title}", flush=True)
    if extra: print(f"  {extra}", flush=True)
    print(f"{'═'*66}", flush=True)


# ── CSS parsing ───────────────────────────────────────────────────────────────

def is_component_scss(path: Path) -> bool:
    path_str = str(path)
    return path.name not in SKIP_FILES and (
        "components" in path_str or
        "frontend/styles" in path_str or
        "frontend\\styles" in path_str
    )


def strip_comments(text: str) -> str:
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return re.sub(r'//[^\n]*', '', text)


def parse_classes(text: str) -> dict[str, dict[str, str]]:
    """Returns {class_name: {prop: value}} for all classes in text."""
    clean   = strip_comments(text)
    classes = {}
    for m in re.finditer(r'(\.[\w][\w-]*)\s*\{([^}]*)\}', clean, re.DOTALL):
        name  = m.group(1)
        block = m.group(2)
        decls = {}
        for dm in re.finditer(r'([\w-]+)\s*:\s*([^;]+);', block):
            decls[dm.group(1).strip()] = dm.group(2).strip()
        if decls:
            classes[name] = decls
    return classes


def remove_class_block(text: str, class_name: str) -> str:
    pattern = re.compile(
        r'\n?// [^\n]*\n' + re.escape(class_name) + r'\s*\{[^}]*\}|'
        + re.escape(class_name) + r'\s*\{[^}]*\}',
        re.DOTALL
    )
    return pattern.sub('', text).strip() + '\n'


def relative_use(from_file: Path, to_file: Path) -> str:
    try:
        rel = str(to_file.relative_to(from_file.parent)).replace('\\', '/')
        return rel.replace('.scss', '')
    except ValueError:
        parts_from = from_file.parent.parts
        parts_to   = to_file.parts
        common = sum(1 for a, b in zip(parts_from, parts_to) if a == b)
        ups    = len(parts_from) - common
        downs  = parts_to[common:]
        return '../' * ups + '/'.join(downs).replace('.scss', '')


# ── Skeleton naming ───────────────────────────────────────────────────────────

def make_skeleton_name(files: list[Path], used_names: set[str]) -> str:
    """First letter of each file's stem joined + -skeleton. Unique."""
    letters = []
    seen    = set()
    for f in files:
        letter = f.stem[0].lower()
        if letter not in seen:
            seen.add(letter)
            letters.append(letter)
    base = '.' + ''.join(letters) + '-skeleton'
    name = base
    counter = 2
    while name in used_names:
        name = f"{base}-{counter}"
        counter += 1
    used_names.add(name)
    return name


# ── Cascade scan ──────────────────────────────────────────────────────────────

def cascade_scan(
    scss_files: list[Path],
    root: Path,
    min_tier: int = 3,
    max_tier: int = 7,
) -> list[dict]:
    """
    Scan from max_tier down to min_tier.
    Each tier finds property groups of exactly that size shared across 3+ files.
    Property groups already claimed by a higher tier are excluded.

    Returns list of skeleton dicts sorted tier descending.
    """
    # Build per-file class index
    out(f"\n  Loading {len(scss_files)} component files...\n")
    file_classes: dict[Path, dict[str, dict[str, str]]] = {}

    for i, f in enumerate(scss_files, 1):
        rel = f.relative_to(root)
        out(f"  [{i}/{len(scss_files)}] {rel}")
        file_classes[f] = parse_classes(read_file(f))

    # Claimed property sets -- frozensets of (prop, value) tuples
    claimed: set[frozenset] = set()
    used_names: set[str]    = set()  # track skeleton names to ensure uniqueness
    all_skeletons: list[dict] = []

    for tier in range(max_tier, min_tier - 1, -1):
        section(f"TIER {tier} -- searching for {tier}-property skeletons across 3+ files")

        # For each class in each file, extract all N-property subsets
        # key: frozenset of (prop,val) pairs of size tier
        # value: list of (class_name, file, full_decls)
        tier_index: dict[frozenset, list[tuple[str, Path, dict]]] = defaultdict(list)

        pairs_checked = 0
        for fa in scss_files:
            for fb in scss_files:
                if fa >= fb:
                    continue
                classes_a = file_classes.get(fa, {})
                classes_b = file_classes.get(fb, {})
                pairs_checked += 1

                for name_a, decls_a in classes_a.items():
                    for name_b, decls_b in classes_b.items():
                        if name_a == name_b:
                            continue
                        # Find shared properties
                        shared = {
                            k: v for k, v in decls_a.items()
                            if k in decls_b and decls_b[k] == v
                        }
                        if len(shared) != tier:
                            continue
                        key = frozenset(shared.items())
                        # Skip if claimed by higher tier
                        if any(key <= c for c in claimed):
                            out(f"    ~ Skipping {name_a}/{name_b} -- subset of higher-tier skeleton")
                            continue
                        tier_index[key].append((name_a, fa, decls_a))
                        tier_index[key].append((name_b, fb, decls_b))

        out(f"\n  Checked {pairs_checked} file pairs")

        tier_skeletons = 0
        for prop_key, entries in tier_index.items():
            # Deduplicate entries by (class_name, file)
            unique: list[tuple[str, Path, dict]] = []
            seen_pairs: set[tuple[str, str]] = set()
            for name, f, decls in entries:
                k = (name, str(f))
                if k not in seen_pairs:
                    seen_pairs.add(k)
                    unique.append((name, f, decls))

            # Must appear in 3+ different files
            unique_files = list({str(e[1]) for e in unique})
            if len(unique_files) < 3:
                continue

            # Claim this property group
            claimed.add(prop_key)
            tier_skeletons += 1

            shared_props = dict(prop_key)
            files        = [e[1] for e in unique]
            names        = [e[0] for e in unique]
            full_decls   = [e[2] for e in unique]

            # Unique-to-each-file props (not in shared)
            extras = []
            for name, f, decls in unique:
                extra = {k: v for k, v in decls.items() if k not in shared_props}
                extras.append(extra)

            skel_name = make_skeleton_name(files, used_names)

            out(f"\n  ⚡ SKELETON [{tier} props]: {skel_name}")
            out(f"     Props: {', '.join(sorted(shared_props.keys()))}")
            for name, f in zip(names, files):
                out(f"     • {name}  →  {f.relative_to(root)}")

            all_skeletons.append({
                "tier":         tier,
                "skeleton_name": skel_name,
                "shared_props": shared_props,
                "names":        names,
                "files":        files,
                "extras":       extras,
            })

        out(f"\n  Tier {tier} complete: {tier_skeletons} skeleton(s) found")

    return all_skeletons


# ── Apply skeletons ───────────────────────────────────────────────────────────

def format_props(props: dict) -> str:
    return '\n'.join(f"  {p}: {v};" for p, v in sorted(props.items()))


def apply_skeleton(
    skeleton: dict,
    shared_path: Path,
    root: Path,
    dry_run: bool,
    removed_classes: dict,   # {str(file): set(class_names already removed)}
    extended_classes: dict,  # {str(file)+class_name: skeleton_name already extended}
) -> None:
    skel_name    = skeleton["skeleton_name"]
    shared_props = skeleton["shared_props"]
    names        = skeleton["names"]
    files        = skeleton["files"]
    extras       = skeleton["extras"]
    tier         = skeleton["tier"]

    out(f"\n▶ Applying: {skel_name}  (tier {tier})")

    # ── 1. Add to shared.scss ──────────────────────────────────────────────────
    shared_text = read_file(shared_path) if shared_path.exists() else ""

    if skel_name in shared_text:
        out(f"  ~ {skel_name} already in shared.scss", 1)
    else:
        origins = "\n".join(
            f"  //   {f.relative_to(root)} → {name}"
            for name, f in zip(names, files)
        )
        block = (
            f"\n\n"
            f"// ── Skeleton (tier {tier}) from:\n"
            f"{origins}\n"
            f"// ───────────────────────────────────\n"
            f"{skel_name} {{\n{format_props(shared_props)}\n}}\n"
        )
        step(f"Adding {skel_name} to shared.scss...")
        if not dry_run:
            if not shared_path.parent.exists():
                shared_path.parent.mkdir(parents=True)
            backup(shared_path)
            write_file(shared_path, shared_text + block)
            ok()
            log_change("add_skeleton", str(shared_path),
                       f"Added {skel_name} tier {tier}")
        else:
            skip_msg()

    # ── 2. Update each component file ─────────────────────────────────────────
    use_path = relative_use(files[0], shared_path)

    for name, f, extra in zip(names, files, extras):
        rel      = f.relative_to(root)
        file_key = str(f)
        cls_key  = file_key + "::" + name

        if file_key not in removed_classes:
            removed_classes[file_key] = set()

        text = read_file(f)

        already_removed  = name in removed_classes[file_key]
        already_extended = cls_key in extended_classes

        if already_extended:
            # Class already has an @extend -- append the shared props as extras
            first_skel = extended_classes[cls_key]
            extra_combined = dict(shared_props)
            extra_combined.update(extra)
            step(f"Appending {skel_name} props to existing {name} block...")
            # Show what will be appended
            out(f"  ~ {skel_name if dry_run else '✓'}")
            out(f"    │  Will append to {name}:")
            for line in format_props(extra_combined).splitlines():
                out(f"    │    {line}")
            out(f"    │")
            # Find the existing @extend block and append
            pattern = re.compile(
                re.escape(name) + r'\s*\{([^}]*)\}', re.DOTALL
            )
            def inject_props(m):
                inner = m.group(1).rstrip()
                additions = '\n' + format_props(extra_combined)
                return f"{name} {{{inner}{additions}\n}}"
            new_text = pattern.sub(inject_props, text)
            if new_text == text:
                out(f"  [warn] Could not inject into existing {name} block", 1)
            ok() if not dry_run else skip_msg()
        else:
            if not already_removed:
                step(f"Removing {name} from {f.name}...")
                text = remove_class_block(text, name)
                ok() if not dry_run else skip_msg()
                removed_classes[file_key].add(name)

            # Add @use if not present
            use_line = f"@use '{use_path}' as *;"
            if use_line not in text and f"@use '{use_path}'" not in text:
                step(f"Adding @use to {f.name}...")
                text = use_line + "\n" + text
                ok() if not dry_run else skip_msg()

            # Build @extend block with unique extras preserved
            extra_lines = f"\n{format_props(extra)}" if extra else ""
            extend_block = (
                f"\n// Skeleton: {skel_name} (tier {tier}, {len(shared_props)} shared props)\n"
                f"{name} {{\n"
                f"  @extend {skel_name};"
                f"{extra_lines}\n"
                f"}}\n"
            )
            step(f"Adding @extend for {name}...")
            new_text = text.rstrip() + "\n" + extend_block
            ok() if not dry_run else skip_msg()
            extended_classes[cls_key] = skel_name

            # Always show the full block being generated so it can be verified
            out(f"    │  Will write:")
            for line in extend_block.strip().splitlines():
                out(f"    │    {line}")
            out(f"    │")

        if not dry_run:
            step(f"Writing {f.name}...")
            backup(f)
            write_file(f, new_text if already_extended else new_text)
            ok()
            log_change("apply_skeleton", str(rel),
                       f"{name} @extends {skel_name}",
                       before=name, after=f"@extend {skel_name}")
            out(f"  ✓ {rel}", 1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _BACKUP_DIR

    parser = argparse.ArgumentParser(
        description="Cascade scan for shared CSS skeletons. Extends via shared.scss."
    )
    parser.add_argument("--root",       default=".", help="Repo root (default: .)")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--scan-only",  action="store_true")
    parser.add_argument("--min",        type=int, default=3,
                        help="Minimum property count per tier (default: 3)")
    parser.add_argument("--max",        type=int, default=7,
                        help="Maximum property count per tier (default: 7)")
    parser.add_argument("--shared",     default=None,
                        help="Path to shared.scss (default: src/css/shared.scss)")
    parser.add_argument("--recover",    nargs="?", const="menu", metavar="latest")
    args = parser.parse_args()

    if args.recover is not None:
        recover("latest" if args.recover == "latest" else "list")
        return

    root        = Path(args.root).resolve()
    shared_path = Path(args.shared).resolve() if args.shared \
                  else root / "src" / "css" / "shared.scss"
    dry_run     = args.dry_run

    # Output file -- always written to repo root
    ts          = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    mode_label  = "dry-run" if dry_run else ("scan" if args.scan_only else "apply")
    output_path = root / f"skeleton-{mode_label}-{ts}.txt"
    output_file = open(output_path, "w", encoding="utf-8")

    # Tee all stdout to the file
    import io
    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
        def flush(self):
            for s in self.streams:
                s.flush()

    sys.stdout = Tee(sys.__stdout__, output_file)

    header(
        f"FIND SKELETONS {'(DRY RUN) ' if dry_run else ''}{'(SCAN ONLY) ' if args.scan_only else ''}",
        f"Root: {root}  |  Tiers: {args.max} → {args.min}  |  Min files: 3"
    )

    if not dry_run and not args.scan_only:
        _BACKUP_DIR = create_backup_dir()
        out(f"Backup: {_BACKUP_DIR}\n")

    scss_files = [
        f for f in find_files(root, {".scss", ".css"})
        if is_component_scss(f)
    ]

    out(f"Found {len(scss_files)} component style file(s)")

    skeletons = cascade_scan(scss_files, root, args.min, args.max)

    section("SKELETON SUMMARY")
    if not skeletons:
        out("✓ No skeletons found.")
        sys.exit(0)

    out(f"\n  {len(skeletons)} skeleton(s) across tiers {args.min}–{args.max}:\n")
    for s in skeletons:
        out(f"  [{s['tier']} props] {s['skeleton_name']}  ({s['count'] if 'count' in s else len(s['names'])} files)")
        for name, f in zip(s["names"], s["files"]):
            out(f"    • {name}  →  {f.relative_to(root)}")

    if args.scan_only:
        sys.exit(0)

    section("APPLYING SKELETONS")
    removed_classes: dict  = {}  # {file_path_str: set of removed class names}
    extended_classes: dict = {}  # {file_path_str::class_name: skeleton_name}

    for skeleton in skeletons:
        apply_skeleton(skeleton, shared_path, root, dry_run,
                       removed_classes, extended_classes)

    write_change_log(root, "find_skeletons", dry_run)

    header("DONE")
    out(f"Skeletons applied: {len(skeletons)}")
    if not dry_run and _BACKUP_DIR:
        out(f"Backup:  {_BACKUP_DIR}")
        out(f"Recover: skeleton --recover")
    out(f"Output:  {output_path}")
    print(flush=True)

    sys.stdout = sys.__stdout__
    output_file.close()
    print(f"\n  ✓ Full output saved → {output_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()