# Empirica Examples

Practical examples showing Empirica in action with Claude Code.

## Coming Soon

Examples are being rebuilt for v1.8 to demonstrate real-world usage patterns:

- **Quick Start** — Minimal setup showing the PREFLIGHT → CHECK → POSTFLIGHT loop
- **Code Review Agent** — Uses the noetic firewall to investigate before judging
- **Research Agent** — Structured investigation with findings, unknowns, and dead-ends
- **Token Efficiency Demo** — Same task with and without Empirica, comparing token usage

## In the Meantime

The fastest way to see Empirica in action:

```bash
pip install empirica
empirica setup-claude-code --force
```

Then start a Claude Code conversation. Empirica runs automatically — the statusline shows epistemic state, the Sentinel gates action behind investigation, and artifacts persist across sessions.

See [Plugin README](../empirica/plugins/claude-code-integration/README.md) for what each component does.
