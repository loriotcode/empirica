# Empirica Serve API Reference

The `empirica serve` daemon is a localhost FastAPI server that provides REST endpoints
for the Empirica Chrome extension. The extension extracts epistemic artifacts client-side
(in TypeScript) and sends them to this daemon for storage in the Empirica database.

**Security model:** Localhost-only by default (`127.0.0.1`). No authentication is required.
CORS is configured to allow all origins so that `chrome-extension://` URLs can reach the
server. The security boundary is the network interface, not CORS.

**Source:** `empirica/api/serve_app.py`

---

## Starting the Server

```bash
empirica serve
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8000` | Port to listen on |
| `--host` | `127.0.0.1` | Host to bind to. Use `0.0.0.0` for network access. |
| `--reload` | off | Enable auto-reload on code changes (development only) |

### Examples

```bash
# Default: localhost:8000
empirica serve

# Custom port
empirica serve --port 9090

# Bind to all interfaces (use with caution)
empirica serve --host 0.0.0.0 --port 8000

# Development mode with auto-reload
empirica serve --reload
```

### Requirements

The server requires `uvicorn` and `fastapi`. Install with the API extras:

```bash
pip install 'empirica[api]'
```

---

## Endpoints

### GET /api/v1/health

Health check endpoint. Reports daemon status and availability of optional integrations
(Ollama for embeddings, Qdrant for vector search).

**Response:** `HealthResponse` (200 OK)

The endpoint probes `localhost:11434` (Ollama) and `localhost:6333` (Qdrant) with a
2-second timeout to determine availability.

#### Example

```bash
curl http://localhost:8000/api/v1/health
```

```json
{
  "ok": true,
  "version": "0.1.0",
  "api_version": "v1",
  "ollama": false,
  "claude_mem": false,
  "qdrant": true
}
```

---

### POST /api/v1/artifacts/import

Import pre-extracted artifacts from the Chrome extension into the Empirica database.

Artifacts are stored in the appropriate SQLite tables based on their `type` field.
Deduplication is performed when a `contentHash` is provided in the artifact payload:
the endpoint checks for an existing record with identical content before inserting.

All imported artifacts are assigned `project_id = "extension-import"`.

**Request body:** `ArtifactImportRequest` (JSON)

**Response:** `ArtifactImportResponse` (200 OK, or 500 on failure)

#### Artifact Type to Table Mapping

| Artifact Type | Database Table | Notes |
|---------------|----------------|-------|
| `finding` | `project_findings` | Stored directly |
| `decision` | `project_findings` | Content prefixed with `[decision]` |
| `dead_end` | `project_dead_ends` | Uses `metadata.whyFailed` for the `why_failed` column |
| `mistake` | `mistakes_made` | Uses `metadata.whyFailed` and `metadata.prevention` |
| `unknown` | `project_unknowns` | Stored directly |

#### Example

```bash
curl -X POST http://localhost:8000/api/v1/artifacts/import \
  -H "Content-Type: application/json" \
  -d '{
    "artifacts": [
      {
        "type": "finding",
        "content": "React 19 compiler eliminates the need for useMemo in most cases",
        "confidence": 0.8,
        "metadata": {"impact": 0.7}
      },
      {
        "type": "dead_end",
        "content": "Tried using Web Workers for state management",
        "confidence": 0.9,
        "metadata": {"whyFailed": "Serialization overhead negated any parallelism gains"}
      },
      {
        "type": "unknown",
        "content": "Does the new streaming API support backpressure?",
        "confidence": 0.3
      }
    ]
  }'
```

```json
{
  "ok": true,
  "imported": 3,
  "duplicates_skipped": 0,
  "errors": []
}
```

#### Example with Deduplication

```bash
curl -X POST http://localhost:8000/api/v1/artifacts/import \
  -H "Content-Type: application/json" \
  -d '{
    "artifacts": [
      {
        "type": "finding",
        "content": "React 19 compiler eliminates the need for useMemo in most cases",
        "confidence": 0.8,
        "contentHash": "a1b2c3d4e5f6",
        "metadata": {"impact": 0.7}
      }
    ]
  }'
```

```json
{
  "ok": true,
  "imported": 0,
  "duplicates_skipped": 1,
  "errors": []
}
```

---

### GET /api/v1/profile/status

Retrieve the epistemic profile status, including artifact counts across all database
tables and the timestamp of the last sync.

**Response:** `ProfileStatusResponse` (200 OK, or 500 on failure)

The endpoint queries row counts from: `project_findings`, `project_unknowns`,
`project_dead_ends`, `mistakes_made`, and `goals`.

#### Example

```bash
curl http://localhost:8000/api/v1/profile/status
```

```json
{
  "ok": true,
  "artifact_counts": {
    "findings": 42,
    "unknowns": 7,
    "dead_ends": 12,
    "mistakes": 3,
    "goals": 15
  },
  "total_artifacts": 79,
  "last_sync": null
}
```

---

### POST /api/v1/profile/sync

Trigger a profile sync operation. This invokes `empirica profile-sync --import-only`
as a subprocess, which fetches notes from external sources and imports them into the
local SQLite database. The subprocess has a 60-second timeout.

**Request body:** None

**Response:** `SyncResponse` (200 OK, or 500 on failure)

#### Example

```bash
curl -X POST http://localhost:8000/api/v1/profile/sync
```

```json
{
  "ok": true,
  "message": "Sync complete",
  "fetched": 5,
  "imported": 3
}
```

---

## Models

### HealthResponse

Response from the health check endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | No | `true` | Whether the daemon is operational |
| `version` | `str` | No | `"0.1.0"` | Daemon version |
| `api_version` | `str` | No | `"v1"` | API version |
| `ollama` | `bool` | No | `false` | Whether Ollama is reachable at `localhost:11434` |
| `claude_mem` | `bool` | No | `false` | Whether Claude memory integration is available |
| `qdrant` | `bool` | No | `false` | Whether Qdrant is reachable at `localhost:6333` |

---

### ArtifactPayload

A single artifact extracted by the Chrome extension.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `str` | Yes | -- | Artifact type: `finding`, `decision`, `dead_end`, `mistake`, or `unknown` |
| `content` | `str` | Yes | -- | Artifact content text |
| `confidence` | `float` | No | `0.5` | Confidence score, range `[0.0, 1.0]` |
| `confidenceTier` | `str` or `null` | No | `null` | Optional tier label (e.g., `"high"`, `"medium"`, `"low"`) |
| `contentHash` | `str` or `null` | No | `null` | Hash for deduplication. When provided, the server checks for existing records with identical content before inserting. |
| `metadata` | `dict` | No | `{}` | Type-specific metadata. Keys used by the server: `impact` (float, for findings/decisions), `whyFailed` (str, for dead_ends/mistakes), `prevention` (str, for mistakes). |

---

### ArtifactImportRequest

Request body for the artifact import endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `artifacts` | `list[ArtifactPayload]` | Yes | -- | List of pre-extracted artifacts from the extension |

---

### ArtifactImportResponse

Response from the artifact import endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | Yes | -- | Whether the import succeeded |
| `imported` | `int` | No | `0` | Number of artifacts successfully stored |
| `duplicates_skipped` | `int` | No | `0` | Number of artifacts skipped due to deduplication |
| `errors` | `list[str]` | No | `[]` | Error messages for individual artifacts that failed to import |

---

### ProfileStatusResponse

Response from the profile status endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | No | `true` | Whether the status query succeeded |
| `artifact_counts` | `dict` | No | `{}` | Map of artifact type label to count (keys: `findings`, `unknowns`, `dead_ends`, `mistakes`, `goals`) |
| `total_artifacts` | `int` | No | `0` | Sum of all artifact counts |
| `last_sync` | `str` or `null` | No | `null` | ISO 8601 timestamp of the last profile sync, or `null` if never synced |

---

### SyncResponse

Response from the profile sync endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | Yes | -- | Whether the sync succeeded |
| `message` | `str` | No | `""` | Human-readable status message |
| `fetched` | `int` | No | `0` | Number of items fetched from external sources |
| `imported` | `int` | No | `0` | Number of items imported into the local database |

---

## Error Handling

All endpoints return standard HTTP error responses on failure:

- **500 Internal Server Error** -- Returned when an endpoint's internal handler raises an
  exception. The response body contains a JSON `detail` field with the error message.

```json
{
  "detail": "Profile sync failed: command not found"
}
```

The `POST /api/v1/artifacts/import` endpoint handles per-artifact errors gracefully:
individual artifact failures are collected in the `errors` list of the response rather
than causing the entire request to fail. Only unexpected top-level exceptions result in
a 500 response.

---

## CORS Configuration

The daemon uses permissive CORS settings since security is enforced at the network layer:

| Setting | Value |
|---------|-------|
| Allowed origins | `*` (all) |
| Allowed methods | `GET`, `POST`, `OPTIONS` |
| Allowed headers | `*` (all) |
