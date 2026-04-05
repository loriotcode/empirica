# Local Memory Management System

**5-tier hierarchy with CC memory/*.md as KV cache**
**POSTFLIGHT-driven promotion/demotion**

## Transactions

### T1: POSTFLIGHT hot-cache update (G1: f2e82ad1)
- Extract update_memory_hot_cache from session-end → empirica/core/memory_manager.py
- Call from POSTFLIGHT after grounded verification
- Session-end keeps call as fallback
- **Depends on:** nothing

### T2: Memory file promotion (G2: 662c9b79)
- Qdrant eidetic facts with confirmation_count >= 3, confidence >= 0.7 → memory/*.md
- Write files with CC auto-memory frontmatter format
- Update MEMORY.md index
- **Depends on:** T1

### T3: Memory file demotion (G3: c9c54db3)
- Track memory file access via tool traces
- Files not referenced in N transactions → memory/_archive/
- Remove from MEMORY.md index
- **Depends on:** T1

### T4: MEMORY.md eviction (G4: cad3af13)
- Auto section > 100 lines → evict lowest-ranked
- Never touch manual sections
- Evicted items stay in Qdrant
- **Depends on:** T1

### T5: CLI extensions (G5: 7ea5488b)
- memory-report: add CC memory stats (file count, ages, MEMORY.md lines)
- profile-prune --scope memory: prune stale memory files
- **Depends on:** T1, T2, T3
