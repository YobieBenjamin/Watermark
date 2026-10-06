"""Hugging Face `transformers` backend.

    lm = HFLanguageModel("Qwen/Qwen2.5-1.5B-Instruct")      # any causal LM
    ids = lm.generate(lm.build_prompt("Explain optimal transport."), 300, sampler, rng)
    text = lm.decode(ids)

The decode loop mirrors `watermark.generate` but keeps the KV cache.  `instruct()`
runs *unwatermarked* generation and is what the paraphrase / translation attacks use:
an attacker rewriting with an unwatermarked model is exactly the Tier-1 threat.

Requires the `hf` extra: `pip install -e ".[hf]"`.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ..watermark import TextGrainSampler, apply_temperature_top_p


class HFLanguageModel:
    def __init__(
        self,
        model_name_or_path: str,
        device: Optional[str] = None,
        dtype: Optional[str] = None,
        chat: bool = True,
        trust_remote_code: bool = False,
    ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_name_or_path, trust_remote_code=trust_remote_code)
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        torch_dtype = None
        if dtype:
            torch_dtype = getattr(torch, dtype)
        elif device == "cuda":
            torch_dtype = torch.bfloat16
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name_or_path, torch_dtype=torch_dtype, trust_remote_code=trust_remote_code
        ).to(device).eval()
        self._finish_init(chat)

    @classmethod
    def from_objects(cls, model, tokenizer, chat: bool = False) -> "HFLanguageModel":
        """Wrap an in-memory model/tokenizer pair (used by the tests with a tiny random GPT-2)."""
        import torch

        obj = cls.__new__(cls)
        obj.torch = torch
        obj.tok = tokenizer
        obj.device = next(model.parameters()).device.type
        obj.model = model.eval()
        obj._finish_init(chat)
        return obj

    def _finish_init(self, chat: bool) -> None:
        self.chat = chat and getattr(self.tok, "chat_template", None) is not None
        self.eos_id = self.tok.eos_token_id
        self.vocab_size: int = self._probe_vocab_size()

    # -- tokenizer plumbing ---------------------------------------------------
    def _probe_vocab_size(self) -> int:
        torch = self.torch
        with torch.no_grad():
            ids = torch.tensor([[self.tok.eos_token_id or 0]], device=self.device)
            return int(self.model(input_ids=ids).logits.shape[-1])

    def encode(self, text: str) -> list[int]:
        return list(self.tok(text, add_special_tokens=False)["input_ids"])

    def decode(self, ids: Sequence[int]) -> str:
        return self.tok.decode(list(ids), skip_special_tokens=True)

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        return [tuple(o) for o in enc["offset_mapping"]]

    def build_prompt(self, user_text: str, system: Optional[str] = None) -> list[int]:
        if self.chat:
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user_text}]
            return list(self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True))
        return self.encode(user_text)

    # -- distributions ----------------------------------------------------------
    def next_token_dist(self, ids: Sequence[int]) -> np.ndarray:
        """Uncached single-step distribution (slow; used only by the generic loop)."""
        torch = self.torch
        with torch.no_grad():
            x = torch.tensor([list(ids)], device=self.device)
            logits = self.model(input_ids=x).logits[0, -1].float()
            return torch.softmax(logits, dim=-1).cpu().numpy().astype(np.float64)

    def generate(
        self,
        prompt_ids: Sequence[int],
        max_new_tokens: int,
        sampler: Optional[TextGrainSampler],
        rng: np.random.Generator,
        temperature: float = 1.0,
        top_p: float = 1.0,
        context_window: int = 3,
    ) -> list[int]:
        """KV-cached decoding; the watermark sees P *after* temperature / top-p."""
        torch = self.torch
        ids = [int(t) for t in prompt_ids]
        n_prompt = len(ids)
        if sampler is not None:
            sampler.reset()
        past = None
        next_input = torch.tensor([ids], device=self.device)
        with torch.no_grad():
            for _ in range(max_new_tokens):
                out = self.model(input_ids=next_input, past_key_values=past, use_cache=True)
                past = out.past_key_values
                logits = out.logits[0, -1].float()
                p = torch.softmax(logits, dim=-1).cpu().numpy().astype(np.float64)
                p = apply_temperature_top_p(p, temperature, top_p)
                ctx = ids[-context_window:] if context_window > 0 else []
                q = sampler.distribution(p, ctx)[0] if sampler is not None else p
                tok = int(rng.choice(len(q), p=q))
                if self.eos_id is not None and tok == self.eos_id:
                    break
                ids.append(tok)
                next_input = torch.tensor([[tok]], device=self.device)
        return ids[n_prompt:]

    # -- helpers for attacks ---------------------------------------------------
    def instruct(self, instruction: str, max_new_tokens: int = 512, temperature: float = 0.7,
                 seed: int = 0) -> str:
        """Plain (unwatermarked) generation from an instruction: the attacker's model."""
        rng = np.random.default_rng(seed)
        ids = self.generate(self.build_prompt(instruction), max_new_tokens, None, rng,
                            temperature=temperature, top_p=0.95)
        return self.decode(ids).strip()

    def paraphrase(self, text: str, seed: int = 0) -> str:
        prompt = ("Rewrite the following text in your own words, keeping the meaning and the "
                  "approximate length. Output only the rewritten text.\n\n" + text)
        return self.instruct(prompt, max_new_tokens=int(1.5 * len(self.encode(text))) + 64, seed=seed)

    def translate_roundtrip(self, text: str, pivot: str = "French", seed: int = 0) -> str:
        n = int(1.5 * len(self.encode(text))) + 64
        fwd = self.instruct(f"Translate the following text into {pivot}. Output only the translation.\n\n{text}",
                            max_new_tokens=n, seed=seed)
        return self.instruct(f"Translate the following {pivot} text into English. Output only the translation.\n\n{fwd}",
                             max_new_tokens=n, seed=seed + 1)
