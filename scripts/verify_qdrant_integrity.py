#!/usr/bin/env python3
"""Verify Qdrant collection integrity: check for content-level duplicates.

Scrolls all points in memory and eidetic collections for a given project,
extracts text payloads, and reports any content-level duplicates.
"""
import hashlib
import json
import sys
from collections import Counter

from qdrant_client import QdrantClient

PROJECT_ID = "748a81a2-ac14-45b8-a185-994997b76828"
QDRANT_URL = "http://localhost:6333"

COLLECTIONS = {
    "memory": f"project_{PROJECT_ID}_memory",
    "eidetic": f"project_{PROJECT_ID}_eidetic",
}


def normalize(text: str) -> str:
    """Normalize text for content comparison."""
    return " ".join(text.strip().lower().split())


def get_text_from_payload(payload: dict) -> str:
    """Extract the text content from a Qdrant point payload."""
    # Memory payloads use 'text', eidetic use 'content'
    for key in ("text", "content", "narrative", "description"):
        if key in payload and payload[key]:
            return str(payload[key])
    # Fallback: serialize the whole payload
    return json.dumps(payload, sort_keys=True)


def scroll_all_points(client: QdrantClient, collection_name: str):
    """Scroll through all points in a collection."""
    all_points = []
    offset = None
    batch_size = 100

    while True:
        results, next_offset = client.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(results)
        if next_offset is None:
            break
        offset = next_offset

    return all_points


def check_collection(client: QdrantClient, label: str, collection_name: str) -> dict:
    """Check a single collection for content-level duplicates."""
    result = {
        "collection": collection_name,
        "label": label,
        "total_points": 0,
        "unique_contents": 0,
        "duplicate_count": 0,
        "duplicate_groups": [],
        "pass": True,
    }

    try:
        info = client.get_collection(collection_name)
        result["total_points"] = info.points_count
    except Exception as e:
        result["error"] = str(e)
        result["pass"] = False
        return result

    if result["total_points"] == 0:
        result["unique_contents"] = 0
        return result

    points = scroll_all_points(client, collection_name)
    result["total_points"] = len(points)

    # Build content hash -> list of point IDs
    content_map = {}  # hash -> (text_snippet, [point_ids])
    for point in points:
        text = get_text_from_payload(point.payload)
        norm = normalize(text)
        h = hashlib.md5(norm.encode()).hexdigest()
        if h not in content_map:
            content_map[h] = (text[:120], [])
        content_map[h][1].append(str(point.id))

    result["unique_contents"] = len(content_map)

    # Find duplicates
    for h, (snippet, ids) in content_map.items():
        if len(ids) > 1:
            result["duplicate_groups"].append({
                "count": len(ids),
                "point_ids": ids,
                "snippet": snippet,
            })

    result["duplicate_count"] = sum(g["count"] - 1 for g in result["duplicate_groups"])
    if result["duplicate_count"] > 0:
        result["pass"] = False

    return result


def main():
    client = QdrantClient(url=QDRANT_URL)

    print("=" * 70)
    print("QDRANT COLLECTION INTEGRITY VERIFICATION")
    print(f"Project: {PROJECT_ID}")
    print("=" * 70)

    all_results = []
    overall_pass = True

    for label, collection_name in COLLECTIONS.items():
        print(f"\nChecking {label} ({collection_name})...")
        result = check_collection(client, label, collection_name)
        all_results.append(result)

        status = "PASS" if result["pass"] else "FAIL"
        if not result["pass"]:
            overall_pass = False

        print(f"  Status: {status}")
        print(f"  Total points: {result['total_points']}")
        print(f"  Unique contents: {result['unique_contents']}")
        print(f"  Content-level duplicates: {result['duplicate_count']}")

        if result.get("error"):
            print(f"  Error: {result['error']}")

        if result["duplicate_groups"]:
            print(f"  Duplicate groups ({len(result['duplicate_groups'])}):")
            for i, group in enumerate(result["duplicate_groups"][:10]):
                print(f"    [{i+1}] {group['count']}x: {group['snippet'][:80]}...")
            if len(result["duplicate_groups"]) > 10:
                print(f"    ... and {len(result['duplicate_groups']) - 10} more groups")

    print("\n" + "=" * 70)
    print(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print("=" * 70)

    # JSON summary for structured consumption
    summary = {
        "project_id": PROJECT_ID,
        "overall_pass": overall_pass,
        "collections": all_results,
    }
    print("\n--- JSON Summary ---")
    print(json.dumps(summary, indent=2, default=str))

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
