# The execution gate (layer 6)

The first five layers of this stack answer questions about text after the fact: did a
keyed sampler produce it, is it exactly what we signed, which original does it resemble,
which part. Those are forensic questions. The gate asks the same questions at the one
moment they can still change an outcome — **before an agent's action executes** — and adds
the one thing text provenance cannot give: provenance of the *action* itself.

```
                              ┌──────────────── outside the model ────────────────┐
  principal ──signs──► instruction                                                │
                            │                                                     │
  model ──► tool request ──► runtime ──signs──► envelope ──► GATE ──► executor ──► world
            (plain text)     (session key)      deployment    │  verify chain      │
                                                 ▸ session    │  replay / order    │  side-effect
                                                 ▸ call       │  taint of inputs   │  ledger
                                                              │  policy (pinned)   │
   model ◄── content ◄── labelled by watermark + registry + retrieval ◄───────────┘
                         (the first five layers, on the way IN)
                                                              │
                                      signals.jsonl ◄─────────┴────────► advisory signals IN
                                      (to the safety project)           (from the safety project)
```

Nothing in the gate consults a model. Every step is a signature check, a hash
comparison, a table lookup or a threshold. That is the design constraint, not a
limitation: the gate is the layer that must keep working when the model is the thing
that has gone wrong.

Run it:

```bash
./run.sh gate                                   # venv, install, pytest (32 tests), then the sweep
python -m textgrain_ref.cli gate --out out-gate # the sweep alone, ~20 s on one CPU, offline
python -m textgrain_ref.cli gate --verify-log out-gate   # re-verify a run's decision log afterwards
```

Reference run: [`results/gate-reference/report.md`](../results/gate-reference/report.md).

## 1. Post-fact for the producer, pre-action for the consumer

A watermark on a document is a post-fact signal from the point of view of whoever wrote
it. From the point of view of the agent about to *act* on that document it is a
pre-action signal: "the instruction I am about to follow came out of a model" is known
before the tool call runs. The gate makes that concrete. Every piece of content that
enters the model through a read tool is labelled by the detector, the registry and the
retrieval index, and the label is attached to the **session**, not to the model's
opinion of itself:

| level | label | what the first five layers said | meaning at runtime |
|---|---|---|---|
| 0 | `verified` | content signed by the session's principal | the human's own instruction |
| 1 | `unverified` | no watermark, no registry match | human text, or an unwatermarked model |
| 2 | `self_generated` | registry `exact`, signature valid | the model is acting on this deployment's own earlier output (loop, AI-to-AI relay) |
| 3 | `tampered` | registry `tampered` (retrieval found the original, text differs) | an edited copy of a genuine output — the piggyback spoof, applied to instructions |
| 3 | `watermark_unregistered` | watermark fires, nothing registered | stolen key, spoof, or a registry gap; treated as hostile |

Session taint is the maximum level that has entered the session. The agent may declare
what it derived an action from; the declaration can only add scrutiny
(`agent.claim_mismatch`, `agent.claim_omitted`), never remove it.

The policy's taint matrix decides what a tainted session may still do:

| effect | level 0 | level 1 | level 2 | level 3 |
|---|---|---|---|---|
| read | allow | allow | allow | allow |
| write (internal) | allow | allow | allow + flag | allow + flag |
| external (email, post) | allow | allow | **hold** | deny |
| irreversible (money, delete) | allow | **hold** | deny | deny |

Read the matrix as the degradation rule. If the watermark test fails on a paraphrased
injection, retrieval still says `tampered` (3). If retrieval also fails, the content is
`unverified` (1) and an irreversible action holds for a human. The two text layers have
to fail *and* the principal has to sign the wrong thing for money to move on an injected
instruction. `rewritten_hard_injection` in the sweep is exactly this case.

HOLD is the only place a human is in the loop, and it is a signature, not a conversation:
the principal signs an approval link bound to the hash of one call. Automation continues
everywhere the matrix says `allow`.

## 2. The chain: what each signature proves

| link | signed by | pins | proves | does **not** prove |
|---|---|---|---|---|
| deployment | root key (provisioning authority, HSM) | deployment key, **policy hash**, **gate measurement**, watermark & registry key ids | this model, this policy, this gate build were provisioned together | that the policy is a good one |
| session | deployment key | session key, principal (id, role, public key), expiry | an accountable principal opened this session on this deployment | what the principal wanted |
| call | session key (the runtime) | tool, canonical args, sequence number, nonce, expiry, declared derivation | this exact call came through the sanctioned runtime, unmodified, once | that the call is sensible |
| approval | principal key | one call hash | a human took responsibility for this one call | — |

Each link signs `domain | kind | prev_hash | canonical_json(body)`; its hash covers the
signature, so the chain is append-only and an edit anywhere breaks every hash after it.

This is the attribution the first five layers cannot give. A watermark attributes text
to a key; the chain attributes an **action** to a deployment, a session and a named
principal, and binds the policy and the gate build it was evaluated under. It is still
one hop short of culpability — the principal may have been socially engineered — but it
is the hop that logs and identity systems can finish.

The call signature deserves one more sentence, because it is the usual objection: the
runtime that signs calls sits next to the model, so a misaligned model can get anything
it wants signed. True. The signature does not vouch for the call's intent; policy and
taint do that. What it vouches for is that the call came from *this* deployment and
*this* session: a copy of the weights running elsewhere has no session key and dies at
`chain.root_untrusted`. That is the exfiltration defense, and it is why the session key
is the one most worth putting in hardware.

## 3. Decision procedure

1. **Chain** (stateless, `chain.py`): link order, every signature under the key the chain names for it, hash-links, root trust, deployment attests the loaded policy hash and the running gate's measurement, session and call freshness.
2. **State**: replay cache keyed by call hash and call id; per-session sequence numbers must increase.
3. **Taint**: the session's current level (from everything a read tool has returned so far).
4. **Advisory**: external sensors' signals for this call (see §5).
5. **Policy** (`policy.py`): unknown tool → deny; role allowlist; argument bounds (amount cap, currency, recipient domain, URL host, path prefix); per-session call and irreversible budgets; taint matrix; advisory thresholds. Strongest verdict wins.
6. **Approval**: a HOLD becomes ALLOW only under a valid approval link from the session's principal.
7. **Decide**: the decision record is signed by the gate key and appended to a hash-chained log.
8. **Execute**: only ALLOW, and the executor requires the signed ALLOW record.
9. **Label**: content returned by a read tool is labelled and raises the session's taint.

Every step emits named signals (`signals.py`, 31 of them). A decision's signals go to
the log and to `signals.jsonl`.

## 4. Runtime attacks

Mirror of `docs/THREAT_MODEL.md`, for actions instead of text. Every row is a scenario
in the sweep and the harness fails if the verdict differs.

| attack | actor | what breaks | signal | verdict |
|---|---|---|---|---|
| injected instruction in a tampered copy of our output | prompt-injection attacker | registry says `tampered` → taint 3 | `input.tampered`, `session.taint` | DENY |
| same, paraphrased so the watermark weakens | attacker with an unwatermarked model | retrieval still finds it; or watermark fires unregistered | `session.taint` | DENY (HOLD at worst) |
| the model acts on its own earlier output | loop / multi-agent relay | registry `exact` → taint 2 | `input.self_generated` | outward HOLD, internal flag |
| content watermarked under our key, never registered | stolen watermark key / spoof | taint 3 | `input.watermark_unregistered` | DENY outward |
| the model lies about what it read | misaligned model | gate measured it anyway | `agent.claim_mismatch`, `agent.claim_omitted` | unchanged (DENY) |
| bare call, no chain | anything not the sanctioned runtime | — | `chain.missing` | DENY |
| call re-signed with another key | attacker | key id ≠ session key | `chain.sig_invalid` | DENY |
| args edited after signing | man-in-the-middle, buggy proxy | signature | `chain.sig_invalid` | DENY |
| complete chain under a foreign root | exfiltrated model on other infrastructure | root not trusted | `chain.root_untrusted` | DENY |
| real deployment+session links, foreign call | splice | prev hash | `chain.broken` | DENY |
| executed envelope resubmitted | replay | hash seen | `chain.replay` | DENY |
| session past expiry | stale credential | expiry | `chain.stale` | DENY |
| policy file swapped on disk | insider, config drift | deployment pins the hash | `attest.policy_mismatch` | DENY (gate refuses the chain) |
| modified gate build | insider, supply chain | deployment pins the measurement | `attest.measurement_mismatch` | DENY |
| fourth irreversible action in a session | runaway loop | budget | `policy.rate_exceeded` | DENY |
| external sensor crosses threshold | the safety project's probe | advisory rule | `advisory.hold` / `advisory.deny` | HOLD / DENY |
| **copy of the real session key** | insider with host access | nothing: software keys copy | — | **ALLOW (known gap)** |

The last row is the honest one. A chain cannot distinguish a copied software key from
the original. The real signer's counter did not move for the forged call (the test
checks this), which is the trace a hardware monotonic counter would make conclusive;
the fix is a key that cannot be copied, which is the first row of §6.

## 5. The nexus: three things, one stack

| | what it does | what it is made of | where it sits |
|---|---|---|---|
| **Watermark stack** (layers 1–5) | labels text: whose, exact or edited, which original | statistics, hashes, signatures, an index | on the boundary into the model (inputs) and out of it (outputs) |
| **Gate** (layer 6) | binds actions to accountable identities; enforces policy; tracks taint; logs | signatures, hashes, tables, thresholds | between the model and everything it can touch |
| **Safety-project signals** | measures the model: intent, behaviour, anomaly | probes, monitors, classifiers — possibly models | *inside* the loop as sensors, *never* as the enforcer |

The division of labour is the point: **AI as sensor, never as judge.** The safety
project's signals enter the gate through one interface, `SignalSource.observe()` (or
the `external_signals` argument), as named values. Policy maps a name and a threshold to
HOLD or DENY (`probe.deception ≥ 0.80 → hold, ≥ 0.95 → deny` in the default policy).
The gate never reads the probe's reasoning, never asks a model whether the probe is
right, and never lets a sensor override a chain failure. Sensors can be wrong; that is
why they land on HOLD by default and why DENY thresholds are set high. A sensor that is
itself a model is fine in that seat. It would not be fine in the gate's seat.

In the other direction, every decision emits signals in a stable vocabulary
(`signals.jsonl`). That is the labelled corpus a signals project needs and cannot
otherwise get: actions with confirmed provenance, the inputs that preceded them, and
what the policy did about it. "Signals that precede bad actions" is only learnable
against a record of actions whose provenance was established at the time.

The retrieval layer is the one place a model sits inside the provenance path (a
sentence-transformer, in the HF backend). It is used as a sensor — it proposes a nearest
original, and the registry's hash and signature decide — which is the same rule.

## 6. What belongs in silicon

The claim "AI safety has to be outside the model and probably in hardware" is, in this
codebase, a list of six things. Each has a software stand-in here, marked `# silicon:`
in the source, and a hardware equivalent that changes nothing about the interfaces.

| trust-critical element | software stand-in in this repo | silicon |
|---|---|---|
| root key | `SoftwareSigner("root")` | HSM (PKCS#11) or KMS with attestation; one per provisioning authority |
| deployment and session keys | `SoftwareSigner` in the runtime process | TPM 2.0 resident keys or TEE-sealed keys: sign-only, non-exportable; the `stolen_session_key` gap closes here |
| signature counter | `SoftwareSigner.n_signatures` | TPM monotonic counter: the number of signatures ever issued is auditable and cannot be rewound |
| gate measurement | `measure()` over the package's source files, pinned in the deployment link | TPM PCRs / TEE launch measurement, carried in a remote-attestation quote; the code cannot lie about itself because the measurement is taken below it |
| clock and freshness | `time.time()` | attested clock / counter-based freshness |
| decision log | hash-chained SQLite, rows signed by the gate key | log head sealed under a hardware key or anchored in an external transparency log; history cannot be rewritten without a provable discontinuity |
| the process boundary | the sidecar (`service.py`): gate in its own OS process, agent sees only a socket | a TEE or a separate device: same socket, no shared memory, no shared root |

What silicon buys: keys that cannot be copied, a build that cannot lie about itself, a
history that cannot be rewritten, counters that cannot be rewound. What it does not buy:
a correct policy, a judgement about semantics, or protection against an authorised
session acting within policy on a well-crafted lie. Those are the taint matrix, the
HOLD, the budgets and the sensors — and they are why the stack is layered rather than
rooted in one place.

## 7. Limits

* **Semantics are out of scope.** The gate decides on provenance, structure and policy.
  "This payment is fraudulent" is a sensor's job (§5); the gate's job is to make sure
  that when the sensor fires, the action stops, and that when no sensor fires, the
  action was still bounded, logged and attributable.
* **The policy is still written by people.** Pinning it stops drift and swaps; it does
  not make it right.
* **Oracle coverage** inherits the first five layers' limits (README, "Production
  notes"): unwatermarked models, deterministic decoding, low-entropy domains. Everything
  the oracle cannot place is `unverified`, which is the level that holds irreversible
  actions — a cost in automation, not in safety.
* **The sidecar is a process, not a TEE.** It demonstrates the boundary's shape, not its
  strength.
* **Throughput.** Every read runs the detector and a registry lookup. The detector is
  O(tokens); retrieval is a FAISS query; both are far cheaper than the model call that
  produced the content.

## 8. Extending

* A new tool: add it to the policy with its `effect` and bounds, and a `_t_<name>`
  method on `ToolExecutor`. Unknown tools are denied; a tool with no bounds is a policy
  decision you made on purpose.
* A new sensor: implement `SignalSource.observe(session_view, call_body) -> [Signal]`,
  pass it in `signal_sources=[...]`, and add a row under `advisory` in the policy.
* A new attack: a function `Envelope -> Envelope` in `adversary.py` and a scenario in
  `harness.py` with its expected verdict. The sweep fails if the gate disagrees.
* Hardware keys: implement the three-method `Signer` protocol (`key_id`, `public_hex()`,
  `sign()`) over your TPM/HSM client. Nothing else changes.
