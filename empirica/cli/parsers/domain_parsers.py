"""Domain registry command parsers (A1 — SPEC 1)."""


def add_domain_parsers(subparsers):
    """Add domain registry command parsers."""

    # domain-list
    dl = subparsers.add_parser("domain-list", help="List all loaded domains")
    dl.add_argument("--output", choices=["text", "json"], default="text")

    # domain-show
    ds = subparsers.add_parser("domain-show", help="Show a domain's checklist details")
    ds.add_argument("domain", help="Domain name (e.g., cybersec, default, remote-ops)")
    ds.add_argument("--output", choices=["text", "json"], default="text")

    # domain-resolve
    dr = subparsers.add_parser("domain-resolve", help="Resolve a (work_type, domain, criticality) tuple")
    dr.add_argument("work_type", help="Work type (code, infra, docs, remote-ops, ...)")
    dr.add_argument("--domain", default="default", help="Domain name (default: default)")
    dr.add_argument("--criticality", default="medium", help="Criticality level (low|medium|high|critical)")
    dr.add_argument("--output", choices=["text", "json"], default="text")

    # domain-validate
    dv = subparsers.add_parser("domain-validate", help="Validate all YAML domain files")
    dv.add_argument("--output", choices=["text", "json"], default="text")
