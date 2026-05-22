#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# tsx_audit.py
# Runs eslint on every .tsx file in a given directory and collects
# results. Outputs a full report of errors and successes.
#
# Usage:
#   python3 tsx_audit.py
# ═══════════════════════════════════════════════════════════════════

import os
import subprocess
from datetime import datetime


def prompt_directory() -> str:
    print('\n  ┌─────────────────────────────────────────┐')
    print('  │           TSX AUDIT                     │')
    print('  └─────────────────────────────────────────┘\n')
    while True:
        path = input('  Enter the directory to audit (e.g. src/components/matr): ').strip()
        if not path:
            print('  Please enter a path.\n')
            continue
        if not os.path.isdir(path):
            print(f'  Directory not found: {path}\n')
            retry = input('  Try again? (y/n): ').strip().lower()
            if retry != 'y':
                raise SystemExit('Audit cancelled.')
            continue
        return path


def run_audit(directory: str):
    tsx_files = sorted([
        f for f in os.listdir(directory)
        if f.endswith('.tsx') or f.endswith('.ts')
    ])

    if not tsx_files:
        print(f'\n  No .tsx/.ts files found in {directory}')
        return

    # ── Output directory ─────────────────────────────────────────
    output_dir = os.path.join('scripts', 'data', 'audits', 'tsx-audit')
    os.makedirs(output_dir, exist_ok=True)

    # ── Filename based on audited folder name ─────────────────────
    folder_name = os.path.basename(os.path.normpath(directory))
    timestamp   = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'{folder_name}_{timestamp}.txt')

    results   = []
    errors    = []
    successes = []

    print(f'\n  Auditing {len(tsx_files)} files in {directory}...\n')

    for filename in tsx_files:
        file_path = os.path.join(directory, filename)

        result = subprocess.run(
            ['npx', 'eslint', '--no-ignore', file_path],
            capture_output=True,
            text=True
        )

        has_error = result.returncode != 0
        output    = (result.stdout + result.stderr).strip()

        # Extract error lines — skip blank lines and the file path line
        error_lines = []
        if has_error and output:
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped == file_path:
                    continue
                error_lines.append(line)
            error_summary = '\n    '.join(error_lines).strip()
        else:
            error_summary = ''

        entry = {
            'file':   filename,
            'status': 'ERROR' if has_error else 'OK',
            'error':  error_summary,
        }

        results.append(entry)

        if has_error:
            errors.append(entry)
            print(f'  ✗  {filename}')
        else:
            successes.append(entry)
            print(f'  ✓  {filename}')

    # ── Build report ─────────────────────────────────────────────
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    lines.append('═' * 70)
    lines.append('  TSX AUDIT REPORT')
    lines.append(f'  {now}')
    lines.append(f'  directory: {directory}')
    lines.append('═' * 70)
    lines.append('')
    lines.append(f'  total:    {len(results)}')
    lines.append(f'  ok:       {len(successes)}')
    lines.append(f'  errors:   {len(errors)}')
    lines.append('')

    if errors:
        lines.append('─' * 70)
        lines.append('  ERRORS')
        lines.append('─' * 70)
        lines.append('')
        for e in errors:
            lines.append(f'  ✗  {e["file"]}')
            lines.append(f'    {e["error"]}')
            lines.append('')

    lines.append('─' * 70)
    lines.append('  ALL FILES')
    lines.append('─' * 70)
    lines.append('')
    for r in results:
        status = '✓' if r['status'] == 'OK' else '✗'
        lines.append(f'  {status}  {r["file"]}')
        if r['error']:
            lines.append(f'    {r["error"]}')
            lines.append('')

    lines.append('')
    lines.append('═' * 70)

    report = '\n'.join(lines)

    with open(output_file, 'w') as f:
        f.write(report)

    print(f'\n  ─────────────────────────────────────────')
    print(f'  Report saved to: {output_file}')
    print(f'  {len(successes)} ok  ·  {len(errors)} errors')
    print(f'  ─────────────────────────────────────────\n')


if __name__ == '__main__':
    directory = prompt_directory()
    run_audit(directory)