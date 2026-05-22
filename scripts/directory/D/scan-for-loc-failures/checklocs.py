#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# sass_audit.py
# Runs sass on every .scss file in a given directory and collects
# results. Outputs a full report of errors and successes.
#
# Usage:
#   python3 sass_audit.py
# ═══════════════════════════════════════════════════════════════════

import os
import subprocess
import tempfile
from datetime import datetime


def prompt_directory() -> str:
    print('\n  ┌─────────────────────────────────────────┐')
    print('  │           SASS AUDIT                    │')
    print('  └─────────────────────────────────────────┘\n')
    while True:
        path = input('  Enter the directory to audit (e.g. src/styles/locs): ').strip()
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


def run_audit(locs_dir: str):
    scss_files = sorted([
        f for f in os.listdir(locs_dir)
        if f.endswith('.scss')
    ])

    if not scss_files:
        print(f'\n  No .scss files found in {locs_dir}')
        return

    # ── Output directory ─────────────────────────────────────────
    output_dir = os.path.join('scripts', 'data', 'audits', 'sass')
    os.makedirs(output_dir, exist_ok=True)

    # ── Filename based on audited folder name ─────────────────────
    folder_name = os.path.basename(os.path.normpath(locs_dir))
    timestamp   = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'{folder_name}_{timestamp}.txt')

    results   = []
    errors    = []
    successes = []

    print(f'\n  Auditing {len(scss_files)} files in {locs_dir}...\n')

    for filename in scss_files:
        input_path = os.path.join(locs_dir, filename)
        css_name   = filename.replace('.scss', '.css')

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, css_name)

            result = subprocess.run(
                ['sass', input_path, output_path],
                capture_output=True,
                text=True
            )

            has_error = result.returncode != 0
            stderr    = result.stderr.strip()

            # Extract error message — stop before the stack trace
            error_lines = []
            if has_error and stderr:
                for line in stderr.splitlines():
                    if line.strip().startswith('[at '):
                        break
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
    lines.append('  SASS AUDIT REPORT')
    lines.append(f'  {now}')
    lines.append(f'  directory: {locs_dir}')
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
    locs_dir = prompt_directory()
    run_audit(locs_dir)