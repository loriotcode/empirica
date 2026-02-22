# First-Time Setup Guide

**For new Empirica users**

## 🎯 TL;DR

When you first install Empirica, you get a **completely clean slate**. Your data is isolated from:
- Other users' data
- The Empirica development team's data
- Other projects on your machine

**Just run:** `empirica session-create --ai-id your-name` and you're ready!

---

## 📊 Data Isolation Architecture

### 1. Session Database (`.empirica/sessions/sessions.db`)

**Location:** `<your-git-repo>/.empirica/sessions/sessions.db`

**What happens:**
- ✅ **First session creation**: Empirica auto-creates `.empirica/sessions/sessions.db`
- ✅ **Git isolation**: `.empirica/` is in `.gitignore` (never committed)
- ✅ **Per-repo**: Each git repo gets its own database
- ✅ **Clean slate**: Cloning a repo = empty database

**Example:**
```bash
# First time in your project
cd ~/my-project
empirica session-create --ai-id alice

# Creates: ~/my-project/.empirica/sessions/sessions.db
# Only YOU can see this data!
```

---

### 2. Git Notes (`refs/notes/empirica/*`)

**Location:** `.git/refs/notes/empirica/*`

**What happens:**
- ✅ **Storage**: Epistemic checkpoints stored as git notes
- ✅ **Git isolation**: Stored in `.git/` (local only)
- ✅ **Not pushed by default**: Requires explicit `git push origin refs/notes/*`
- ✅ **Per-repo**: Each repo has separate git notes

**Sharing git notes (optional):**
```bash
# Push your epistemic history to remote
git push origin refs/notes/empirica/*

# Pull someone else's epistemic history
git fetch origin refs/notes/empirica/*:refs/notes/empirica/*
```

**Privacy:** You control whether to share epistemic data!

---

### 3. BEADS Issue Tracking (`.beads/`)

**Location:** `<your-git-repo>/.beads/`

**What happens:**
- ✅ **Config tracked**: `.beads/config.yaml` in git
- ✅ **Database isolated**: `.beads/beads.db` in `.gitignore`
- ✅ **Issues tracked**: `.beads/issues.jsonl` in git (optional)
- ✅ **Per-repo**: Each repo gets separate BEADS database

**Example:**
```bash
# First BEADS command
bd create "My first task" -t task

# Creates: .beads/beads.db (NOT committed to git)
```

---

### 4. Project Auto-Mapping

**How Empirica maps sessions to projects:**

1. **First session in a repo**: Empirica checks git remote URL
2. **Auto-creates project**: Based on `git remote get-url origin`
3. **Links sessions**: All sessions in this repo → this project

**Example:**
```bash
# Your repo: https://github.com/alice/my-app.git
empirica session-create --ai-id alice
# Auto-creates project: "my-app" with UUID abc-123

# Your colleague Bob in same repo
empirica session-create --ai-id bob
# Creates NEW project UUID (different from yours!)
# Each user gets independent project tracking
```

---

## 🚀 First-Time Workflow

### Step 1: Install Empirica

```bash
pip install empirica
```

### Step 2: Navigate to Your Project

```bash
cd ~/your-project
```

### Step 3: Create Your First Session

```bash
# AI-first JSON mode (recommended)
echo '{"ai_id": "your-name", "session_type": "development"}' | empirica session-create -

# Legacy CLI mode
empirica session-create --ai-id your-name
```

**Output:**
```json
{
  "ok": true,
  "session_id": "abc-123-...",
  "project_id": "xyz-789-...",
  "message": "Session created successfully"
}
```

### Step 4: Load Project Context

```bash
empirica project-bootstrap --project-id <YOUR_PROJECT_ID>
```

**First time:** Shows empty project (no findings, goals, etc.)

### Step 5: Start Working with CASCADE

```bash
# PREFLIGHT assessment
empirica preflight-submit <YOUR_SESSION_ID> \
  --vectors '{"engagement": 0.8, ...}' \
  --reasoning "Starting work..."

# Do your work...

# POSTFLIGHT assessment
empirica postflight-submit <YOUR_SESSION_ID> \
  --vectors '{"engagement": 0.9, ...}' \
  --reasoning "Completed work..."
```

---

## 📁 What Gets Created?

### In Your Git Repo

```
your-project/
├── .empirica/              # ✅ Created, ❌ Not in git (.gitignored)
│   ├── config.yaml         # Path configuration
│   ├── sessions/
│   │   └── sessions.db     # YOUR session data (SQLite)
│   ├── identity/           # AI identity keys
│   ├── metrics/            # Performance metrics
│   └── messages/           # Message logs
│
├── .beads/                 # BEADS issue tracking
│   ├── config.yaml         # ✅ In git
│   ├── beads.db            # ❌ Not in git (.gitignored)
│   └── issues.jsonl        # ✅ In git (optional)
│
└── .git/
    └── refs/notes/empirica/  # ❌ Not pushed by default
```

---

## 🔒 Privacy & Security

### What's Private (Never Leaves Your Machine)

- ✅ `.empirica/sessions/sessions.db` - Your session history
- ✅ `.empirica/identity/` - Your AI identity keys
- ✅ `.beads/beads.db` - Your BEADS database
- ✅ `.git/refs/notes/empirica/*` - Your epistemic checkpoints (unless you push)

### What's Shared (In Git)

- ✅ `.beads/config.yaml` - BEADS configuration (no sensitive data)
- ✅ `.beads/issues.jsonl` - Issue tracking (optional, can .gitignore)
- ❌ Nothing else by default

### Sharing Epistemic Data (Optional)

```bash
# Share your epistemic checkpoints with team
git push origin refs/notes/empirica/*

# Pull team member's checkpoints
git fetch origin refs/notes/empirica/*:refs/notes/empirica/*

# View someone else's epistemic journey
empirica session-snapshot --session-id <THEIR_SESSION_ID>
```

---

## 🤔 FAQ

### Q: Will I see the Empirica team's development data?

**A: No!** Data is per-repo. When you clone Empirica's repo:
- `.empirica/` is empty (git-ignored)
- `.git/refs/notes/` is empty (not pushed by default)
- You start with a clean slate

### Q: Will other users see my session data?

**A: No!** Unless you explicitly:
1. Push git notes: `git push origin refs/notes/*`
2. Share your `.empirica/sessions/sessions.db` file (not recommended)

### Q: Can I use Empirica in multiple repos?

**A: Yes!** Each repo gets separate:
- Session database
- Git notes
- Project UUID

### Q: What if I want shared team epistemic data?

**A: Push git notes!**
```bash
# One-time setup: Push notes to remote
git push origin refs/notes/empirica/*

# Team members: Pull notes
git fetch origin refs/notes/empirica/*:refs/notes/empirica/*
```

### Q: Can I delete my session history?

**A: Yes!**
```bash
# Delete local session database
rm -rf .empirica/sessions/

# Delete git notes
git notes --ref=empirica remove <commit-hash>
```

---

## 🎓 Advanced: Custom Data Locations

### Environment Variables

```bash
# Override session DB location
export EMPIRICA_SESSION_DB=~/my-custom-empirica/sessions.db
empirica session-create --ai-id alice

# Override entire .empirica root
export EMPIRICA_DATA_DIR=~/my-custom-empirica
```

### Config File (`.empirica/config.yaml`)

```yaml
version: '2.0'
root: /custom/path/.empirica
paths:
  sessions: sessions/sessions.db
  identity: identity/
  metrics: metrics/
settings:
  auto_checkpoint: true
  git_integration: true
```

---

## 🚀 Next Steps

1. **Read the quickstart:** [01_START_HERE.md](01_START_HERE.md)
2. **CLI guide:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
3. **Understand vectors:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
4. **Setup Claude Code:** `empirica setup-claude-code`

---

## 📞 Need Help?

- **GitHub Issues:** https://github.com/Nubaeon/empirica/issues
- **Documentation:** `docs/`
- **System Prompt:** `.github/copilot-instructions.md` (v4.1 AI-first JSON)

---

**Welcome to Empirica!** 🎉 You're starting with a clean slate and full control over your epistemic data.
