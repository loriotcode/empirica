# ClaudeAIParser — Claude.ai Conversation Import

## Overview

`ClaudeAIParser` parses Claude.ai exported conversations into Empirica's
artifact format. It handles the real export format (ZIP archive with
`conversations.json`) and extracts epistemically relevant content.

## Export Format

Claude.ai exports as a ZIP containing:
- `conversations.json` — Array of conversation objects
- `memories.json` — Claude's internal memory about the user

### conversations.json Structure

```json
[
  {
    "uuid": "abc123",
    "name": "Conversation Title",
    "created_at": "2026-01-15T10:30:00Z",
    "updated_at": "2026-01-15T11:45:00Z",
    "chat_messages": [
      {
        "uuid": "msg-1",
        "sender": "human",
        "content": [
          { "type": "text", "text": "..." }
        ],
        "created_at": "2026-01-15T10:30:00Z"
      },
      {
        "uuid": "msg-2",
        "sender": "assistant",
        "content": [
          { "type": "text", "text": "..." },
          { "type": "tool_use", "name": "search", "input": {...} },
          { "type": "tool_result", "content": "..." }
        ]
      }
    ]
  }
]
```

**Critical**: Parse `content[]` blocks, NOT the `text` field. 87/654 messages
have text vs content mismatches. The `text` field drops tool_use/tool_result details.

## Usage

```python
from empirica.core.profile.claude_ai_parser import ClaudeAIParser

parser = ClaudeAIParser()

# Parse from ZIP file
conversations = parser.parse_zip("/path/to/claude-export.zip")

# Parse from JSON directly
conversations = parser.parse_json("/path/to/conversations.json")

# Extract artifacts
for conv in conversations:
    artifacts = parser.extract_artifacts(conv)
    # artifacts: List[Dict] with type, content, confidence, source_turn
```

## Content Block Types

| Type | Count (typical) | Description |
|------|-----------------|-------------|
| `text` | ~756 | Plain text messages |
| `tool_use` | ~272 | Tool invocations with name + input |
| `tool_result` | ~200 | Tool results with content |
| `thinking` | varies | Claude's reasoning (when visible) |

## Integration with Profile Import

```bash
# Import Claude.ai conversations into Empirica profile
empirica profile-import --source claude-ai --file ~/Downloads/claude-export.zip

# Dry-run to preview what would be imported
empirica profile-import --source claude-ai --file ~/Downloads/claude-export.zip --dry-run
```

## memories.json

Contains Claude's internal memory about the user — a pre-built epistemic
profile. Format: array of memory entries with confidence scores.

```json
[
  {
    "content": "User is a senior engineer working on...",
    "created_at": "2026-01-10T08:00:00Z"
  }
]
```

These can be imported as eidetic facts with confidence scores.
