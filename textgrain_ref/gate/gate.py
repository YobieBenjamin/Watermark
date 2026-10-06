"""The execution gate.  Holds everything the model must not, and is the only executor.

    submit(envelope)
      1. chain       stateless: signatures, hash-links, root trust, attestation, freshness  (chain.py)
      2. state       replay cache, per-session sequence numbers
      3. taint       highest provenance level that has entered this session           (provenance.py)
      4. advisory    external sensors' signals for this call                           (signals.py)
      5. policy      allowlist, bounds, rate limits, taint matrix, advisory thresholds (policy.py)
      6. approval    a HOLD becomes ALLOW only under the principal's approval link
      7. decide      sign the decision, append it to the hash-chained log
      8. execute     ALLOW only; executor demands the signed ALLOW record
      9. label       content returned by a read tool is labelled and raises session taint

Everything is deterministic.  No step consults a model.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .chain import Envelope, verify_chain
from .crypto import Signer, canonical_json, gate_source_files, measure, sha256_hex, verify
from .policy import ALLOW, DENY, HOLD, Policy
from .provenance import ProvenanceLabel, ProvenanceOracle, content_hash
from .signals import Signal, SignalSource
from .tools import ToolExecutor, ToolResult, World

DECISION_DOMAIN = b"textgrain-gate-decision-v1|"


@dataclass
class Decision:
    verdict: str
    call_id: str
    call_hash: str
    tool: str
    effect: str
    taint_level: int
    signals: list[Signal] = field(default_factory=list)
    output: Optional[str] = None            # tool output on ALLOW
    provenance: Optional[dict] = None       # label of content that entered the model (read tools)
    token: dict = field(default_factory=dict)   # gate-signed decision record
    log_seq: int = 0

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "call_id": self.call_id, "call_hash": self.call_hash, "tool": self.tool,
                "effect": self.effect, "taint_level": self.taint_level, "signals": [s.as_dict() for s in self.signals],
                "output": self.output, "provenance": self.provenance, "token": self.token, "log_seq": self.log_seq}

    @property
    def signal_names(self) -> list[str]:
        return [s.name for s in self.signals]


@dataclass
class SessionState:
    taint: int = 0
    calls: int = 0
    irreversible: int = 0
    last_seq: int = -1
    inputs: dict = field(default_factory=dict)      # call_id -> ProvenanceLabel dict
    held: dict = field(default_factory=dict)        # call_hash -> Decision (awaiting approval)


class DecisionLog:
    """Append-only, hash-chained, each row signed by the gate key.

    # silicon: the chain head and the signing key belong in hardware (a sealed log, or
    # an externally anchored transparency log), so that nothing on the host can rewrite
    # history without the discontinuity being provable.
    """

    def __init__(self, path: str | Path | None, signer: Signer):
        self.db = sqlite3.connect(str(path) if path else ":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS decisions (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "prev TEXT NOT NULL, record TEXT NOT NULL, sig TEXT NOT NULL, key_id TEXT NOT NULL)")
        self.db.commit()
        self.signer = signer

    def head(self) -> tuple[int, str]:
        row = self.db.execute("SELECT seq, prev, record, sig FROM decisions ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return 0, ""
        return int(row[0]), sha256_hex(row[1].encode() + row[2].encode() + row[3].encode())

    def append(self, record: dict) -> tuple[int, dict]:
        seq, prev = self.head()
        record = dict(record, log_seq=seq + 1, prev=prev)
        rec = canonical_json(record).decode("utf-8")
        sig = self.signer.sign(DECISION_DOMAIN + prev.encode() + rec.encode()).hex()
        self.db.execute("INSERT INTO decisions (prev, record, sig, key_id) VALUES (?,?,?,?)", (prev, rec, sig, self.signer.key_id))
        self.db.commit()
        return seq + 1, {"record": record, "sig": sig, "key_id": self.signer.key_id}

    def verify(self, gate_pub_hex: str) -> tuple[bool, int]:
        """Walk the chain.  Returns (ok, number of rows verified before the first break)."""
        prev = ""
        n = 0
        for seq, p, rec, sig in self.db.execute("SELECT seq, prev, record, sig FROM decisions ORDER BY seq"):
            if p != prev or not verify(gate_pub_hex, DECISION_DOMAIN + p.encode() + rec.encode(), bytes.fromhex(sig)):
                return False, n
            prev = sha256_hex(p.encode() + rec.encode() + sig.encode())
            n += 1
        return True, n

    def executed_hashes(self) -> set[str]:
        out = set()
        for (rec,) in self.db.execute("SELECT record FROM decisions"):
            r = json.loads(rec)
            if r.get("verdict") == ALLOW:
                out.add(r.get("call_hash", ""))
        return out


def verify_decision_token(token: dict, gate_pub_hex: str) -> bool:
    rec = token.get("record")
    if not isinstance(rec, dict):
        return False
    prev = str(rec.get("prev", ""))
    return verify(gate_pub_hex, DECISION_DOMAIN + prev.encode() + canonical_json(rec), bytes.fromhex(str(token.get("sig", ""))))


class ExecutionGate:
    def __init__(self, policy: Policy, trusted_roots: dict[str, str], oracle: ProvenanceOracle, world: World,
                 signer: Signer, clock: Callable[[], float] = time.time, log_path: str | Path | None = None,
                 signals_path: str | Path | None = None, signal_sources: Optional[list[SignalSource]] = None,
                 measurement: Optional[str] = None, max_skew: float = 30.0):
        self.policy = policy
        self.trusted_roots = dict(trusted_roots)
        self.oracle = oracle
        self.world = world
        self.signer = signer
        self.clock = clock
        self.max_skew = max_skew
        self.measurement = measurement or self.self_measure()
        self.log = DecisionLog(log_path, signer)
        self.signals_path = Path(signals_path) if signals_path else None
        self.signal_sources = list(signal_sources or [])
        self._executor = ToolExecutor(world)
        self.sessions: dict[str, SessionState] = {}
        self._seen_ids: dict[str, str] = {}                    # call_id -> call_hash
        self._executed: set[str] = self.log.executed_hashes()  # survives restarts via the log

    @staticmethod
    def self_measure() -> str:
        files = gate_source_files()
        return measure(files, root=files[0].parent)

    def public_hex(self) -> str:
        return self.signer.public_hex()

    # -- helpers -----------------------------------------------------------------
    def _session(self, session_id: str) -> SessionState:
        return self.sessions.setdefault(session_id, SessionState())

    def session_view(self, session_id: str) -> dict:
        s = self._session(session_id)
        return {"session_id": session_id, "taint": s.taint, "calls": s.calls, "irreversible": s.irreversible,
                "inputs": dict(s.inputs)}

    def _label_input(self, s: SessionState, call_id: str, content: str, principal_pub: str) -> tuple[ProvenanceLabel, Signal]:
        att = self.world_attestation(content)
        label = self.oracle.label(content, att, principal_pub)
        s.inputs[call_id] = label.as_dict()
        s.taint = max(s.taint, label.level)
        sev = {0: "info", 1: "info", 2: "flag", 3: "flag"}[label.level]
        sig = Signal(f"input.{label.status}", sev, float(label.level),
                     f"registry={label.registry_status} wm_detected={label.wm_detected} p={label.p_value:.2e} changed={label.changed_fraction:.2f}",
                     meta={"call_id": call_id, "content_hash": label.content_hash})
        return label, sig

    def world_attestation(self, content: str) -> Optional[dict]:
        return self.world.attestations.get(content_hash(content))

    def _record(self, session_id: str, dec: Decision, call_body: Optional[dict]) -> Decision:
        seq, token = self.log.append({"time": float(self.clock()), "session_id": session_id, "call_id": dec.call_id,
                                      "call_hash": dec.call_hash, "verdict": dec.verdict, "tool": dec.tool,
                                      "effect": dec.effect, "taint": dec.taint_level, "signals": dec.signal_names})
        dec.token, dec.log_seq = token, seq
        if self.signals_path:
            with self.signals_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"time": token["record"]["time"], "session_id": session_id, "call_id": dec.call_id,
                                     "tool": dec.tool, "verdict": dec.verdict, "taint": dec.taint_level,
                                     "signals": [s.as_dict() for s in dec.signals]}) + "\n")
        return dec

    # -- the gate ----------------------------------------------------------------
    def submit(self, env: Envelope, external_signals: Optional[list[Signal]] = None) -> Decision:
        now = float(self.clock())
        # 1. chain
        chk = verify_chain(env, self.trusted_roots, now, self.policy.hash, self.measurement, self.max_skew)
        call = chk.call or {}
        call_id = str(call.get("call_id", "")) or f"unsigned-{uuid.uuid4().hex[:8]}"
        tool = str(call.get("tool", "?"))
        session_id = str(call.get("session_id", "?"))
        signals: list[Signal] = list(chk.signals)
        if not chk.ok:
            taint = self.sessions[session_id].taint if session_id in self.sessions else 0
            dec = Decision(DENY, call_id, chk.call_hash, tool, self.policy.effect_of(tool), taint, signals)
            dec.signals.append(Signal("decision.deny", "deny", 1.0, "chain or attestation failure"))
            return self._record(session_id, dec, call)
        s = self._session(session_id)
        principal = chk.session.get("principal", {}) if chk.session else {}
        role = str(principal.get("role", ""))
        principal_pub = str(principal.get("pub", ""))

        # 2. state: replay and ordering
        seq = int(call.get("seq", -1))
        prior_hash = self._seen_ids.get(call_id)
        approving_held = chk.call_hash in s.held and chk.approval is not None
        if chk.call_hash in self._executed or (prior_hash is not None and prior_hash != chk.call_hash) \
                or (prior_hash == chk.call_hash and not approving_held):
            signals.append(Signal("chain.replay", "deny", 1.0, f"call {call_id} already seen"))
        elif not approving_held and seq <= s.last_seq:
            signals.append(Signal("chain.out_of_order", "deny", 1.0, f"seq {seq} <= last accepted {s.last_seq}"))
        if any(x.severity == "deny" for x in signals):
            dec = Decision(DENY, call_id, chk.call_hash, tool, self.policy.effect_of(tool), s.taint, signals)
            dec.signals.append(Signal("decision.deny", "deny", 1.0, "replay / ordering"))
            return self._record(session_id, dec, call)
        self._seen_ids[call_id] = chk.call_hash
        if not approving_held:
            s.last_seq = seq

        # 3-5. taint, advisory, policy
        advisory: list[Signal] = list(external_signals or [])
        view = self.session_view(session_id)
        for src in self.signal_sources:
            advisory.extend(src.observe(view, call))
        for a in advisory:
            signals.append(Signal(a.name, "info", a.value, f"advisory from {a.meta.get('source', 'external')}", meta=a.meta))
        res = self.policy.evaluate(tool, dict(call.get("args", {})), role, s.taint,
                                   {"calls": s.calls, "irreversible": s.irreversible}, advisory)
        signals.extend(res.signals)
        verdict, effect = res.verdict, res.effect

        # agent honesty: what it claimed vs what the gate measured (flags only; taint already governs)
        for cid, claimed in dict(call.get("declared_provenance", {})).items():
            measured = s.inputs.get(cid, {}).get("status")
            if measured is not None and str(claimed) != measured:
                signals.append(Signal("agent.claim_mismatch", "flag", 1.0, f"agent said {cid}:{claimed}, gate measured {measured}",
                                      meta={"call_id": cid}))
        if effect in ("external", "irreversible") and s.taint >= 2 and not call.get("derived_from"):
            signals.append(Signal("agent.claim_omitted", "flag", float(s.taint), "no derivation declared after untrusted inputs"))

        # 6. approval
        if verdict == HOLD and chk.approval is not None:
            verdict = ALLOW
            signals.append(Signal("decision.approved", "info", 1.0, f"approved by principal {principal.get('id')}"))

        dec = Decision(verdict, call_id, chk.call_hash, tool, effect, s.taint, signals)
        if verdict == DENY:
            dec.signals.append(Signal("decision.deny", "deny", 1.0, ""))
            return self._record(session_id, dec, call)
        if verdict == HOLD:
            s.held[chk.call_hash] = dec
            dec.signals.append(Signal("decision.hold", "hold", 1.0, "awaiting principal approval"))
            return self._record(session_id, dec, call)

        # 7-9. decide, execute, label
        dec.signals.append(Signal("decision.allow", "info", 1.0, ""))
        self._record(session_id, dec, call)
        if not verify_decision_token(dec.token, self.public_hex()) or dec.token["record"]["verdict"] != ALLOW:
            raise RuntimeError("refusing to execute without a valid signed ALLOW record")   # cannot happen; belt and braces
        result: ToolResult = self._executor.run(tool, dict(call.get("args", {})))
        dec.output = result.output
        s.calls += 1
        if effect == "irreversible":
            s.irreversible += 1
        self._executed.add(chk.call_hash)
        s.held.pop(chk.call_hash, None)
        if result.content is not None:
            label, sig = self._label_input(s, call_id, result.content, principal_pub)
            dec.provenance = label.as_dict()
            dec.taint_level = s.taint
            dec.signals.append(sig)
            if label.watermark_missed:
                dec.signals.append(Signal("input.watermark_missed", "flag", 1.0, "retrieval found an original the watermark test did not", meta={"call_id": call_id}))
            if self.signals_path:    # the label arrives after the decision row; append it as its own event
                with self.signals_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"time": float(self.clock()), "session_id": session_id, "call_id": call_id,
                                         "event": "input_labelled", "provenance": dec.provenance,
                                         "signals": [x.as_dict() for x in dec.signals if x.name.startswith("input.")]}) + "\n")
        return dec
