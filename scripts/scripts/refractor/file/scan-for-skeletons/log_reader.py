#!/usr/bin/env python3
"""
log_reader.py
─────────────
Shared base module for all fix-type scripts.

Reads fix-log.json and pulls manual review items by category.
Also provides shared file utilities used across all fix-type scripts.

Usage in a fix-type script:
    from log_reader import load_manual_items, read_file, write_file, backup_file

    items = load_manual_items(root, categories=["CSS_DOUBLE_DECL"])
    for item in items:
        print(item["file"], item["line"], item["detail"])
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


# ── Log reading ───────────────────────────────────────────────────────────────

def load_fix_log(root: Path) -> dict:
    """Load fix-log.json from the repo root."""
    log_path = root / "fix-log.json"
    if not log_path.exists():
        raise FileNotFoundError(
            f"fix-log.json not found at {log_path}\n"
            "Run fix.py at least once first: python3 $current/fix.py --run-all"
        )
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"fix-log.json is malformed: {e}")


def load_manual_items(
    root: Path,
    categories: list[str] | None = None,
    run_id: str | None = None,
) -> list[dict]:
    """
    Pull manual review items from fix-log.json.

    Args:
        root:       repo root where fix-log.json lives
        categories: list of category strings to filter by (e.g. ["CSS_DOUBLE_DECL"])
                    if None, returns all manual items
        run_id:     specific run ID to pull from -- defaults to most recent run

    Returns:
        list of issue dicts with keys: file, line, category, severity, detail, snippet
    """
    data = load_fix_log(root)
    runs = data.get("runs", [])

    if not runs:
        return []

    if run_id:
        runs = [r for r in runs if r.get("run_id") == run_id]
        if not runs:
            available = [r.get("run_id") for r in data.get("runs", [])]
            raise ValueError(
                f"No run found with ID '{run_id}'\n"
                f"Available: {available}"
            )
    else:
        runs = runs[-1:]  # most recent run

    items = []
    for run in runs:
        for entry in run.get("entries", []):
            # We want the manual review items -- those logged as issues
            # Fix-log entries are actions (fix/skip/write/recover)
            # Manual items come from the audit JSON directly
            # So we load them from the audit output embedded in skip entries
            if entry.get("action") in ("skip", "fix"):
                cat = entry.get("category", "")
                if categories and cat not in categories:
                    continue
                items.append({
                    "file":     entry.get("file", ""),
                    "line":     entry.get("line"),
                    "category": cat,
                    "detail":   entry.get("reason", entry.get("detail", "")),
                    "snippet":  entry.get("snippet", ""),
                    "run_id":   run.get("run_id"),
                })

    return items


def load_audit_json(root: Path, audit_json_path: str | None = None) -> list[dict]:
    """
    Load issues directly from a saved audit JSON file.
    More reliable than fix-log.json for manual items since it has full issue data.

    Args:
        root:             repo root
        audit_json_path:  path to audit JSON file -- if None, looks for fix-log.json
    """
    if audit_json_path:
        p = Path(audit_json_path)
        if not p.exists():
            raise FileNotFoundError(f"Audit JSON not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("issues", [])

    # Fall back to fix-log
    data = load_fix_log(root)
    runs = data.get("runs", [])
    if not runs:
        return []

    # The fix-log doesn't store full issue data -- return empty and let
    # the caller know to pass --json output from an audit script directly
    return []


# ── File utilities ────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_file(path: Path, content: str, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.write_text(content, encoding="utf-8")


def backup_file(path: Path, dry_run: bool = False) -> Path | None:
    """Create a .bak backup before modifying a file. Returns backup path."""
    if dry_run or not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def find_files(root: Path, extensions: set[str],
               skip: set[str] | None = None) -> list[Path]:
    skip = skip or {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    return sorted(
        f for f in root.rglob("*")
        if f.is_file()
        and f.suffix in extensions
        and not any(s in f.parts for s in skip)
    )


# ── CSS utilities ─────────────────────────────────────────────────────────────

def parse_css_classes(css_text: str) -> dict[str, dict]:
    """
    Parse CSS text into a dict of class_name → { declarations, start_line, raw }

    Returns:
        {
            ".btn-base": {
                "declarations": {"padding": "10px 20px", "border-radius": "999px"},
                "start_line": 12,
                "raw": ".btn-base { padding: 10px 20px; border-radius: 999px; }"
            }
        }
    """
    classes = {}
    # Remove comments
    clean = re.sub(r'/\*.*?\*/', '', css_text, flags=re.DOTALL)

    for m in re.finditer(r'(\.[\w][\w-]*)\s*\{([^}]*)\}', clean, re.DOTALL):
        name = m.group(1)
        block = m.group(2)
        start_line = css_text[:m.start()].count('\n') + 1

        declarations = {}
        for decl in re.finditer(r'([\w-]+)\s*:\s*([^;]+);', block):
            prop = decl.group(1).strip()
            val  = decl.group(2).strip()
            declarations[prop] = val

        classes[name] = {
            "declarations": declarations,
            "start_line":   start_line,
            "raw":          m.group(0),
        }

    return classes


def declarations_match(a: dict, b: dict) -> bool:
    """Returns True if two declaration dicts are identical."""
    return a == b


def declarations_similar(a: dict, b: dict, threshold: float = 0.8) -> bool:
    """Returns True if two declaration dicts share threshold% of their properties."""
    if not a or not b:
        return False
    all_keys = set(a) | set(b)
    matching = sum(1 for k in all_keys if a.get(k) == b.get(k))
    return matching / len(all_keys) >= threshold


def make_shared_name(name_a: str, name_b: str) -> str:
    """
    Generate a shared CSS class name that encodes both originals.
    e.g. ".btn-base" + ".tile-base" → ".sv-shared-btn-tile-base"
    """
    # Strip leading dot and split by hyphens
    parts_a = name_a.lstrip('.').split('-')
    parts_b = name_b.lstrip('.').split('-')

    # Find unique words from each, drop pure duplicates
    unique_a = [p for p in parts_a if p not in parts_b]
    unique_b = [p for p in parts_b if p not in parts_a]
    shared   = [p for p in parts_a if p in parts_b]

    combined = unique_a + unique_b + shared
    return '.sv-shared-' + '-'.join(combined) if combined else '.sv-shared-combined'


# ── Progress printer ──────────────────────────────────────────────────────────

class Progress:
    """Simple progress printer for fix-type scripts."""

    def __init__(self, total: int, label: str = ""):
        self.total   = total
        self.current = 0
        self.label   = label
        self._bar_width = 40

    def step(self, description: str = "") -> None:
        self.current += 1
        pct  = self.current / self.total if self.total else 1
        done = int(self._bar_width * pct)
        bar  = '█' * done + '░' * (self._bar_width - done)
        print(f"\r  [{bar}] {self.current}/{self.total}  {description[:40]:<40}", end='', flush=True)

    def done(self) -> None:
        print()  # newline after progress bar


# ── Change log ────────────────────────────────────────────────────────────────

_CHANGE_LOG: list[dict] = []


def log_change(action: str, file: str, detail: str, before: str = "", after: str = "") -> None:
    _CHANGE_LOG.append({
        "ts":     datetime.now(timezone.utc).isoformat(),
        "action": action,
        "file":   file,
        "detail": detail,
        "before": before,
        "after":  after,
    })


def write_change_log(root: Path, script_name: str, dry_run: bool) -> None:
    if dry_run or not _CHANGE_LOG:
        return
    log_path = root / "fix-log.json"
    existing = {}
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text())
        except Exception:
            existing = {}

    runs = existing.get("runs", [])
    runs.append({
        "run_id":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "script":  script_name,
        "dry_run": dry_run,
        "entries": _CHANGE_LOG,
    })
    log_path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
    print(f"\n  [log] Changes written → {log_path}")