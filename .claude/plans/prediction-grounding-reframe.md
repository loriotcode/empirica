# Prediction-Grounding Reframe Plan

**Created:** 2026-04-02
**Goal:** 192a5e8d
**Core insight:** LLM outputs are statistical predictions. Quality depends on grounding evidence. The epistemic framework makes grounding explicit and measurable.
**Split:** AI-facing = prediction mechanics. Human-facing = epistemic vocabulary (unchanged).

---

## Scope Audit

Files that contain AI-facing instructions where the reframe applies:

### Tier 1: System Prompts (AI reads every session)
- [x] `~/.claude/empirica-system-prompt.md` — IDENTITY section (DONE: paragraph added)
- [ ] `docs/human/developers/system-prompts/CLAUDE.md` — Claude model delta
- [ ] `docs/human/developers/system-prompts/QWEN.md` — Qwen model delta
- [ ] `docs/human/developers/system-prompts/GEMINI.md` — Gemini model delta
- [ ] `docs/human/developers/system-prompts/COPILOT_INSTRUCTIONS.md` — Copilot delta
- [ ] Other model deltas (codestral, devstral, mistral, etc.)

### Tier 2: Skills (AI reads on invocation)
- [ ] `empirica-framework` skill — core epistemic workflow
- [ ] `epistemic-transaction` skill — transaction planning
- [ ] `epistemic-persistence-protocol` skill — position-holding under pushback
- [ ] `empirica-constitution` skill — governance decision tree

### Tier 3: Developer Docs (humans read, inform AI behavior design)
- [ ] `docs/architecture/EPISTEMIC_FRAMEWORK.md` or equivalent
- [ ] `docs/guides/SYSTEM_PROMPT_DESIGN.md` or equivalent
- [ ] README / product docs — framing section

### Tier 4: Hook/Sentinel Messages (AI sees in permissionDecisionReason)
- [ ] Sentinel nudge messages — reframe from "you don't know enough" to "predictions ungrounded"
- [ ] CHECK gate messages — "investigate" = "predictions need more grounding"
- [ ] POSTFLIGHT breadth note — "missing artifact types" = "ungrounded prediction domains"

---

## Transaction Plan

### T1: System Prompts (all model deltas)
**Scope:** Add prediction-grounding paragraph to each model delta.
**Approach:** Same core paragraph, adapted per model. Not copy-paste — each model has different calibration context.
**Files:** 5-7 model deltas
**Estimated:** Small, mechanical

### T2: Skills (AI-facing epistemic skills)
**Scope:** Update skill internals where "know" / "understand" language appears.
**Approach:**
- `epistemic-transaction`: PREFLIGHT = "assess prediction groundedness", CHECK = "are predictions grounded enough to act"
- `epistemic-persistence-protocol`: "my prediction is grounded in X" vs "I think X"
- `empirica-framework`: vocabulary section gets prediction-grounding note
**Estimated:** Medium — need to read each skill carefully

### T3: Sentinel/Hook Messages
**Scope:** Reframe sentinel messages from knowledge-centric to prediction-centric.
**Approach:**
- "investigate" → "predictions in this domain need more grounding"
- "proceed" → "predictions grounded sufficiently to act"
- Nudges: "past average" stays, but POSTFLIGHT breadth note reframes
**Estimated:** Small — mostly string changes

### T4: Developer Docs
**Scope:** Add "Prediction Grounding" section to architecture docs.
**Approach:**
- New section explaining the dual-audience framing
- Why collaborative context works (richer signal for predictions)
- Why Empirica uses precise vocabulary (noetic, epistemic, UQ)
**Estimated:** Medium — new content, needs careful framing

---

## Key Principles

1. **Human-facing vocabulary doesn't change.** Epistemics, noetics, UQ, confidence — these are correct and precise.
2. **AI-facing internals get the mechanical context.** "Your outputs are predictions" is not anthropomorphization — it's mechanistic truth.
3. **The reframe is additive, not replacement.** We're adding a paragraph of context, not rewriting the framework.
4. **Collaborative framing stays.** It works because it provides richer prediction context, not because the AI "cares."
