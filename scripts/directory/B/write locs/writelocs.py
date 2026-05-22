"""
scss_var_replacer.py

Reads a directory of SCSS files containing class blocks with raw CSS values.
Searches two variable directories (each file named after a CSS property)
to find matching $variable names, then replaces raw values with those names.

Unmatched properties are left as-is and logged as warnings.
"""

import os
import re


# ── Helpers ──────────────────────────────────────────────────────────────────

def prompt_directory(label):
    """Ask the user for a directory path and validate it exists."""
    while True:
        path = input(f"\n📁 Enter path for {label}: ").strip()
        if os.path.isdir(path):
            return path
        print(f"   ✗ Not a valid directory: '{path}' — please try again.")


def load_scss_files(directory):
    """Return a dict of { filename_stem: full_path } for all .scss files in a directory."""
    files = {}
    for fname in os.listdir(directory):
        if fname.endswith(".scss"):
            stem = fname[:-5]  # strip .scss
            files[stem] = os.path.join(directory, fname)
    return files


def parse_variables(filepath):
    """
    Parse a property SCSS file and return a dict of { variable_name: raw_value }.
    Looks for lines like:  $chat-send-btn: 12px;
    """
    variables = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            match = re.match(r"\s*(\$[\w-]+)\s*:\s*(.+?)\s*;", line)
            if match:
                var_name, raw_value = match.group(1), match.group(2).strip()
                variables[var_name] = raw_value
    return variables


def build_property_lookup(var_dirs):
    """
    For each variable directory, load all property files.
    Returns: { property_name: { $var-name: raw_value, ... }, ... }
    Merges both directories — second directory wins on conflict.
    """
    lookup = {}
    for var_dir in var_dirs:
        for prop_stem, filepath in load_scss_files(var_dir).items():
            variables = parse_variables(filepath)
            if prop_stem not in lookup:
                lookup[prop_stem] = {}
            lookup[prop_stem].update(variables)
    return lookup


def extract_class_name(block_header):
    """
    Pull the base class name from a selector line.
    e.g.  '.chat-send-btn {'  →  'chat-send-btn'
          '.chat-send-btn:hover {'  →  'chat-send-btn'
    """
    match = re.match(r"[.\s]*\.?([\w-]+)", block_header)
    return match.group(1) if match else None


def process_scss_file(filepath, property_lookup, warnings):
    """
    Read one class SCSS file, replace raw values with $variable names where found.
    Returns the rewritten content as a string.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into blocks — we track the current class name as we go
    lines = content.splitlines(keepends=True)
    output_lines = []
    current_class = None

    for line in lines:
        # Detect a class/selector opening line
        class_match = re.match(r"^\s*\.?([\w-]+[\w\-]*)\s*\{", line)
        if class_match:
            current_class = class_match.group(1)
            output_lines.append(line)
            continue

        # Detect a CSS property line: e.g.   padding: 12px;
        prop_match = re.match(r"^(\s*)([\w-]+)\s*:\s*(.+?)\s*;", line)
        if prop_match and current_class:
            indent     = prop_match.group(1)
            prop_name  = prop_match.group(2)
            raw_value  = prop_match.group(3).strip()

            # Look up this property in the lookup table
            prop_vars = property_lookup.get(prop_name)
            if prop_vars is None:
                # No file exists for this property at all
                warnings.append(
                    f"  [NO FILE]  property '{prop_name}' has no matching .scss file "
                    f"(class: .{current_class})"
                )
                output_lines.append(line)
                continue

            # Try to find $current-class-name in that property's variables
            target_var = f"${current_class}"
            if target_var in prop_vars:
                new_line = f"{indent}{prop_name}: {target_var};\n"
                output_lines.append(new_line)
            else:
                warnings.append(
                    f"  [NO MATCH] '{target_var}' not found in {prop_name}.scss "
                    f"— leaving raw value '{raw_value}' as-is"
                )
                output_lines.append(line)
            continue

        # Close brace — clear current class
        if re.match(r"^\s*\}", line):
            current_class = None

        output_lines.append(line)

    return "".join(output_lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SCSS Variable Replacer")
    print("=" * 60)

    # Prompt for all four directories
    class_dir  = prompt_directory("class files (the ones with raw CSS values)")
    var_dir_1  = prompt_directory("first variable directory (property .scss files)")
    var_dir_2  = prompt_directory("second variable directory (property .scss files)")
    output_dir = prompt_directory("output directory (where the new files will be saved)")

    print("\n🔍 Loading variable files...")
    property_lookup = build_property_lookup([var_dir_1, var_dir_2])
    print(f"   Loaded {len(property_lookup)} property file(s): {', '.join(sorted(property_lookup.keys()))}")

    class_files = load_scss_files(class_dir)
    print(f"\n📄 Found {len(class_files)} class file(s) to process.\n")

    all_warnings = []

    for stem, filepath in sorted(class_files.items()):
        print(f"  Processing: {stem}.scss")
        file_warnings = []
        new_content = process_scss_file(filepath, property_lookup, file_warnings)

        # Write to the output directory, same filename as the original
        out_path = os.path.join(output_dir, f"{stem}.scss")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"    ✓ Saved → {out_path}")

        if file_warnings:
            all_warnings.extend([f"\n  ── {stem}.scss ──"] + file_warnings)

    # Summary
    print("\n" + "=" * 60)
    if all_warnings:
        print(f"⚠️  Done with {len(all_warnings)} warning(s):\n")
        for w in all_warnings:
            print(w)
    else:
        print("✅  Done! All values replaced with no warnings.")
    print("=" * 60)


if __name__ == "__main__":
    main()