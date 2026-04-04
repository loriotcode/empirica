"""
Workflow Pattern Mining — detect repeated tool sequences across transactions.

Architecture:
- Traces recorded by sentinel-gate.py in hook_counters['tool_trace']
- POSTFLIGHT archives traces to sessions.db (reflex_data.tool_trace)
- This module runs detection on archived traces
- Patterns surfaced as findings via CLI or at session start

Algorithm (inspired by Zoku, adapted for Empirica):
1. Extract contiguous subsequences (length 3-10)
2. Normalize: tool_name + phase only (ignore targets for matching)
3. Count distinct transactions per subsequence
4. Filter: appears in 2+ transactions
5. Remove strict subsets of longer patterns
6. Rank by frequency then length
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Subsequence length bounds
MIN_SEQ_LEN = 3
MAX_SEQ_LEN = 10
# Minimum transaction count for a pattern to be reported
MIN_FREQUENCY = 2


@dataclass
class WorkflowPattern:
    """A detected repeated workflow pattern."""
    sequence: list[str]          # e.g. ["Read(n)", "Grep(n)", "Edit(p)", "Bash(p)"]
    frequency: int               # Number of distinct transactions
    transaction_ids: list[str]   # Which transactions exhibited this
    avg_position: float = 0.0    # Average position in transaction (0.0=start, 1.0=end)
    example_targets: list[str] = field(default_factory=list)  # Example file/cmd targets

    @property
    def signature(self) -> str:
        """Human-readable signature like 'Read→Grep→Edit→Bash(test)'."""
        return " → ".join(self.sequence)

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "signature": self.signature,
            "frequency": self.frequency,
            "transaction_ids": self.transaction_ids[:5],
            "avg_position": round(self.avg_position, 2),
            "example_targets": self.example_targets[:3],
        }


def normalize_trace(trace: list[list[str]]) -> list[str]:
    """Normalize a tool trace to comparable tokens.

    Input: [["Read", "server.py", "n"], ["Grep", "pattern", "n"], ...]
    Output: ["Read(n)", "Grep(n)", ...]

    Targets are dropped for matching (Read(n) matches Read(n) regardless of file).
    """
    tokens = []
    for entry in trace:
        if not entry or len(entry) < 3:
            continue
        tool_name = entry[0]
        phase = entry[2] if len(entry) > 2 else "n"
        tokens.append(f"{tool_name}({phase})")
    return tokens


def extract_subsequences(tokens: list[str], min_len: int = MIN_SEQ_LEN,
                         max_len: int = MAX_SEQ_LEN) -> list[tuple[str, ...]]:
    """Extract all contiguous subsequences of the given length range."""
    subsequences = []
    for length in range(min_len, min(max_len + 1, len(tokens) + 1)):
        for start in range(len(tokens) - length + 1):
            subseq = tuple(tokens[start:start + length])
            subsequences.append(subseq)
    return subsequences


def remove_subsets(patterns: dict[tuple[str, ...], set[str]]) -> dict[tuple[str, ...], set[str]]:
    """Remove patterns that are strict subsets of longer patterns with same frequency."""
    to_remove = set()
    sorted_patterns = sorted(patterns.keys(), key=len, reverse=True)

    for i, longer in enumerate(sorted_patterns):
        for shorter in sorted_patterns[i + 1:]:
            if shorter in to_remove:
                continue
            # Check if shorter is a contiguous subsequence of longer
            longer_str = " ".join(longer)
            shorter_str = " ".join(shorter)
            if shorter_str in longer_str:
                # Only remove if frequency is same or lower
                if len(patterns[shorter]) <= len(patterns[longer]):
                    to_remove.add(shorter)

    return {k: v for k, v in patterns.items() if k not in to_remove}


def detect_patterns(traces: dict[str, list[list[str]]],
                    min_frequency: int = MIN_FREQUENCY) -> list[WorkflowPattern]:
    """Detect repeated workflow patterns across transactions.

    Args:
        traces: {transaction_id: tool_trace} where tool_trace is
                [[tool_name, target, phase], ...]
        min_frequency: Minimum number of distinct transactions for a pattern

    Returns:
        List of WorkflowPattern sorted by frequency (desc) then length (desc)
    """
    if len(traces) < min_frequency:
        return []

    # Step 1: Normalize all traces
    normalized: dict[str, list[str]] = {}
    raw_traces: dict[str, list[list[str]]] = {}
    for tx_id, trace in traces.items():
        tokens = normalize_trace(trace)
        if len(tokens) >= MIN_SEQ_LEN:
            normalized[tx_id] = tokens
            raw_traces[tx_id] = trace

    if len(normalized) < min_frequency:
        return []

    # Step 2: Extract subsequences per transaction, count distinct transactions
    pattern_txs: dict[tuple[str, ...], set[str]] = {}
    pattern_positions: dict[tuple[str, ...], list[float]] = {}

    for tx_id, tokens in normalized.items():
        seen_in_tx: set[tuple[str, ...]] = set()
        subseqs = extract_subsequences(tokens)
        for subseq in subseqs:
            if subseq not in seen_in_tx:
                seen_in_tx.add(subseq)
                pattern_txs.setdefault(subseq, set()).add(tx_id)
                # Track position (normalized 0-1)
                idx = tokens.index(subseq[0]) if subseq[0] in tokens else 0
                pos = idx / max(len(tokens) - 1, 1)
                pattern_positions.setdefault(subseq, []).append(pos)

    # Step 3: Filter by minimum frequency
    frequent = {seq: txs for seq, txs in pattern_txs.items()
                if len(txs) >= min_frequency}

    if not frequent:
        return []

    # Step 4: Remove strict subsets
    filtered = remove_subsets(frequent)

    # Step 5: Build WorkflowPattern objects
    patterns = []
    for seq, tx_ids in filtered.items():
        # Get example targets from the first matching transaction
        example_targets = []
        for tx_id in list(tx_ids)[:1]:
            raw = raw_traces.get(tx_id, [])
            for entry in raw:
                if len(entry) >= 2 and entry[1]:
                    example_targets.append(f"{entry[0]}:{entry[1]}")
                    if len(example_targets) >= 3:
                        break

        avg_pos = sum(pattern_positions.get(seq, [0])) / max(len(pattern_positions.get(seq, [1])), 1)

        patterns.append(WorkflowPattern(
            sequence=list(seq),
            frequency=len(tx_ids),
            transaction_ids=sorted(tx_ids),
            avg_position=avg_pos,
            example_targets=example_targets,
        ))

    # Step 6: Sort by frequency (desc), then length (desc)
    patterns.sort(key=lambda p: (-p.frequency, -len(p.sequence)))

    return patterns


def load_traces_from_db(db_path: str, limit: int = 50) -> dict[str, list[list[str]]]:
    """Load tool traces from sessions.db reflex_data.

    Traces are stored in POSTFLIGHT reflex_data as 'tool_trace' field
    (copied from hook_counters at POSTFLIGHT time).

    Args:
        db_path: Path to sessions.db
        limit: Maximum number of recent transactions to load

    Returns:
        {transaction_id: tool_trace}
    """
    import sqlite3

    traces = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT transaction_id, reflex_data
            FROM reflexes
            WHERE phase = 'POSTFLIGHT'
            AND reflex_data IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()

        for row in rows:
            try:
                data = json.loads(row['reflex_data'])
                trace = data.get('tool_trace', [])
                tx_id = row['transaction_id']
                if trace and tx_id:
                    traces[tx_id] = trace
            except (json.JSONDecodeError, TypeError):
                continue

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to load traces: {e}")

    return traces


def format_patterns_human(patterns: list[WorkflowPattern], limit: int = 5) -> str:
    """Format patterns for human display."""
    if not patterns:
        return "No repeated workflow patterns detected yet."

    lines = [f"Detected {len(patterns)} workflow pattern(s):\n"]
    for i, p in enumerate(patterns[:limit]):
        lines.append(f"  {i+1}. {p.signature}")
        lines.append(f"     Frequency: {p.frequency} transactions | "
                      f"Position: {'early' if p.avg_position < 0.3 else 'mid' if p.avg_position < 0.7 else 'late'}")
        if p.example_targets:
            lines.append(f"     Examples: {', '.join(p.example_targets)}")
        lines.append("")

    if len(patterns) > limit:
        lines.append(f"  ... and {len(patterns) - limit} more")

    return "\n".join(lines)
