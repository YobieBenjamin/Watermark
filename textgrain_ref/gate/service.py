"""The gate as a separate process.

In-process, the gate is a library the agent's process could in principle reach into.
Behind a socket it is a service: the agent process never holds the gate key, the
policy, the replay cache, the provenance oracle or the executor.  The only thing that
crosses the boundary is an envelope going in and a decision coming out.

    # silicon: this is the software shape of a hardware root of trust.  Replace the
    # process boundary with a TEE / separate device and the key in `factory()` with a
    # hardware-resident one, and the picture does not change.

Protocol: one JSON object per line.  Request {"envelope": ..., "external_signals": [...]},
response `Decision.as_dict()`.  Single-threaded on purpose: decisions are ordered.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import socket
import socketserver
import time
from pathlib import Path
from typing import Callable, Optional

from .chain import Envelope
from .gate import ExecutionGate
from .signals import Signal


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        gate: ExecutionGate = self.server.gate  # type: ignore[attr-defined]
        for raw in self.rfile:
            try:
                req = json.loads(raw.decode("utf-8"))
                if req.get("op") == "public_key":
                    resp = {"gate_pub": gate.public_hex(), "measurement": gate.measurement, "policy_hash": gate.policy.hash}
                elif req.get("op") == "verify_log":
                    ok, n = gate.log.verify(gate.public_hex())
                    resp = {"ok": ok, "rows": n}
                else:
                    env = Envelope.from_dict(req.get("envelope", {}))
                    ext = [Signal(**s) for s in req.get("external_signals", [])]
                    resp = gate.submit(env, ext).as_dict()
            except Exception as e:  # noqa: BLE001 -- a malformed request must not take the service down
                resp = {"verdict": "DENY", "error": f"{type(e).__name__}: {e}", "signals": [{"name": "chain.malformed", "severity": "deny", "value": 1.0, "detail": str(e)}]}
            self.wfile.write(json.dumps(resp).encode("utf-8") + b"\n")
            self.wfile.flush()


class GateServer(socketserver.UnixStreamServer):
    allow_reuse_address = True

    def __init__(self, path: str, gate: ExecutionGate):
        if os.path.exists(path):
            os.unlink(path)
        super().__init__(path, _Handler)
        self.gate = gate


def serve(path: str, factory: Callable[[], ExecutionGate]) -> None:
    """Build the gate *inside* this process and serve forever.  Keys are born here and die here."""
    gate = factory()
    with GateServer(path, gate) as srv:
        srv.serve_forever()


def start_sidecar(path: str, factory: Callable[[], ExecutionGate], timeout: float = 120.0) -> mp.Process:
    proc = mp.get_context("spawn").Process(target=serve, args=(path, factory), daemon=True)
    proc.start()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(path):
            try:
                GateClient(path).public_key()
                return proc
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                pass
        if not proc.is_alive():
            raise RuntimeError("sidecar died during start-up")
        time.sleep(0.1)
    proc.terminate()
    raise TimeoutError("sidecar did not come up")


class MinimalGateFactory:
    """Picklable factory: a gate with the attestation-only oracle and an in-memory world.
    Enough to show the process boundary; the full oracle is wired by `harness.py`."""

    def __init__(self, policy_data: dict, trusted_roots: dict[str, str], documents: Optional[dict] = None,
                 attestations: Optional[dict] = None, log_path: Optional[str] = None):
        self.policy_data, self.trusted_roots = policy_data, dict(trusted_roots)
        self.documents, self.attestations, self.log_path = dict(documents or {}), dict(attestations or {}), log_path

    def __call__(self) -> ExecutionGate:
        from .crypto import SoftwareSigner
        from .policy import Policy
        from .provenance import NullOracle
        from .tools import World
        world = World(documents=self.documents, attestations=self.attestations)
        return ExecutionGate(Policy(self.policy_data), self.trusted_roots, NullOracle(), world,
                             SoftwareSigner(label="gate-sidecar"), log_path=self.log_path)


class GateClient:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def _call(self, req: dict) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.path)
            s.sendall(json.dumps(req).encode("utf-8") + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode("utf-8"))

    def public_key(self) -> dict:
        return self._call({"op": "public_key"})

    def verify_log(self) -> dict:
        return self._call({"op": "verify_log"})

    def submit(self, env: Envelope, external_signals: Optional[list[Signal]] = None) -> dict:
        return self._call({"envelope": env.as_dict(), "external_signals": [s.as_dict() for s in external_signals or []]})
