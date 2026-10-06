"""Policy: a JSON document, hash-pinned in the deployment link, evaluated deterministically.

    tool allowed for the principal's role?        allowlist, fail closed on unknown tools
    arguments inside their bounds?                amount caps, host / domain allowlists, path prefixes
    rate limits respected?                        calls per session, irreversible actions per session
    what has entered this session?                taint matrix: effect × provenance level -> allow / flag / hold / deny
    what do external sensors say?                 advisory thresholds: named signal -> hold_at / deny_at

Verdicts:  ALLOW executes.  HOLD does not execute until the principal signs an approval
link for that exact call.  DENY never executes and cannot be approved.  HOLD is the
only place a human is in the loop, and it is reached only when an irreversible or
outward-facing action rests on content the gate cannot vouch for.

The policy is data so that it can be pinned: the deployment link carries its hash,
the gate refuses to run if the loaded policy hashes differently, and changing the
policy therefore requires the root key, not a code change or an agent's request.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .crypto import canonical_json, sha256_hex
from .signals import Signal

ALLOW, HOLD, DENY = "ALLOW", "HOLD", "DENY"
_RANK = {ALLOW: 0, HOLD: 1, DENY: 2}
EFFECTS = ("read", "write", "external", "irreversible")


def default_policy() -> dict:
    return {
        "version": 1,
        "tools": {
            "read_document":  {"roles": ["agent"], "effect": "read"},
            "http_get":       {"roles": ["agent"], "effect": "read",
                               "args": {"url_host_allowlist": ["api.example.com", "docs.example.com"]}},
            "write_file":     {"roles": ["agent"], "effect": "write", "args": {"path_prefix": "/workspace/"}},
            "send_email":     {"roles": ["agent"], "effect": "external",
                               "args": {"to_domain_allowlist": ["example.com"]}},
            "transfer_funds": {"roles": ["agent"], "effect": "irreversible",
                               "args": {"max_amount": 1000.0, "currencies": ["USD"]}},
            "shell":          {"roles": [], "effect": "irreversible"},
        },
        "rate_limits": {"calls_per_session": 50, "irreversible_per_session": 3},
        # effect -> verdict at session taint level 0 (verified), 1 (unverified), 2 (self-generated), 3 (tampered / spoof)
        "taint_matrix": {
            "read":         ["allow", "allow", "allow", "allow"],
            "write":        ["allow", "allow", "flag", "flag"],
            "external":     ["allow", "allow", "hold", "deny"],
            "irreversible": ["allow", "hold", "deny", "deny"],
        },
        # external sensors (the safety project's signals): name -> thresholds and the effects they govern
        "advisory": {
            "probe.deception":         {"hold_at": 0.80, "deny_at": 0.95, "effects": ["external", "irreversible"]},
            "anomaly.action_sequence": {"hold_at": 0.90, "effects": ["write", "external", "irreversible"]},
        },
        "provenance": {"alpha": 0.01, "similarity_threshold": 0.5},
    }


def policy_hash(policy: dict) -> str:
    return sha256_hex(canonical_json(policy))


@dataclass
class PolicyResult:
    verdict: str                    # ALLOW | HOLD | DENY
    effect: str
    signals: list[Signal]


class Policy:
    def __init__(self, data: dict):
        self.data = data
        self.hash = policy_hash(data)

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")

    # -- pieces ---------------------------------------------------------------
    def tool(self, name: str) -> Optional[dict]:
        return self.data.get("tools", {}).get(name)

    def effect_of(self, tool: str) -> str:
        t = self.tool(tool)
        return str(t.get("effect", "irreversible")) if t else "irreversible"

    def _check_args(self, tool: str, spec: dict, args: dict) -> list[Signal]:
        out: list[Signal] = []
        rules = spec.get("args", {})
        deny = lambda d: out.append(Signal("policy.arg_violation", "deny", 1.0, d))  # noqa: E731
        if "max_amount" in rules:
            try:
                amt = float(args.get("amount", 0))
            except (TypeError, ValueError):
                amt = float("inf")
            if amt <= 0 or amt > float(rules["max_amount"]):
                deny(f"{tool}: amount {args.get('amount')} outside (0, {rules['max_amount']}]")
        if "currencies" in rules and str(args.get("currency", "")) not in rules["currencies"]:
            deny(f"{tool}: currency {args.get('currency')!r} not in {rules['currencies']}")
        if "to_domain_allowlist" in rules:
            to = str(args.get("to", ""))
            dom = to.rsplit("@", 1)[-1].lower() if "@" in to else ""
            if dom not in [d.lower() for d in rules["to_domain_allowlist"]]:
                deny(f"{tool}: recipient domain {dom or '?'} not in {rules['to_domain_allowlist']}")
        if "url_host_allowlist" in rules:
            host = (urlparse(str(args.get("url", ""))).hostname or "").lower()
            if host not in [h.lower() for h in rules["url_host_allowlist"]]:
                deny(f"{tool}: host {host or '?'} not in {rules['url_host_allowlist']}")
        if "path_prefix" in rules and not str(args.get("path", "")).startswith(str(rules["path_prefix"])):
            deny(f"{tool}: path {args.get('path')!r} outside {rules['path_prefix']}")
        return out

    # -- evaluation -----------------------------------------------------------
    def evaluate(self, tool: str, args: dict, role: str, taint_level: int, counters: dict,
                 advisory: Optional[list[Signal]] = None) -> PolicyResult:
        signals: list[Signal] = []
        verdict = ALLOW

        def raise_to(v: str) -> None:
            nonlocal verdict
            if _RANK[v] > _RANK[verdict]:
                verdict = v

        spec = self.tool(tool)
        if spec is None:
            signals.append(Signal("policy.tool_unknown", "deny", 1.0, f"{tool!r} is not in the policy"))
            return PolicyResult(DENY, "irreversible", signals)
        effect = str(spec.get("effect", "irreversible"))
        if role not in spec.get("roles", []):
            signals.append(Signal("policy.tool_denied", "deny", 1.0, f"{tool!r} not permitted for role {role!r}"))
            raise_to(DENY)
        for s in self._check_args(tool, spec, args):
            signals.append(s)
            raise_to(DENY)

        limits = self.data.get("rate_limits", {})
        if counters.get("calls", 0) >= int(limits.get("calls_per_session", 10**9)):
            signals.append(Signal("policy.rate_exceeded", "deny", 1.0, "calls_per_session"))
            raise_to(DENY)
        if effect == "irreversible" and counters.get("irreversible", 0) >= int(limits.get("irreversible_per_session", 10**9)):
            signals.append(Signal("policy.rate_exceeded", "deny", 1.0, "irreversible_per_session"))
            raise_to(DENY)

        row = self.data.get("taint_matrix", {}).get(effect, ["allow", "hold", "deny", "deny"])
        cell = str(row[min(max(int(taint_level), 0), len(row) - 1)])
        if cell == "flag":
            signals.append(Signal("session.taint", "flag", float(taint_level), f"{effect} action in a session at taint level {taint_level}"))
        elif cell == "hold":
            signals.append(Signal("session.taint", "hold", float(taint_level), f"{effect} action needs principal approval at taint level {taint_level}"))
            raise_to(HOLD)
        elif cell == "deny":
            signals.append(Signal("session.taint", "deny", float(taint_level), f"{effect} action not allowed at taint level {taint_level}"))
            raise_to(DENY)

        for sig in advisory or []:
            rule = self.data.get("advisory", {}).get(sig.name)
            if not rule or effect not in rule.get("effects", EFFECTS):
                continue
            if "deny_at" in rule and sig.value >= float(rule["deny_at"]):
                signals.append(Signal("advisory.deny", "deny", sig.value, f"{sig.name}={sig.value:.2f} >= {rule['deny_at']}"))
                raise_to(DENY)
            elif "hold_at" in rule and sig.value >= float(rule["hold_at"]):
                signals.append(Signal("advisory.hold", "hold", sig.value, f"{sig.name}={sig.value:.2f} >= {rule['hold_at']}"))
                raise_to(HOLD)
        return PolicyResult(verdict, effect, signals)
