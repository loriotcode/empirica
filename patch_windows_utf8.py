#!/usr/bin/env python3
"""
patch_windows_utf8.py — Fix Windows cp1252 crashes in empirica codebase.

Patches applied:
1. Emoji → ASCII replacements in string literals
2. open() without encoding → add encoding='utf-8'
3. subprocess.run/call/Popen without encoding= → add encoding='utf-8'

Usage:
    python patch_windows_utf8.py [--dry-run] [--path ./empirica]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Emoji → ASCII mapping
# Order matters: longer sequences first (e.g. ⚠️ before ⚠)
# ---------------------------------------------------------------------------
EMOJI_MAP = [
    # Status
    ("✅", "[OK]"),
    ("❌", "[FAIL]"),
    ("⚠️", "[WARN]"),
    ("⚠", "[WARN]"),
    ("ℹ️", "[INFO]"),
    ("ℹ", "[INFO]"),
    # Actions / UI
    ("🔄", "[LOAD]"),
    ("🔍", "[SEARCH]"),
    ("🎯", "[TARGET]"),
    ("📁", "[DIR]"),
    ("📂", "[DIR]"),
    ("🆔", "[ID]"),
    ("🗄️", "[DB]"),
    ("🗄", "[DB]"),
    ("🏗️", "[BUILD]"),
    ("🏗", "[BUILD]"),
    ("🛠️", "[TOOLS]"),
    ("🛠", "[TOOLS]"),
    ("💡", "[HINT]"),
    ("🚀", "[RUN]"),
    ("🔑", "[KEY]"),
    ("🔒", "[LOCK]"),
    ("🔓", "[UNLOCK]"),
    ("📊", "[STATS]"),
    ("📈", "[UP]"),
    ("📉", "[DOWN]"),
    ("🧪", "[TEST]"),
    ("🧠", "[THINK]"),
    ("💾", "[SAVE]"),
    ("🗑️", "[DEL]"),
    ("🗑", "[DEL]"),
    ("📝", "[NOTE]"),
    ("📌", "[PIN]"),
    ("⚡", "[FAST]"),
    ("🌐", "[NET]"),
    ("🔗", "[LINK]"),
    ("📦", "[PKG]"),
    ("🎉", "[DONE]"),
    ("👍", "[OK]"),
    ("👎", "[NO]"),
    ("🔥", "[HOT]"),
    ("❓", "[?]"),
    ("✓", "[OK]"),
    ("✗", "[FAIL]"),
    ("✔", "[OK]"),
    ("✘", "[FAIL]"),
    # Box drawing / separators
    ("─", "-"),
    ("━", "="),
    ("│", "|"),
    ("┃", "|"),
    ("╌", "-"),
    ("╍", "="),
    ("└", "+"),
    ("├", "+"),
    ("┌", "+"),
    ("┐", "+"),
    ("┘", "+"),
    ("┤", "+"),
    ("┬", "+"),
    ("┴", "+"),
    ("┼", "+"),
    # Arrows
    ("→", "->"),
    ("←", "<-"),
    ("↑", "^"),
    ("↓", "v"),
    ("↕", "<->"),
    ("⟶", "->"),
    ("⟵", "<-"),
    ("➜", "->"),
    ("➡", "->"),
    # Math / symbols
    ("△", "~"),
    ("Δ", "D"),
    ("∑", "sum"),
    ("×", "x"),
    ("÷", "/"),
    ("≠", "!="),
    ("≤", "<="),
    ("≥", ">="),
    ("•", "*"),
    ("·", "."),
    ("…", "..."),
    ("—", "--"),
    # Misc
    ("🌑", "-"),
    ("💫", "~"),
    ("🎯", "[G]"),
]

# ---------------------------------------------------------------------------
# Regex patterns for structural fixes
# ---------------------------------------------------------------------------

# open() calls: match open( with no encoding= in the args on the same line
# Heuristic: add encoding='utf-8' before the closing paren if missing
# We target the common patterns: open(path), open(path, 'r'), open(path, 'w'),
# open(path, 'rb') and open(path, 'wb') are binary — skip those.
RE_OPEN_NO_ENCODING = re.compile(
    r"""open\(([^)]+)\)""",
)

# subprocess.run/call/Popen without encoding=
RE_SUBPROCESS_NO_ENCODING = re.compile(
    r"""(subprocess\.(?:run|call|check_output|Popen)\([^)]*?)(\s*\))""",
    re.DOTALL,
)


def has_non_ascii(text: str) -> bool:
    """Quick check: does this file contain any non-ASCII character?"""
    try:
        text.encode('ascii')
        return False
    except UnicodeEncodeError:
        return True


def apply_emoji_replacements(content: str) -> tuple[str, int]:
    """Replace emoji/unicode chars with ASCII equivalents. Returns (new_content, count)."""
    count = 0
    for emoji, replacement in EMOJI_MAP:
        if emoji in content:
            n = content.count(emoji)
            content = content.replace(emoji, replacement)
            count += n
    return content, count


def fix_open_encoding(content: str) -> tuple[str, int]:
    """
    Add encoding='utf-8' to open() calls that:
    - Don't already have encoding=
    - Are not binary mode ('rb', 'wb', 'ab', 'r+b', etc.)
    """
    count = 0
    lines = content.split('\n')
    new_lines = []

    for line in lines:
        # Skip lines that already have encoding=
        if 'encoding=' in line:
            new_lines.append(line)
            continue

        # Skip comment lines
        stripped = line.lstrip()
        if stripped.startswith('#'):
            new_lines.append(line)
            continue

        # Find open() calls on this line
        if 'open(' in line:
            # Skip binary modes
            if re.search(r"""open\([^)]*['"]\s*[rawx]*b[rawx]*\s*['"]""", line):
                new_lines.append(line)
                continue

            # Skip sys.stdin/stdout/stderr open variants
            if 'sys.stdin' in line or 'sys.stdout' in line or 'sys.stderr' in line:
                new_lines.append(line)
                continue

            # Pattern: open(path) or open(path, 'r') or open(path, 'w') — single line
            # Add encoding='utf-8' before closing paren
            def add_encoding(m):
                inner = m.group(1).rstrip()
                # Already has encoding?
                if 'encoding' in inner:
                    return m.group(0)
                # Binary mode check
                if re.search(r"""['"]\s*[rawx]*b[rawx]*\s*['"]""", inner):
                    return m.group(0)
                return f"open({inner}, encoding='utf-8')"

            new_line, n = re.subn(
                r"""open\(([^)]+)\)""",
                add_encoding,
                line,
            )
            if n:
                count += n
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return '\n'.join(new_lines), count


def fix_subprocess_encoding(content: str) -> tuple[str, int]:
    """
    Add encoding='utf-8' to subprocess.run/call/check_output calls
    that use text=True but no encoding=.
    Only target single-line calls for safety.
    """
    count = 0
    lines = content.split('\n')
    new_lines = []

    for line in lines:
        if 'subprocess.' in line and 'encoding=' not in line and 'text=True' in line:
            # Add encoding='utf-8' after text=True
            new_line = line.replace('text=True', "text=True, encoding='utf-8'")
            if new_line != line:
                count += 1
                new_lines.append(new_line)
                continue
        new_lines.append(line)

    return '\n'.join(new_lines), count


def patch_file(path: Path, dry_run: bool = False) -> dict:
    """Apply all patches to a single file. Returns stats dict."""
    try:
        original = path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return {"path": str(path), "error": str(e), "skipped": True}

    content = original
    stats = {
        "path": str(path),
        "emojis": 0,
        "open_encoding": 0,
        "subprocess_encoding": 0,
        "changed": False,
        "skipped": False,
    }

    # 1. Emoji replacements
    content, n = apply_emoji_replacements(content)
    stats["emojis"] = n

    # 2. open() encoding
    content, n = fix_open_encoding(content)
    stats["open_encoding"] = n

    # 3. subprocess encoding
    content, n = fix_subprocess_encoding(content)
    stats["subprocess_encoding"] = n

    total_changes = stats["emojis"] + stats["open_encoding"] + stats["subprocess_encoding"]
    if total_changes == 0:
        return stats

    stats["changed"] = True

    if not dry_run:
        path.write_text(content, encoding='utf-8')

    return stats


def main():
    parser = argparse.ArgumentParser(description="Patch empirica for Windows UTF-8 compatibility")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change, don't write")
    parser.add_argument("--path", default="./empirica", help="Path to empirica package dir")
    parser.add_argument("--verbose", action="store_true", help="Show unchanged files too")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"[FAIL] Path not found: {root}")
        sys.exit(1)

    py_files = list(root.rglob("*.py"))
    print(f"[SEARCH] Scanning {len(py_files)} Python files in {root}")
    if args.dry_run:
        print("[NOTE] DRY RUN — no files will be modified\n")

    total_files_changed = 0
    total_emojis = 0
    total_open = 0
    total_subprocess = 0
    errors = []

    for f in sorted(py_files):
        stats = patch_file(f, dry_run=args.dry_run)

        if stats.get("skipped"):
            errors.append(f"  [WARN] {stats['path']}: {stats.get('error', 'skipped')}")
            continue

        if stats["changed"] or args.verbose:
            rel = Path(stats["path"]).relative_to(root.parent)
            parts = []
            if stats["emojis"]:
                parts.append(f"{stats['emojis']} emojis")
            if stats["open_encoding"]:
                parts.append(f"{stats['open_encoding']} open()")
            if stats["subprocess_encoding"]:
                parts.append(f"{stats['subprocess_encoding']} subprocess")
            if parts:
                print(f"  [OK] {rel}: {', '.join(parts)}")

        if stats["changed"]:
            total_files_changed += 1
            total_emojis += stats["emojis"]
            total_open += stats["open_encoding"]
            total_subprocess += stats["subprocess_encoding"]

    print()
    print("=" * 60)
    print(f"Files changed    : {total_files_changed} / {len(py_files)}")
    print(f"Emojis replaced  : {total_emojis}")
    print(f"open() fixed     : {total_open}")
    print(f"subprocess fixed : {total_subprocess}")
    if errors:
        print(f"Errors/skipped   : {len(errors)}")
        for e in errors[:10]:
            print(e)
    if args.dry_run:
        print("\n[NOTE] DRY RUN complete — run without --dry-run to apply")
    else:
        print("\n[OK] Patch complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
