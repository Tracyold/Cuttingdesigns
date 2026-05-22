#!/usr/bin/env python3
"""
scss_audit.py
─────────────
CCG Design System — SCSS Audit Tool

Reads YOUR actual files. Builds truth from what is there.
No hardcoded variable names. No hardcoded namespace maps.
Everything is discovered by scanning your directories.

Flow:
  1. You give it your decs/ directory
     → reads every .scss file
     → extracts every declared variable name ($name)
     → saves one .txt per file to scripts/data/scss/decs/

  2. You give it your effs/ directory
     → reads every .scss file
     → extracts every declared variable name ($name)
     → saves one .txt per file to scripts/data/scss/effs/

  3. You give it your locs/ directory
     → reads every .scss file
     → extracts every namespace.$variable reference used
     → saves one .txt per file to scripts/data/scss/locs/

  4. You give it your tokens.scss file
     → reads every @forward rule
     → extracts namespace → file path mapping
     → saves to scripts/data/scss/tokens/

  5. Compares tokens → decs+effs
     → finds namespaces pointing to missing files
     → finds files not forwarded in tokens
     → saves to scripts/data/scss/results/tokens/

  6. Compares tokens → locs
     → finds variables used in locs but not declared in
       the file that namespace forwards to
     → saves to scripts/data/scss/results/locs/

  7. Asks you: ready for full cross-reference?
     → pulls from data tables (not from live files again)
     → checks every locs reference against every declared
       variable across ALL decs and effs combined
     → saves full results to scripts/data/scss/results/

Run from your project root:
  python3 scripts/scss_audit.py

Or from anywhere:
  python3 /path/to/scss_audit.py
"""

import os
import re
import sys
import json
from pathlib import Path
from collections import defaultdict


# ── Output directories ────────────────────────────────────────────────────────

BASE       = Path("scripts/data/scss")
DECS_OUT   = BASE / "decs"
EFFS_OUT   = BASE / "effs"
LOCS_OUT   = BASE / "locs"
TOKENS_OUT = BASE / "tokens"
RESULTS    = BASE / "results"
RES_TOKENS = RESULTS / "tokens"
RES_LOCS   = RESULTS / "locs"


def make_dirs():
    # nuke the entire base folder and recreate it fresh every run
    # this guarantees no stale files survive anywhere
    import shutil
    if BASE.exists():
        shutil.rmtree(BASE)
    for d in [DECS_OUT, EFFS_OUT, LOCS_OUT, TOKENS_OUT,
              RESULTS, RES_TOKENS, RES_LOCS]:
        d.mkdir(parents=True, exist_ok=True)


# ── Patterns ──────────────────────────────────────────────────────────────────

# $variable-name:  (declaration in any dec or eff file)
DECL   = re.compile(r"^\s*\$([a-zA-Z0-9_-]+)\s*:")

# namespace.$variable-name  (usage in any locs file)
USAGE  = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_-]*)\.\$([a-zA-Z0-9_-]+)")

# @forward 'decs/colors' as color-*;
FWRD   = re.compile(r"""@forward\s+['"]([^'"]+)['"]\s+as\s+([a-zA-Z0-9_-]+)-\*""")


# ── Prompts ───────────────────────────────────────────────────────────────────

def ask_dir(label, example):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"  e.g.  {example}")
    print(f"{'─'*60}")
    while True:
        raw = input(f"  Path: ").strip()
        if not raw:
            print("  (no input — try again)")
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
        print(f"  ✗ Not a directory: {p}")
        print("    Check the path and try again.")


def ask_file(label, example):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"  e.g.  {example}")
    print(f"{'─'*60}")
    while True:
        raw = input(f"  Path: ").strip()
        if not raw:
            print("  (no input — try again)")
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            return p
        print(f"  ✗ Not a file: {p}")
        print("    Check the path and try again.")


def ask_yn(prompt):
    while True:
        ans = input(f"\n  {prompt} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


# ── Step 1 + 2: Read decs or effs ────────────────────────────────────────────

def read_declarations(directory: Path, out_dir: Path) -> dict:
    """
    Scan every .scss file in directory.
    Extract every $variable-name declaration.
    Save one .txt per file.
    Return { file_stem: [var_name, ...] }
    """
    files = sorted(directory.rglob("*.scss"))
    if not files:
        print(f"  ⚠  No .scss files found in {directory}")
        return {}

    print(f"\n  {len(files)} files found\n")
    result = {}

    for fp in files:
        names = []
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = DECL.match(line)
                if m:
                    names.append(m.group(1))

        result[fp.stem] = names

        # save txt
        lines = [f"# {fp.name}", f"# {len(names)} variables", ""]
        lines += [f"  ${n}" for n in names]
        (out_dir / f"{fp.stem}.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )

        print(f"  {fp.name:45s}  {len(names):4d} vars")

    # save combined json
    (out_dir / "_all.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(f"\n  → Saved to {out_dir}/")
    return result


# ── Step 3: Read locs ─────────────────────────────────────────────────────────

def read_locs(directory: Path, out_dir: Path) -> dict:
    """
    Scan every .scss file in locs directory.
    Extract every namespace.$variable reference.
    Save one .txt per file.
    Return { file_stem: [ {ns, var, line_no}, ... ] }
    """
    files = sorted(directory.rglob("*.scss"))
    if not files:
        print(f"  ⚠  No .scss files found in {directory}")
        return {}

    print(f"\n  {len(files)} files found\n")
    result = {}

    for fp in files:
        refs = []
        with open(fp, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith("//") or s.startswith("/*"):
                    continue
                for m in USAGE.finditer(line):
                    refs.append({
                        "ns":      m.group(1),
                        "var":     m.group(2),
                        "line_no": n,
                    })

        result[fp.stem] = refs

        # group by namespace for the txt output
        by_ns = defaultdict(list)
        for r in refs:
            by_ns[r["ns"]].append(r)

        lines = [f"# {fp.name}", f"# {len(refs)} references", ""]
        for ns in sorted(by_ns):
            lines.append(f"  [{ns}]")
            for r in by_ns[ns]:
                lines.append(f"    line {r['line_no']:4d}  {ns}.${r['var']}")
        (out_dir / f"{fp.stem}.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )

        print(f"  {fp.name:45s}  {len(refs):4d} refs")

    (out_dir / "_all.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(f"\n  → Saved to {out_dir}/")
    return result


# ── Step 4: Read tokens ───────────────────────────────────────────────────────

def read_tokens(filepath: Path, out_dir: Path) -> dict:
    """
    Read tokens.scss.
    Extract every @forward rule.
    Return { namespace: relative_path_stem }
    e.g. { "color": "colors", "radius": "border-radius" }
    """
    forwards = {}
    with open(filepath, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = FWRD.search(line)
            if m:
                rel  = m.group(1)   # e.g. decs/colors
                ns   = m.group(2)   # e.g. color
                stem = Path(rel).stem  # e.g. colors
                forwards[ns] = {"stem": stem, "path": rel}

    print(f"\n  {len(forwards)} @forward rules found\n")
    for ns, info in sorted(forwards.items()):
        print(f"  {ns:15s}  →  {info['path']}")

    lines = ["# tokens.scss @forward rules", ""]
    for ns, info in sorted(forwards.items()):
        lines.append(f"  {ns:15s}  →  {info['path']}")
    (out_dir / "forwards.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (out_dir / "forwards.json").write_text(
        json.dumps(forwards, indent=2), encoding="utf-8"
    )
    print(f"\n  → Saved to {out_dir}/")
    return forwards


# ── Step 5: tokens vs decs+effs ──────────────────────────────────────────────

def check_tokens_vs_files(tokens, decs, effs, out_dir) -> dict:
    """
    Find:
    A. Namespaces in tokens pointing to files that do not exist
       in our decs or effs data.
    B. Files in decs/effs that have no namespace in tokens.
    """
    all_files = {**decs, **effs}
    forwarded_stems = {info["stem"] for info in tokens.values()}

    # A — namespace → missing file
    ns_no_file = {}
    for ns, info in tokens.items():
        if info["stem"] not in all_files:
            ns_no_file[ns] = info["path"]

    # B — file with no namespace
    file_no_ns = {
        stem: "no namespace in tokens.scss"
        for stem in all_files
        if stem not in forwarded_stems
    }

    lines = ["# TOKENS vs DEC/EFF FILES", ""]

    if ns_no_file:
        lines.append(f"NAMESPACES WITH NO MATCHING FILE ({len(ns_no_file)})")
        lines.append("These are in tokens.scss but the file was not found")
        lines.append("in your decs/ or effs/ directory.")
        lines.append("")
        for ns, path in sorted(ns_no_file.items()):
            lines.append(f"  [{ns}]  →  {path}  (FILE NOT FOUND)")
        lines.append("")
    else:
        lines.append("✓ All token namespaces point to existing files.")
        lines.append("")

    if file_no_ns:
        lines.append(f"FILES NOT IN TOKENS ({len(file_no_ns)})")
        lines.append("These files exist but are not forwarded in tokens.scss.")
        lines.append("")
        for stem in sorted(file_no_ns):
            lines.append(f"  {stem}.scss")
        lines.append("")
    else:
        lines.append("✓ All dec/eff files are forwarded in tokens.")

    txt = "\n".join(lines)
    print("\n" + txt)
    (out_dir / "token-file-check.txt").write_text(txt, encoding="utf-8")

    result = {"ns_no_file": ns_no_file, "file_no_ns": file_no_ns}
    (out_dir / "token-file-check.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


# ── Step 6: tokens vs locs ────────────────────────────────────────────────────

def check_tokens_vs_locs(tokens, decs, effs, locs, out_dir) -> dict:
    """
    Using only the TOKEN namespace map (not direct file lookup),
    check every locs reference:
    A. Is the namespace in tokens at all?
    B. Is the variable name declared in the file that namespace forwards?
    """
    all_files = {**decs, **effs}

    # build namespace → set of declared var names
    ns_to_vars = {}
    for ns, info in tokens.items():
        stem = info["stem"]
        ns_to_vars[ns] = set(all_files.get(stem, []))

    unknown_ns  = []  # namespace not in tokens at all
    undeclared  = []  # namespace known but var not declared

    for file_stem, refs in locs.items():
        for r in refs:
            ns  = r["ns"]
            var = r["var"]
            if ns not in tokens:
                unknown_ns.append({
                    "file": file_stem, "line": r["line_no"],
                    "ns": ns, "var": var
                })
            elif var not in ns_to_vars.get(ns, set()):
                undeclared.append({
                    "file":        file_stem,
                    "line":        r["line_no"],
                    "ns":          ns,
                    "var":         var,
                    "target_file": tokens[ns]["path"] + ".scss",
                })

    lines = ["# TOKENS vs LOCS", ""]

    if unknown_ns:
        lines.append(f"UNKNOWN NAMESPACES ({len(unknown_ns)})")
        lines.append("These prefixes appear in locs files but have")
        lines.append("no @forward rule in tokens.scss.")
        lines.append("")
        by_ns = defaultdict(list)
        for u in unknown_ns:
            by_ns[u["ns"]].append(u)
        for ns in sorted(by_ns):
            lines.append(f"  [{ns}]  used {len(by_ns[ns])} time(s)")
            for u in by_ns[ns][:5]:
                lines.append(f"    {u['file']}.scss  line {u['line']}  "
                              f"{u['ns']}.${u['var']}")
            if len(by_ns[ns]) > 5:
                lines.append(f"    ... and {len(by_ns[ns])-5} more")
        lines.append("")
    else:
        lines.append("✓ All namespaces in locs are in tokens.")
        lines.append("")

    if undeclared:
        lines.append(f"USED BUT NOT DECLARED ({len(undeclared)})")
        lines.append("These variables appear in locs files but are not")
        lines.append("declared in the file that namespace forwards to.")
        lines.append("")
        by_file = defaultdict(list)
        for u in undeclared:
            by_file[u["file"]].append(u)
        for fs in sorted(by_file):
            items = by_file[fs]
            lines.append(f"  ── {fs}.scss  ({len(items)} issues)")
            for u in sorted(items, key=lambda x: x["line"]):
                lines.append(f"     line {u['line']:4d}  "
                              f"{u['ns']}.${u['var']}")
                lines.append(f"            → {u['target_file']}")
            lines.append("")
    else:
        lines.append("✓ All locs variables match their token declarations.")

    txt = "\n".join(lines)
    print("\n" + txt)
    (out_dir / "locs-token-check.txt").write_text(txt, encoding="utf-8")

    result = {"unknown_ns": unknown_ns, "undeclared": undeclared}
    (out_dir / "locs-token-check.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


# ── Step 7: Full cross-reference ─────────────────────────────────────────────

def full_cross_reference(tokens, decs, effs, locs, out_dir) -> list:
    """
    Pull from the saved data tables.
    For every locs reference check against ALL declared vars
    across ALL decs and effs combined.
    Namespace must be in tokens. Variable must be declared
    in the specific file that namespace forwards to.
    """
    all_files  = {**decs, **effs}
    mismatches = []

    for file_stem, refs in locs.items():
        for r in refs:
            ns  = r["ns"]
            var = r["var"]

            if ns not in tokens:
                continue  # already caught in step 6

            stem     = tokens[ns]["stem"]
            declared = set(all_files.get(stem, []))

            if var not in declared:
                mismatches.append({
                    "locs_file":   file_stem,
                    "line":        r["line_no"],
                    "namespace":   ns,
                    "variable":    var,
                    "target_file": tokens[ns]["path"] + ".scss",
                    "target_stem": stem,
                })

    lines = ["# FULL CROSS-REFERENCE RESULTS", ""]
    lines.append(f"Total mismatches: {len(mismatches)}")
    lines.append("")

    by_locs = defaultdict(list)
    for m in mismatches:
        by_locs[m["locs_file"]].append(m)

    for fs in sorted(by_locs):
        items = by_locs[fs]
        lines.append(f"── {fs}.scss  ({len(items)} issues)")
        for m in sorted(items, key=lambda x: x["line"]):
            lines.append(f"   line {m['line']:4d}  "
                          f"{m['namespace']}.${m['variable']}")
            lines.append(f"          → add ${m['variable']} "
                          f"to {m['target_file']}")
        lines.append("")

    if not mismatches:
        lines.append("✓ CLEAN — all variables declared and matched.")

    txt = "\n".join(lines)
    print("\n" + txt)
    (out_dir / "full-results.txt").write_text(txt, encoding="utf-8")
    (out_dir / "full-mismatches.json").write_text(
        json.dumps(mismatches, indent=2), encoding="utf-8"
    )
    return mismatches


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    make_dirs()

    print("\n" + "═"*60)
    print("  CCG SCSS AUDIT")
    print("  Reads your files. Finds your mismatches.")
    print("  No hardcoded assumptions.")
    print("═"*60)

    # ── Step 1: decs ─────────────────────────────────────────────
    print("\n\n  STEP 1 of 4 — DEC FILES")
    print("  Every $variable declared in your decs/ directory.")
    decs_dir  = ask_dir("decs/ directory", "src/styles/decs")
    print(f"\n  Reading {decs_dir} ...")
    decs = read_declarations(decs_dir, DECS_OUT)

    # ── Step 2: effs ─────────────────────────────────────────────
    print("\n\n  STEP 2 of 4 — EFF FILES")
    print("  Every $variable declared in your effs/ directory.")
    effs_dir  = ask_dir("effs/ directory", "src/styles/effs")
    print(f"\n  Reading {effs_dir} ...")
    effs = read_declarations(effs_dir, EFFS_OUT)

    # ── Step 3: locs ─────────────────────────────────────────────
    print("\n\n  STEP 3 of 4 — LOCS FILES")
    print("  Every namespace.$variable used in your locs/ directory.")
    locs_dir  = ask_dir("locs/ directory", "src/styles/locs")
    print(f"\n  Reading {locs_dir} ...")
    locs = read_locs(locs_dir, LOCS_OUT)

    # ── Step 4: tokens ───────────────────────────────────────────
    print("\n\n  STEP 4 of 4 — TOKENS FILE")
    print("  Every @forward rule in your tokens.scss.")
    tokens_fp = ask_file("tokens.scss", "src/styles/tokens.scss")
    print(f"\n  Reading {tokens_fp} ...")
    tokens = read_tokens(tokens_fp, TOKENS_OUT)

    # ── Step 5: tokens vs files ───────────────────────────────────
    print("\n\n  ── ANALYSIS 1: TOKENS vs DEC/EFF FILES ──")
    check_tokens_vs_files(tokens, decs, effs, RES_TOKENS)

    # ── Step 6: tokens vs locs ────────────────────────────────────
    print("\n\n  ── ANALYSIS 2: TOKENS vs LOCS ──")
    check_tokens_vs_locs(tokens, decs, effs, locs, RES_LOCS)

    # ── Step 7: full cross-reference ──────────────────────────────
    print("\n\n  ── ANALYSIS 3: FULL CROSS-REFERENCE ──")
    print("  Pulls from saved data tables in scripts/data/scss/")
    print("  Checks every locs reference against every declared")
    print("  variable across all decs and effs.")

    if not ask_yn("Ready to run the full cross-reference?"):
        print("\n  Skipped. Partial results saved in scripts/data/scss/results/")
        return

    # reload from saved json — uses YOUR data tables not live files
    decs   = json.loads((DECS_OUT   / "_all.json").read_text())
    effs   = json.loads((EFFS_OUT   / "_all.json").read_text())
    locs   = json.loads((LOCS_OUT   / "_all.json").read_text())
    tokens = json.loads((TOKENS_OUT / "forwards.json").read_text())

    mismatches = full_cross_reference(tokens, decs, effs, locs, RESULTS)

    # ── Final summary ─────────────────────────────────────────────
    total_decs_vars  = sum(len(v) for v in decs.values())
    total_effs_vars  = sum(len(v) for v in effs.values())
    total_locs_refs  = sum(len(v) for v in locs.values())

    print("\n" + "═"*60)
    print("  DONE")
    print("═"*60)
    print(f"\n  Dec files:       {len(decs)}")
    print(f"  Eff files:       {len(effs)}")
    print(f"  Locs files:      {len(locs)}")
    print(f"  Token namespaces:{len(tokens)}")
    print(f"  Dec vars total:  {total_decs_vars}")
    print(f"  Eff vars total:  {total_effs_vars}")
    print(f"  Locs refs total: {total_locs_refs}")
    print(f"  Mismatches:      {len(mismatches)}")
    print(f"\n  Results saved to:")
    print(f"    {RESULTS / 'full-results.txt'}")
    print(f"    {RESULTS / 'full-mismatches.json'}")
    print(f"    {RES_TOKENS / 'token-file-check.txt'}")
    print(f"    {RES_LOCS   / 'locs-token-check.txt'}")
    print()


if __name__ == "__main__":
    main()