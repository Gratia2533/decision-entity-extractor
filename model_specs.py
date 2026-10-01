"""Production runtime identities and scenario-neutral policy constants."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

PARALLEL_INTRAOP_THREADS = frozenset({1, 2, 4, 8})
DECODER_VERSION = "local-mask-first-ge-unsuppressed-v1"
BM_TOKENIZER_MODEL = "jhu-clsp/mmBERT-base"
BM_TOKENIZER_REVISION = "c5955035435e2bf121cde7f3c8863ef52ff35d82"
RUNTIME_VERSIONS = {
    "torch": "2.9.1+cpu",
    "transformers": "4.56.2",
    "tokenizers": "0.22.2",
    "numpy": "2.5.2",
    "huggingface-hub": "0.36.2",
    "safetensors": "0.8.0",
}
BM_TOKENIZER_FILES = {
    "special_tokens_map.json": (
        636,
        "baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351",
    ),
    "tokenizer.json": (
        17525329,
        "197d4cc5406ee12cc50c8b5511f2393cc32d9db321545979ce041c1199178356",
    ),
    "tokenizer_config.json": (
        46440,
        "1d2f82c1341a79748e00efe82e67690f99d00b3c2a894f2b23128fd9d3519da3",
    ),
}

MODEL_THRESHOLDS = {"CM": 0.04, "BM": 0.05}


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    key: str
    model_id: str
    revision: str
    architecture: str
    text_encoder: str
    license: str
    weight_sha256: str
    weight_size_bytes: int
    max_sequence_tokens: int
    max_span_tokens: int = 30


MODELS = {
    "CM": ModelIdentity(
        key="CM",
        model_id="whoisjones/otter-cross-mmbert",
        revision="8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d",
        architecture="cross_encoder",
        text_encoder="mmBERT",
        license="Apache-2.0",
        weight_sha256="8987080bfc3e6672a75fb19ffe904d39e79ad804eabfda8247a62c347fb024b2",
        weight_size_bytes=1_235_084_300,
        max_sequence_tokens=1024,
    ),
    "BM": ModelIdentity(
        key="BM",
        model_id="whoisjones/otter-bi-mmbert",
        revision="53e10a09bc71a2e45980a7a257233a28305a5777",
        architecture="bi_encoder",
        text_encoder="mmBERT",
        license="Apache-2.0",
        weight_sha256="05c4f718fb9e5871d66b8eb68fc40e17d0d8611c5b8e6252e371662bc0f78c91",
        weight_size_bytes=1_906_302_124,
        max_sequence_tokens=1024,
    ),
}

PINNED_REMOTE_CODE_SHA256 = {
    "collate_fn.py": "1b1fbfe6dffd913272783218a86c2d2e965d43ec1bae37f87152cad1605a386b",
    "configuration_otter.py": "74390931c1ca49b2b1e367d4a1f361fd847f61dc9885d19041ce9cd8905ee3bf",
    "loss.py": "659b6b7c652d13b841e7100221a60316a00cecd67c9fc332e49167c51d0b9864",
    "masks.py": "25bb783bff875d8c714721a774e424e5c0ee6487c6e29b38bd483d4ebbf8f45f",
    "metrics.py": "effb726caa594cc6588baa80b33b30dc8599a2483e50f76c931c356897c38a05",
    "modeling_otter.py": "b80a5fed71a78c986fe42a4b1e9d7b2533b73ca2c133113c851ab05951e2b93d",
}
PINNED_CONFIG_SHA256 = {
    "CM": "01b919ae8b94450f077779f1523d5cdd7df63740aee745c80fbad602c5084ab9",
    "BM": "b1d90c73cd224e0b37577dc623eb74581b067ee59b486b8bb0fdb90b85bf44d0",
}

# Fixed production pins must not be mutated by service instances.

MODELS = MappingProxyType(MODELS)
MODEL_THRESHOLDS = MappingProxyType(MODEL_THRESHOLDS)
RUNTIME_VERSIONS = MappingProxyType(RUNTIME_VERSIONS)
BM_TOKENIZER_FILES = MappingProxyType(BM_TOKENIZER_FILES)
PINNED_REMOTE_CODE_SHA256 = MappingProxyType(PINNED_REMOTE_CODE_SHA256)
PINNED_CONFIG_SHA256 = MappingProxyType(PINNED_CONFIG_SHA256)
