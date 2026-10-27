# SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Yobie Benjamin. Noncommercial use only; see NOTICE and LICENSING.md.
"""Regression test for the adversarial audit of 26 October 2026: "exact" means the same bytes that were registered.
Text that only canonicalizes to the registered text (here, with zero-width characters carrying hidden bits) is reported
as "normalized", not "exact"."""
from textgrain_ref.registry import Registry


def test_exact_means_the_same_bytes(tmp_path):
    reg = Registry(tmp_path / "r.sqlite", Registry.generate_key())
    text = "Transfer approved for account review next week."
    reg.register(text, model="m")
    secret_bits = "1011"
    stego = "".join(ch + ("​" if i < len(secret_bits) and secret_bits[i] == "1" else "") for i, ch in enumerate(text))
    assert stego.encode() != text.encode()
    v = reg.verify(stego)
    assert v.status != "exact" and getattr(v, "verdict", "") != "exact", \
        f"bytes carrying a hidden payload verified as {v.as_dict()}"
