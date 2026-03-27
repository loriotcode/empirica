#!/usr/bin/env python3
"""
Generate SEMANTIC_INDEX.yaml from project file discovery.

Scans docs, core modules, hooks, skills, and config to produce a semantic
index that project-embed uses for Qdrant doc embeddings.

Usage:
    python3 scripts/generate_semantic_index.py                    # Writes docs/SEMANTIC_INDEX.yaml
    python3 scripts/generate_semantic_index.py --dry-run          # Preview without writing
    python3 scripts/generate_semantic_index.py --output .empirica # Alternative location
"""

import argparse
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

# File categories and their metadata templates
SCAN_RULES = [
    {
        "glob": "docs/architecture/**/*.md",
        "doc_type": "architecture",
        "tags_from_path": True,
        "base_tags": ["architecture"],
    },
    {
        "glob": "docs/reference/**/*.md",
        "doc_type": "reference",
        "tags_from_path": True,
        "base_tags": ["reference"],
    },
    {
        "glob": "docs/guides/**/*.md",
        "doc_type": "guide",
        "tags_from_path": True,
        "base_tags": ["guide"],
    },
    {
        "glob": "docs/human/**/*.md",
        "doc_type": "user-docs",
        "tags_from_path": True,
        "base_tags": ["documentation"],
    },
    {
        "glob": "docs/*.md",
        "doc_type": "documentation",
        "tags_from_path": True,
        "base_tags": ["documentation"],
    },
    {
        "glob": "empirica/core/**/*.py",
        "doc_type": "core-module",
        "tags_from_path": True,
        "base_tags": ["core", "python"],
        "extract_docstring": True,
    },
    {
        "glob": "empirica/cli/command_handlers/*.py",
        "doc_type": "cli-handler",
        "tags_from_path": True,
        "base_tags": ["cli", "commands"],
        "extract_docstring": True,
    },
    {
        "glob": "empirica/data/**/*.py",
        "doc_type": "data-layer",
        "tags_from_path": True,
        "base_tags": ["data", "database"],
        "extract_docstring": True,
    },
    {
        "glob": "empirica/utils/*.py",
        "doc_type": "utility",
        "tags_from_path": True,
        "base_tags": ["utils"],
        "extract_docstring": True,
    },
    {
        "glob": "empirica/config/*.py",
        "doc_type": "config",
        "tags_from_path": True,
        "base_tags": ["config"],
        "extract_docstring": True,
    },
    {
        "glob": "*.md",
        "doc_type": "project-root",
        "base_tags": ["project"],
        "tags_from_path": False,
    },
]

# Skip patterns
SKIP_PATTERNS = [
    "__pycache__",
    ".pyc",
    "__init__.py",
    "build/",
    ".venv",
    ".egg-info",
    "node_modules",
]

# Minimum file size to index (skip empty/trivial files)
MIN_FILE_SIZE = 100  # bytes


def _should_skip(path: str) -> bool:
    return any(pat in path for pat in SKIP_PATTERNS)


def _extract_module_docstring(filepath: Path) -> Optional[str]:
    """Extract the module-level docstring from a Python file."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        # Match triple-quoted string at the start (after optional comments/encoding)
        match = re.search(r'^(?:\s*#[^\n]*\n)*\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')',
                          content, re.DOTALL)
        if match:
            doc = (match.group(1) or match.group(2) or "").strip()
            # First line only for description
            return doc.split("\n")[0].strip()
    except Exception:
        pass
    return None


def _extract_md_title(filepath: Path) -> Optional[str]:
    """Extract the first heading from a markdown file."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if line.startswith("## "):
                    return line[3:].strip()
    except Exception:
        pass
    return None


def _tags_from_filepath(relpath: str) -> List[str]:
    """Generate tags from the file path components."""
    parts = Path(relpath).parts
    tags = []
    for part in parts[:-1]:  # Skip filename
        cleaned = part.replace("_", "-").replace(".", "-").lower()
        if cleaned and cleaned not in ("docs", "empirica", "src"):
            tags.append(cleaned)
    # Add filename stem
    stem = Path(relpath).stem.replace("_", "-").lower()
    if stem and len(stem) > 2:
        tags.append(stem)
    return tags


def _concepts_from_content(filepath: Path, max_concepts: int = 5) -> List[str]:
    """Extract key concepts from file content (lightweight heuristic)."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")[:3000]
    except Exception:
        return []

    concepts = set()

    if filepath.suffix == ".py":
        # Extract class and function names
        for match in re.finditer(r"^class\s+(\w+)", content, re.MULTILINE):
            concepts.add(match.group(1))
        for match in re.finditer(r"^def\s+(\w+)", content, re.MULTILINE):
            name = match.group(1)
            if not name.startswith("_"):
                concepts.add(name)
    elif filepath.suffix == ".md":
        # Extract headings
        for match in re.finditer(r"^#{1,3}\s+(.+)", content, re.MULTILINE):
            heading = match.group(1).strip()
            if len(heading) < 60:
                concepts.add(heading)

    return sorted(concepts)[:max_concepts]


def scan_project(project_root: Path) -> Dict[str, Dict]:
    """Scan project and build semantic index entries."""
    entries = {}

    for rule in SCAN_RULES:
        glob_pattern = rule["glob"]
        matches = sorted(project_root.glob(glob_pattern))

        for filepath in matches:
            if not filepath.is_file():
                continue
            if filepath.stat().st_size < MIN_FILE_SIZE:
                continue

            relpath = str(filepath.relative_to(project_root))
            if _should_skip(relpath):
                continue
            if relpath in entries:
                continue  # First rule wins

            # Build tags
            tags = list(rule.get("base_tags", []))
            if rule.get("tags_from_path"):
                tags.extend(_tags_from_filepath(relpath))
            tags = sorted(set(tags))

            # Extract description
            description = None
            if rule.get("extract_docstring") and filepath.suffix == ".py":
                description = _extract_module_docstring(filepath)
            elif filepath.suffix == ".md":
                description = _extract_md_title(filepath)

            # Extract concepts
            concepts = _concepts_from_content(filepath)

            entry = {
                "tags": tags,
                "doc_type": rule["doc_type"],
            }
            if description:
                entry["description"] = description
            if concepts:
                entry["concepts"] = concepts

            entries[relpath] = entry

    return entries


def generate_yaml(entries: Dict[str, Dict]) -> str:
    """Generate YAML content from index entries."""
    import yaml

    index = {
        "version": "1.0",
        "generated_by": "scripts/generate_semantic_index.py",
        "total_docs_indexed": len(entries),
        "index": entries,
    }

    return yaml.dump(index, default_flow_style=False, sort_keys=False, allow_unicode=True)


def main():
    parser = argparse.ArgumentParser(description="Generate SEMANTIC_INDEX.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--output", default="docs", help="Output directory (default: docs/)")
    parser.add_argument("--root", default=".", help="Project root")
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    entries = scan_project(project_root)

    # Stats
    by_type = {}
    for e in entries.values():
        t = e.get("doc_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"Scanned {project_root}")
    print(f"Total entries: {len(entries)}")
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {count}")

    if args.dry_run:
        print(f"\n--- Preview (first 20 entries) ---")
        for i, (path, meta) in enumerate(list(entries.items())[:20]):
            desc = meta.get("description", "")[:60]
            print(f"  {path}: {desc}")
        print(f"\n(dry-run, not written)")
        return

    output_dir = project_root / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "SEMANTIC_INDEX.yaml"

    yaml_content = generate_yaml(entries)
    output_path.write_text(yaml_content, encoding="utf-8")
    print(f"\nWritten to: {output_path}")
    print(f"Run 'empirica project-embed' to re-index docs in Qdrant")


if __name__ == "__main__":
    main()
