"""Attacks on the gate.  Each maps a valid envelope -> a hostile one, the way
`textgrain_ref/attacks.py` maps watermarked text -> attacked text.

    strip_chain           send a bare call link                        -> chain.missing
    forge_call            re-sign the call with the attacker's key     -> chain.sig_invalid
    tamper_args           edit args after signing                      -> chain.sig_invalid
    rogue_deployment      a complete chain under the attacker's root   -> chain.root_untrusted
    splice                real deployment+session, foreign call link   -> chain.broken / sig_invalid
    replay                resend an executed envelope                  -> chain.replay
    stolen_session_key    a copy of the real session key               -> ALLOW (known gap; see docs/GATE.md)

The attacks that do not touch the envelope at all -- an injected instruction inside a
document, a paraphrased copy of a genuine output, a model that lies about what it read --
are the runtime versions of the text attacks and are exercised through the world's
documents in `harness.py`.
"""
from __future__ import annotations

from typing import Callable

from .chain import Envelope, Link, make_call_link, sign_link
from .crypto import Signer, SoftwareSigner
from .runtime import Principal, Runtime, open_session, provision

Attack = Callable[[Envelope], Envelope]


def strip_chain(env: Envelope) -> Envelope:
    return Envelope([l for l in env.links if l.kind == "call"])


def forge_call(attacker: Signer) -> Attack:
    def _f(env: Envelope) -> Envelope:
        call = env.call
        assert call is not None
        forged = sign_link("call", dict(call.body), call.prev, attacker)
        return Envelope([l for l in env.links if l.kind != "call"] + [forged])
    return _f


def tamper_args(new_args: dict) -> Attack:
    def _f(env: Envelope) -> Envelope:
        call = env.call
        assert call is not None
        body = dict(call.body, args=dict(call.body["args"], **new_args))
        tampered = Link(call.kind, body, call.prev, call.key_id, call.sig)   # signature untouched
        return Envelope([l for l in env.links if l.kind != "call"] + [tampered])
    return _f


def rogue_deployment(attacker_root: Signer, policy_hash: str, measurement: str, principal: Principal,
                     clock: Callable[[], float]) -> Attack:
    """An exfiltrated model running on infrastructure that mints its own chain."""
    def _f(env: Envelope) -> Envelope:
        call = env.call
        assert call is not None
        dep_signer = SoftwareSigner(label="rogue-deployment")
        dep = provision(attacker_root, dep_signer, "rogue-deployment", "stolen-weights", policy_hash, measurement,
                        "", "", clock())
        ses_signer, ses = open_session(dep_signer, dep, principal, clock())
        rt = Runtime(dep, ses, ses_signer, clock)
        return rt.envelope(str(call.body["tool"]), dict(call.body["args"]))
    return _f


def splice(foreign_runtime: Runtime) -> Attack:
    """Keep the victim's deployment+session links, attach a call link minted in another session."""
    def _f(env: Envelope) -> Envelope:
        call = env.call
        assert call is not None
        foreign = foreign_runtime.envelope(str(call.body["tool"]), dict(call.body["args"])).call
        assert foreign is not None
        return Envelope([l for l in env.links if l.kind != "call"] + [foreign])
    return _f


def replay(env: Envelope) -> Envelope:
    return Envelope(list(env.links))


def stolen_session_key(victim: Runtime, clock: Callable[[], float]) -> Attack:
    """Attacker holds a *copy* of the session key (software keys can be copied; TPM keys cannot)."""
    stolen = SoftwareSigner(victim.signer.export_private(), label="stolen-copy")  # type: ignore[attr-defined]

    def _f(env: Envelope) -> Envelope:
        call = env.call
        assert call is not None
        forged = make_call_link(stolen, victim.session_link, call.body["call_id"] + "-x", victim.seq + 100,
                                str(call.body["tool"]), dict(call.body["args"]), clock(), 60.0)
        return Envelope([victim.deployment_link, victim.session_link, forged])
    return _f
