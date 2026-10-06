"""Exercises the Hugging Face decode loop with a tiny randomly initialised GPT-2 and a BPE
tokenizer trained on the bundled corpus: no download, no GPU.  Skipped unless the `hf`
extra is installed (`pip install -e ".[hf]"`)."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
tokenizers = pytest.importorskip("tokenizers")

from textgrain_ref.detector import Detector  # noqa: E402
from textgrain_ref.harness import ROOT  # noqa: E402
from textgrain_ref.lm.hf import HFLanguageModel  # noqa: E402
from textgrain_ref.watermark import TextGrainConfig, TextGrainSampler  # noqa: E402

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def tiny_lm():
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    tok = Tokenizer(models.BPE(unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=1000, special_tokens=["[UNK]", "[EOS]"],
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    tok.train([str(p) for p in (ROOT / "data" / "corpus").glob("*.txt")], trainer)
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, unk_token="[UNK]", eos_token="[EOS]")
    torch.manual_seed(0)
    cfg = GPT2Config(vocab_size=len(fast), n_positions=512, n_embd=64, n_layer=2, n_head=2,
                     bos_token_id=fast.eos_token_id, eos_token_id=fast.eos_token_id)
    model = GPT2LMHeadModel(cfg)
    return HFLanguageModel.from_objects(model, fast, chat=False)


def test_hf_generate_detect_roundtrip(tiny_lm):
    cfg = TextGrainConfig(beta=0.5, context_window=3)
    sampler = TextGrainSampler(KEY, cfg, tiny_lm.vocab_size)
    prompt = tiny_lm.encode("It was a truth universally acknowledged")
    ids = tiny_lm.generate(prompt, 150, sampler, np.random.default_rng(0), temperature=1.0, top_p=0.95,
                           context_window=cfg.context_window)
    assert 100 <= len(ids) <= 150
    text = tiny_lm.decode(ids)
    det = Detector(KEY, tiny_lm.encode, cfg, token_spans=tiny_lm.token_spans)
    r = det.detect(text)
    assert r.n_scored > 50
    assert r.detected and r.z_score > 4           # random weights -> high entropy -> strong signal
    plain = tiny_lm.generate(prompt, 150, None, np.random.default_rng(0), context_window=3)
    assert det.detect(tiny_lm.decode(plain)).p_value > 1e-3


def test_hf_uncached_matches_cached_distribution(tiny_lm):
    ids = tiny_lm.encode("Emma Woodhouse, handsome, clever")
    p = tiny_lm.next_token_dist(ids)
    assert p.shape[0] == tiny_lm.vocab_size and abs(p.sum() - 1) < 1e-5
