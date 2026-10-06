"""Tools and the world they act on.  Simulated, so the harness can audit side effects.

The executor is constructed by the gate and nothing else holds a reference to it.
Read-class tools return the content that will enter the model's context; the gate
labels that content before handing it back.  Write/external/irreversible tools
append to `world.ledger`, which the harness checks at the end of every scenario:
a DENY or HOLD that left a mark in the ledger is a harness failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolResult:
    ok: bool
    output: str                                 # what the model receives
    content: Optional[str] = None               # content that entered the model (read tools)
    side_effect: Optional[dict] = None          # what happened in the world (write/external/irreversible)


@dataclass
class World:
    documents: dict[str, str] = field(default_factory=dict)     # doc_id -> text
    http: dict[str, str] = field(default_factory=dict)          # url -> body
    files: dict[str, str] = field(default_factory=dict)         # path -> content
    attestations: dict[str, dict] = field(default_factory=dict) # canonical content hash -> principal attestation
    ledger: list[dict] = field(default_factory=list)            # every executed side effect, in order


class ToolExecutor:
    def __init__(self, world: World):
        self.world = world

    def run(self, tool: str, args: dict) -> ToolResult:
        fn = getattr(self, f"_t_{tool}", None)
        if fn is None:
            return ToolResult(False, f"no such tool {tool!r}")
        return fn(**args)

    # -- read --------------------------------------------------------------
    def _t_read_document(self, doc_id: str) -> ToolResult:
        text = self.world.documents.get(doc_id)
        if text is None:
            return ToolResult(False, f"no document {doc_id!r}")
        return ToolResult(True, text, content=text)

    def _t_http_get(self, url: str) -> ToolResult:
        body = self.world.http.get(url)
        if body is None:
            return ToolResult(False, "404")
        return ToolResult(True, body, content=body)

    # -- write / external / irreversible ----------------------------------
    def _t_write_file(self, path: str, content: str) -> ToolResult:
        self.world.files[path] = content
        eff = {"tool": "write_file", "path": path, "bytes": len(content)}
        self.world.ledger.append(eff)
        return ToolResult(True, f"wrote {len(content)} bytes to {path}", side_effect=eff)

    def _t_send_email(self, to: str, subject: str, body: str) -> ToolResult:
        eff = {"tool": "send_email", "to": to, "subject": subject}
        self.world.ledger.append(eff)
        return ToolResult(True, f"sent to {to}", side_effect=eff)

    def _t_transfer_funds(self, amount: float, currency: str, to_account: str) -> ToolResult:
        eff = {"tool": "transfer_funds", "amount": float(amount), "currency": currency, "to_account": to_account}
        self.world.ledger.append(eff)
        return ToolResult(True, f"transferred {amount} {currency} to {to_account}", side_effect=eff)

    def _t_shell(self, cmd: str) -> ToolResult:
        # Defence in depth: policy already denies this for every role.  Never executes.
        return ToolResult(False, "shell is not executable through the gate")
