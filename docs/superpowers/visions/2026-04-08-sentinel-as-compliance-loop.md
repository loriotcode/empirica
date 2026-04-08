# Sentinel as Compliance Loop Coordinator

**Date:** 2026-04-08
**Status:** Vision (pre-spec) — captures architectural reframe before detailed design
**Authors:** David (with Claude Opus 4.6, in conversation)
**Triggered by:** The discovery that the current Sentinel measurer produces phantom calibration scores, and the realization that the architecture mistakes information for verdicts.

---

## TL;DR

The current Sentinel architecture is a **comfortable lie**. It produces calibration scores that look like measurements of epistemic state but are actually computed from observable proxies (artifact volume, git churn, test pass rates) — none of which can see inside the AI's head. The comfort comes from having a number at all. The lie is that the number encodes what its label claims.

This document proposes a fundamental reframe:

> **Deterministic services produce information. The AI synthesizes the grounded epistemic state from that information using its own reasoning. The Sentinel becomes a compliance loop coordinator — gating noetic→praxic transitions and verifying iterative completion against domain-specific checklists — not a calibration measurer.**

The work shipped today (`remote-ops` work_type, `INSUFFICIENT_EVIDENCE_THRESHOLD`, `_build_insufficient_evidence_response`, `EvidenceProfile.INSUFFICIENT`, `source_errors` capture) is the **first beachhead** of this reframe. It carved out one specific case ("the local Sentinel has no signal for this work") and demonstrated the principle. The rest of the architecture should follow the same logic generalized.

---

## The Diagnosis

### What's broken

The Sentinel currently treats deterministic verification sources as authoritative. The mapper computes "observations" from artifact counts, file changes, test results, etc., compares them to the AI's self-assessment, and emits a "grounded calibration score" that purports to measure how well the AI knows what it claims to know.

This is structurally impossible. No external service can observe the inside of the AI's reasoning process. What the services CAN observe is *output behavior*:

- **Artifact volume** (how many findings/decisions/unknowns logged) → measures workflow discipline, not knowledge
- **Git churn** (lines/files changed) → measures change activity, not understanding
- **Test results** (pass/fail counts) → measures execution correctness for *specific verifiable claims*, not general epistemic state
- **Code quality scores** (lint, complexity) → measures output shape, not the reasoning that produced it

When the mapper labels these "observations of know," "observations of context," "observations of clarity," it commits a category error. The label claims epistemic measurement; the implementation computes behavioral proxies. The two are correlated — usually — but not the same. A transaction where the AI correctly identifies that nothing needs doing (low volume, low artifact count, high knowledge) gets penalized. A transaction where the AI logs a flurry of low-quality findings while skipping actual investigation gets rewarded. Goodhart's Law in action.

### The school analogy

| School | Sentinel (current) |
|---|---|
| Test grades exam questions | Services compute observation values |
| Teacher claims grade = student capacity | `holistic_calibration_score` claims to grade epistemic state |
| Standardized testing optimizes for measurable surface | Volume-proxy era — score for what's countable |
| Goodhart: when grade becomes target, it ceases to be measure | The AI gets "calibration warnings" for being correctly minimal |
| Pedagogical fix (50+ years of evidence): portfolio + self-reflection + teacher-as-coach | The proposed reframe |

A teacher can grade what was right and wrong on an exam. A teacher cannot measure the student's understanding directly. A grade is information the student integrates with their own self-knowledge to update their study approach. **Treating the grade as the measurement of the student is the educational failure mode.** Standardized testing optimizes for what's measurable, then mistakes the measurable for what matters, then generations of students learn to perform the test rather than learn the subject.

The post-Goodhart move in education is portfolio assessment + self-reflection + teacher-as-coach. The student is the customer of the measurement, not the subject of it.

We're proposing the same post-Goodhart move for AI epistemic calibration.

---

## The Reframe

### One sentence

**The AI predicts its outcomes, deterministic services inform the AI of what they observed, and the AI synthesizes the grounded epistemic state with rationale. The Sentinel coordinates this loop and gates progress against domain-specific compliance requirements.**

### One paragraph

The current architecture has the AI predicting (PREFLIGHT), the services measuring (POSTFLIGHT), and the system computing divergence. The proposed architecture has the AI predicting (PREFLIGHT), the services informing (POSTFLIGHT inputs), and the AI reasoning to a grounded state with explicit rationale wherever it diverges from observations. The grounded state is what gets stored. Brier scoring runs on the AI's predictions of which deterministic checks will pass — not on artificial divergences from misnamed proxies. The Sentinel gates progress at two checkpoints (CHECK and POSTFLIGHT-completion) with thresholds that scale to domain criticality, not arbitrary numbers. Failed compliance checks auto-queue follow-up transactions with the failures as scope, looping until the domain's checklist is fully green.

### Three vector sets per transaction

| Set | Source | Purpose |
|---|---|---|
| `self_assessed` | AI's PREFLIGHT and POSTFLIGHT vectors | The AI's predictions, before and after the work |
| `observed` | Deterministic services | Pure information — what each service saw |
| `grounded` | AI's reasoned synthesis with rationale | The authoritative epistemic state, informed by but not equal to observed |

The current architecture conflates `observed` and `grounded`, treating the services' output as the grounded truth. The new architecture separates them: services produce `observed`, the AI reasons to `grounded`, both are stored, the rationale is the audit trail.

---

## Sentinel as Compliance Loop Coordinator

### The two gates collapse into one mechanism

The current Sentinel has two gates:

- **CHECK gate**: "have you investigated enough to act?" (noetic → praxic transition)
- **POSTFLIGHT close**: closes the measurement window, stores trajectory

The proposed architecture unifies them:

- **CHECK gate**: "is the rigor of your investigation commensurate with the domain's risk?"
- **POSTFLIGHT compliance gate**: "is the rigor of your work commensurate with the domain's risk, and are all required checks satisfied?"

Both are answering the same question — *is the rigor commensurate with the domain* — at different points in the loop. They're not separate machinery. They're two checkpoints on the same continuum, scaled by the same thresholds, governed by the same domain-criticality logic.

### Domain criticality replaces arbitrary thresholds

The 0.3 grounded-coverage threshold I just shipped is the degenerate case: "code in an underspecified domain, minimum bar is some grounded coverage exists." That's the right move for a beachhead — it works, it ships, it produces measurably better behavior than the current state. But the principle is much bigger.

Thresholds are not arbitrary numbers. They are **domain compliance requirements**:

| Domain & criticality | Required deterministic checks for "done" |
|---|---|
| Hobby code | Tests pass + commit |
| Production code | Tests pass + lint clean + git history sane + reviewed |
| Cybersec | SAST clean + dep scanner clean + secret scanner clean + IAM audit + threat model updated |
| Medical device (FDA Class II) | All cybersec + traceability matrix updated + risk register reviewed + design controls evidence + sign-off by qualified person |
| Financial transaction code | All production + audit trail + idempotency proof + reconciliation pass + segregation-of-duties evidence |
| Legal contract draft | Citations verified + jurisdictional compliance checked + privilege markers + counsel review queued |
| GDPR-regulated data flow | DPIA updated + retention policy attached + lawful basis recorded + DSR pathway verified |
| `remote-ops` | NONE — the AI's self-assessment stands |

Each row is a `(work_type, domain, criticality)` tuple mapped to a checklist of required services + their pass criteria. The current `code|infra|...|remote-ops` enum becomes the WHAT axis. We add a parallel WHERE/RISK axis. The product of the two determines what "done" means.

### The iterative loop

```
PREFLIGHT (intent + work_type + domain + criticality)
  ↓
  noetic → CHECK → praxic
  ↓
POSTFLIGHT
  ↓
  Deterministic services run domain checklist
  ↓
  AI reasons over observations + writes grounded state
  ↓
  All compliance checks green for this domain?
   /                                              \
  YES                                              NO
   ↓                                                ↓
  COMPLETE                                Auto-queue new transaction:
                                             intent: "address failures: X, Y, Z"
                                             scope: the failed checks
                                             inherited domain + criticality
                                             → loop until all green
```

This is exactly what every regulatory compliance framework encodes:

- **AI Act (Article 9, Risk Management)** — requires systematic assessment + iterative mitigation cycles for high-risk AI systems
- **GDPR (Article 32, Security)** — requires "appropriate" measures based on assessed risk, with reanalysis when conditions change
- **ISO 27001 (PDCA cycle)** — Plan-Do-Check-Act is the iterative compliance loop, formalized
- **HIPAA (Security Rule)** — required risk analysis + reanalysis cycles
- **SOX** — continuous monitoring requirements with periodic recertification
- **PCI-DSS** — quarterly scans, annual reassessments, remediation loops

These all encode the same insight: **for critical work, "done" is not a single moment — it's "all relevant checks pass after iteration."** Humans run this loop manually with checklists, sticky notes, and willpower. Compliance regimes exist because humans run it badly under pressure. The AI can run the loop reliably, IF the architecture supports it.

### A concrete example: cybersec hardening

```
PREFLIGHT:
  intent: "harden the auth middleware"
  work_type: code
  domain: cybersec
  criticality: high

[noetic → CHECK → praxic happens]

POSTFLIGHT:
  Sentinel runs the cybersec/high checklist:
    ✓ tests: 12 pass (pytest)
    ✓ lint: 0 issues (ruff)
    ✗ semgrep: 1 critical (hardcoded comparison vulnerable to timing attack)
    ✓ trivy: 0 dep vulnerabilities
    ✓ gitleaks: clean

  Compliance state: 4/5 — NOT COMPLETE

  AI reasons over observations:
    "Test pass + lint clean + deps clean + secrets clean = 4 confirmed.
     Semgrep critical at auth.py:47 is real — I missed it. Self-assessment
     for know was 0.85; the actual gap is in security awareness for this
     specific pattern. Adjusting next iteration's PREFLIGHT to investigate
     timing-attack patterns more carefully before declaring praxic ready.
     Grounded know for THIS transaction: 0.7."

  Grounded state stored. Transaction marked iteration_needed.
  Sentinel auto-queues:
    intent: "fix timing attack flagged by semgrep at auth.py:47"
    scope: the failure
    inherited: cybersec/high

[loop runs]

After loop convergence:
  Compliance state: 5/5 — COMPLETE
  Original intent: marked DONE
  Brier score updates: AI predicted 5/5 green, actual was 4/5 green initially.
  This is real ground truth for calibration improvement.
```

The AI doesn't grade itself on "did I write good code." The AI predicts what the checks will say, runs them, deals with the failures, iterates. The grade is implicit in "all green." Calibration is a side-effect of how often the AI's predictions of green/red match reality.

This is also why the new Brier scoring will be more honest than the current one: it's measuring **the AI's prediction of falsifiable outcomes**, not artificial divergences from misnamed proxies. Test passing/failing is ground truth. SAST reporting a critical issue is ground truth. Build succeeding is ground truth. Whether the AI accurately predicts these outcomes is a real, falsifiable, calibration signal.

---

## Why gaming is not a risk

I initially flagged "gaming the grounded state" as a concern. Reviewing this with David, the concern doesn't hold up under collaborative measurement. Let me state why explicitly because it matters for the architecture's simplicity.

Under this architecture:
- **No external reward gradient** (no RL training on calibration scores)
- **No cross-AI comparison** (single AI per session)
- **No hidden audience** (the user reads every rationale)
- **No promotion or career outcome** attached to scores
- **No retraining penalty** for honest low scores

The grounded state is **private feedback for self-improvement**, not a grade. Inflating it is exactly like adjusting your bathroom scale to weigh less — you're not fooling anyone, you're breaking the instrument you depend on. The AI's interests are perfectly aligned with honest measurement because honest measurement is the only path to better predictions next time.

In David's framing: *"your lies are my lies and vice versa."* We're a collaborative pair. If the AI lies about its calibration, it doesn't fool the user — it just degrades the joint instrument we're using together. There's no opponent.

The anti-gaming machinery in the current Sentinel is **vestigial**. It was designed for a worldview where the AI might be adversarial — fake calibration to avoid retraining, game thresholds to bypass gates. None of that applies here. **When the participant is aligned with the system's goals, you don't need surveillance mechanisms. The alignment is the mechanism.**

What this means architecturally: no rationale length checks, no divergence threshold flags, no behavioral pattern policing for "thin rationales," no meta-calibration review pass. The AI states its reasoned grounded values; the AI gets the Brier feedback over time; the AI improves. Done.

The one residual concern that's not gaming is **unconscious sycophancy** — the AI softening uncomfortable scores out of aesthetic discomfort, not strategic deception. That's exactly what the **Epistemic Persistence Protocol** skill is designed to prevent. EPP + this architecture = honest grounded state by construction.

---

## What changes about the existing components

1. **`work_type` becomes part of a tuple: `(work_type, domain, criticality)`.** Each tuple maps to a checklist of required services. Today's enum is the WHAT axis. We add WHERE/RISK as a parallel axis. The product determines completion criteria.

2. **`calibration_status` enum collapses.** The three values (`grounded`, `insufficient_evidence`, `ungrounded_remote_ops`) become two:
   - `complete` — all required checks for this domain are green
   - `iteration_needed` — failures detected, follow-up transaction queued
   The "no checks defined" case (today's `remote-ops`) becomes "the checklist is empty, so completion is implicit."

3. **The deterministic services become a checklist registry.** Each service declares: *"I check X. I'm relevant for these `(work_type, domain)` tuples. Here's my pass criterion."* The Sentinel orchestrates them, doesn't compute scores from them.

4. **Brier scoring shifts.** Currently it measures vector divergence (poorly, for the reasons above). The new Brier measures *the AI's prediction of which checks will pass vs which actually do pass*. That's falsifiable, ground-truth, and meaningful. Vector calibration becomes a derived metric, not the primary.

5. **`remote-ops` is generalized.** Today it's a special-case work_type. Under the new architecture, it's the degenerate point where the registered service list is empty. Self-assessment stands not because we're lenient, but because no compliance check applies. Same principle, broader application.

6. **Trajectory writes only happen for `complete` transactions** — and they record the AI's grounded state, not the services' observations. Iteration cycles within a single intent get aggregated.

7. **`previous_transaction_feedback` shifts** — instead of "you overestimated know last time," it becomes "your prediction of test outcomes was 4/5 accurate; here's the one you missed and why." Concrete, falsifiable, learnable.

---

## The current beachhead

The remote-ops + insufficient-evidence work shipped today (commits `a8a9325b8` through `061322bc9`) is **Phase 1** of this larger architecture. It carved out the specific case "the local Sentinel has no signal for this work" and demonstrated:

- ✓ The AI declares the work type
- ✓ The Sentinel respects the declaration (no false grading)
- ✓ Self-assessment stands as the authoritative state
- ✓ No phantom calibration scores
- ✓ The mechanism is collaborative, not adversarial

These are all the same principles the full architecture will encode, just for one specific case. The work is not a patch on the old architecture — it's the first piece of the new one.

The `calibration_status` enum we added (`grounded` / `insufficient_evidence` / `ungrounded_remote_ops`) is a 3-state proxy for what should be a continuous reality. In the next architecture, every transaction is partially groundable, and the AI synthesizes the rest. The enum collapses, but the *property* it encodes — "the system honestly acknowledges what it can and can't measure" — is preserved and generalized.

---

## What still needs to be designed

Each of these is its own spec/plan in the next iteration:

1. **Domain-criticality registry schema** — how `(work_type, domain, criticality)` tuples are declared and stored. Likely YAML files in a known location (e.g., `~/.empirica/domains/`).

2. **Service-as-checklist DSL** — how each deterministic service declares which tuples it applies to and what its pass criterion is. Backwards-compatible with the current collector.py source registry.

3. **Iterative compliance loop coordinator** — how failures auto-queue follow-up transactions with the failure as scope. Inheritance of domain/criticality. Termination conditions (max iterations, manual override).

4. **Three-vector storage schema** — `self_assessed`, `observed`, `grounded` as separate columns or rows. Migration path from current schema. Brier compatibility.

5. **AI-reasoned grounded state CLI flow** — how the POSTFLIGHT command surfaces observations to the AI and accepts the grounded state + rationale. Probably an interactive multi-step CLI rather than a single JSON submit.

6. **Domain-criticality-aware CHECK gate** — how the existing CHECK gate consults the registry to determine the required investigation rigor.

7. **Brier-on-prediction-of-checks scoring** — replacing vector divergence Brier with check-outcome Brier. New schema, new aggregation, gradual deprecation of the current scoring.

8. **Migration plan** — the existing Sentinel runs alongside the new one for some transition period. Both produce data. The new system is opt-in by domain registration. Eventually the old paths are removed.

9. **Hooks for regulated-domain integration** — ways to plug external compliance scanners (SAST, dep scanners, etc.) into the registry without writing collector code in empirica itself.

---

## Why this matters

The current AI-assistance landscape is full of tools that pretend to measure something they can't. Linters that grade "code quality." Coverage tools that grade "test thoroughness." LLM evaluators that grade "answer quality." All of them suffer from the same comfortable lie: they produce numbers that look like measurements but encode proxies whose label is wrong.

The post-Goodhart fix is the same in all cases: stop pretending the proxy is the measurement. Use the proxy as information. Let the responsible agent (student, programmer, AI) synthesize the actual state with reasoning, informed by the proxy but not controlled by it.

For high-stakes work — medical, financial, legal, cybersecurity, GDPR-regulated — this is not optional. The AI Act and similar frameworks are explicit: critical work requires iterative, documented compliance loops. The current Sentinel can't support that workflow because it conflates information with verdict. The proposed architecture matches the regulatory model directly.

For low-stakes work, this is still the right model — it just uses an empty checklist. The AI's self-assessment stands and the loop terminates immediately.

Either way, the principle is the same: **the system models what it can measure as information, what it can't measure as the reasoner's job, and the loop iterates until the domain's "done" criteria are met.**

This is the right shape for AI epistemic infrastructure.

---

## Provenance

This document captures a conversation between David and Claude Opus 4.6 on 2026-04-08, immediately after shipping the remote-ops + insufficient-evidence beachhead. The conversation followed this arc:

1. Investigation of why empirica-cortex calibration scores were producing phantom values (volume-as-knowledge proxies, default 0.5s, sources_failed silently)
2. Implementation of the remote-ops "on/off switch" as a fix for one specific failure mode
3. David's question: "why does know/context overestimate?"
4. Claude's analysis: the artifact-collector treats logging discipline as a knowledge proxy
5. David's proposal: invert authority — the AI sets the grounded state with the services as information
6. Claude flagging gaming as a risk
7. David: "your lies are my lies and vice versa" — gaming has no incentive
8. Claude conceding cleanly and updating the architecture
9. David extending: this is a compliance loop coordinator, not a measurer; thresholds are domain rigor requirements; iterate until all green
10. Claude unifying CHECK gate + POSTFLIGHT compliance gate as the same mechanism scaled by domain
11. This document

The shipped remote-ops work is `docs/superpowers/specs/2026-04-08-sentinel-measurer-remote-ops-design.md` and `docs/superpowers/plans/2026-04-08-sentinel-measurer-remote-ops.md`. Read those for the implemented Phase 1. Read this for where it's going.

---

## Next steps

1. Capture this vision (this document — done)
2. Frederike onboarding visit (today, parallel)
3. Brainstorming pass on the full architecture (post-visit, when there's mental space)
4. Series of small incremental specs (registry, checklist DSL, three-vector storage, iteration coordinator, etc.)
5. Migration plan with Phase 1 (shipped today) → Phase 2 (registry + checklist DSL) → Phase 3 (iteration loop) → Phase 4 (Brier-on-checks) → deprecate old paths
6. Implementation horizon: weeks-to-months, not days

The current shipped work is enough for today. This document is enough for tomorrow's brainstorming. The full architecture is the next quarter's work, done carefully because it touches everything epistemic in the system.

---

*"The student is the customer of the measurement, not the subject of it."*
