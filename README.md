# Canonical Entity Resolution

[繁體中文說明](README.zh-TW.md)

This repository is a clone, customization, and run framework for canonical entity
resolution. It extracts typed, span-grounded entities from raw text.
The caller provides entity names and descriptions. CM/BM candidate probes use the
descriptions; TypeSafe criteria use each label name and description plus the rejection
choice. The package does not assume a business domain, resolve catalog identifiers,
or normalize the caller's text.

The package includes flat packaging, explicit artifact provisioning, the CLI/API
transports, and the retained CM/BM plus TypeSafe pipeline. Runtime never downloads
models automatically. The TypeSafe live classification path needs a TYPESAFE_API_KEY;
local Otter download, verification, loading, warmup, and candidate proposal have
been verified on the pinned artifacts.

## Architecture and boundaries

~~~text
raw text
  → pinned local CM/BM candidate proposal
  → schema-derived TypeSafe decision questions
  → raw-probability confidence filtering and same-label containment NMS
  → gap recovery from the same decision pool
  → surface-preserving annotation and provenance/conflict projection
  → EntityResolutionResult
~~~

The root packages have one clear ownership boundary:

| Package | Responsibility |
| --- | --- |
| contracts | Immutable request/result/schema models and generic proposer/provider interfaces. |
| resolution | Selection, recovery, annotation, provenance, and conflict rules. |
| runtime | Resolver facade, lifecycle, readiness, cache, single-flight, admission, and telemetry. |
| adapters/otter | Candidate model runtime, alignment, artifact validation, and provisioning. |
| adapters/typesafe | TypeSafe wire construction, strict response validation, and provider lifecycle. |
| transport | CLI and injected FastAPI application. |
| bootstrap.py | Explicit production composition root. |

The candidate adapter uses fixed CM and BM model assumptions. They are intentionally
not an arbitrary plug-in model registry. The TypeSafe adapter is behind the generic
DecisionProvider interface, and the API receives an injected resolver; the transport
does not take ownership of service shutdown.

The retained postprocessing policy uses raw decision probabilities. It keeps scores
at or above 0.90, suppresses same-label contained spans with the frozen containment
rule, then recovers candidates at or above 0.80 only inside eligible uncovered runs
of at least two characters. Recovery reuses the same decision pool and performs no
additional inference. Standalone lexical operator cues protect gaps; they do not
infer application operators or preferences.

## First checkout and locked environment

Python 3.12 and 3.13 are supported. The `otter` extra pins CPU wheels for Linux
(`x86_64`/`aarch64`) and Windows (`AMD64`/`ARM64`); use a platform with a matching
wheel for local candidate inference. The verification described below ran on Linux
`x86_64`; the current extra does not pin a macOS torch wheel. From a fresh clone, create the environment,
activate it, and then install the locked extras:

~~~bash
git clone https://github.com/Gratia2533/decision-entity-extractor.git
cd decision-entity-extractor
uv venv --python 3.13
source .venv/bin/activate
uv sync --frozen --group dev --extra api --extra otter --extra decision
~~~

Install uv using the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).

The extras are:

| Extra | Purpose |
| --- | --- |
| otter | Pinned local CPU candidate-model runtime. |
| decision | TypeSafe SDK 0.7.0 and HTTP client. |
| api | FastAPI and Uvicorn hosting. |

The lock includes the runtime pins used by the candidate models: torch 2.9.1+cpu,
transformers 4.56.2, tokenizers 0.22.2, numpy 2.5.2, huggingface-hub 0.36.2,
and safetensors 0.8.0. The configured runtime loads CPU float32 models. No .env
file is loaded automatically; export TYPESAFE_API_KEY explicitly when using the
decision provider.

Core imports do not load model weights, import optional ML/provider SDKs, contact
the network, or create runtime workers. The TypeSafe key is not needed for
identities, help, or download.

## Schema and label customization

One EntitySchema supplies label descriptions to the CM/BM candidate probes, and label
names plus descriptions to the TypeSafe criteria. Names must be unique, nonblank, and free of surrounding
whitespace or line breaks. Descriptions must contain non-whitespace text. The
rejection label must differ from every entity label, and schema order provides
deterministic tie breaking.

~~~python
from contracts.models import EntityLabel, EntitySchema

schema = EntitySchema(
    labels=(
        EntityLabel(name="PERSON", description="A named person"),
        EntityLabel(name="ORGANIZATION", description="A named organization"),
        EntityLabel(name="PLACE", description="A named geographic place"),
    ),
    rejection_label="NOT_ENTITY_OR_MIXED",
)
~~~

The neutral example is also in config/schema.example.json. Change the schema before
constructing a service; the schema identity and TypeSafe prompt hash then change.
The current CM/BM candidate architectures, revisions, and file allowlists remain
fixed when labels change.

The equivalent JSON configuration is:

~~~json
{
  "labels": [
    {"name": "PERSON", "description": "A named person"},
    {"name": "ORGANIZATION", "description": "A named organization"},
    {"name": "PLACE", "description": "A named geographic place"}
  ],
  "rejection_label": "NOT_ENTITY_OR_MIXED"
}
~~~

Each label requires a unique name and a non-whitespace description. The rejection
label is used for non-entities, mixed labels, and broken boundaries; it cannot
duplicate an entity label. Descriptions are sent to both candidate probes and TypeSafe
criteria; label names and the rejection label are TypeSafe choices, not Otter probe
text. A schema change requires a new service composition so its prompt and schema
identities remain explicit.

## Models, revisions, files, and integrity

The candidate runtime uses these exact Apache-2.0 checkpoints:

| Key | Architecture | Model | Revision | Weight size | Weight SHA-256 | Threshold |
| --- | --- | --- | --- | ---: | --- | ---: |
| CM | cross encoder | whoisjones/otter-cross-mmbert | 8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d | 1,235,084,300 bytes | 8987080bfc3e6672a75fb19ffe904d39e79ad804eabfda8247a62c347fb024b2 | 0.04 |
| BM | bi encoder | whoisjones/otter-bi-mmbert | 53e10a09bc71a2e45980a7a257233a28305a5777 | 1,906,302,124 bytes | 05c4f718fb9e5871d66b8eb68fc40e17d0d8611c5b8e6252e371662bc0f78c91 | 0.05 |

Both use mmBERT and require max sequence length 1024 and max span length 30.
BM additionally uses jhu-clsp/mmBERT-base tokenizer revision
c5955035435e2bf121cde7f3c8863ef52ff35d82. Its required tokenizer files are:

| File | Size | SHA-256 |
| --- | ---: | --- |
| runtime_tokenizer/special_tokens_map.json | 636 | baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351 |
| runtime_tokenizer/tokenizer.json | 17,525,329 | 197d4cc5406ee12cc50c8b5511f2393cc32d9db321545979ce041c1199178356 |
| runtime_tokenizer/tokenizer_config.json | 46,440 | 1d2f82c1341a79748e00efe82e67690f99d00b3c2a894f2b23128fd9d3519da3 |

Each checkpoint also requires the six pinned remote-code files:
collate_fn.py, configuration_otter.py, loss.py, masks.py, metrics.py, and
modeling_otter.py; config.json; model.safetensors; and token_encoder_config.json.
CM additionally requires tokenizer.json, tokenizer_config.json, and
special_tokens_map.json. BM requires type_encoder_config.json plus
token_tokenizer/tokenizer.json, token_tokenizer/tokenizer_config.json,
type_tokenizer/tokenizer.json, and type_tokenizer/tokenizer_config.json. The
provisioner copies only these allowlisted regular files and writes a manifest after
full validation.

The combined weight bytes are about 3.14 GB; that is not a minimum RAM claim.
Startup checks manifest identity, required files, sizes, tokenizer metadata,
regular-file boundaries, and symlink rules. Startup trusts declared weight identity
and reports TRUSTED_SOURCE_NOT_HASHED; the explicit check command hashes current
bytes with FULL_SHA256.

## Download and verify

Provisioning uses the existing huggingface_hub cache and resume behavior. It stages
files outside the final checkpoint directory, copies Hub symlink targets as regular
files, validates the complete manifest, and publishes only after validation:

~~~bash
entity-resolver identities
entity-resolver download --artifact-root .local-artifacts
entity-resolver check --artifact-root .local-artifacts
~~~

The final paths are derived from model IDs and revisions:

~~~text
.local-artifacts/whoisjones--otter-cross-mmbert/8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d/
.local-artifacts/whoisjones--otter-bi-mmbert/53e10a09bc71a2e45980a7a257233a28305a5777/
~~~

The command reuses a healthy target without downloading. An existing damaged target
is reported and preserved; it is never silently replaced. A failed or interrupted
staging operation does not publish a valid final manifest and leaves the Hub cache
untouched. A later BM failure can leave an already completed CM target reusable.
There is no bespoke cross-process download lock.

Provisioning and Otter inference use local artifact files. During a real decision
request, the raw text, candidate spans, schema criteria, and bounded context are
sent to TypeSafe; credentials and raw provider bodies are not written to public
metadata or completion telemetry. Do not send sensitive text unless that transfer
is acceptable for your deployment.

## CLI

The installed console script is entity-resolver. Help and identities do not require
artifacts or a TypeSafe key:

~~~bash
entity-resolver --help
entity-resolver identities
~~~

After provisioning and exporting the TypeSafe key, resolve text or start the
single-worker loopback server:

~~~bash
export TYPESAFE_API_KEY="..."
entity-resolver resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
entity-resolver serve --artifact-root .local-artifacts --schema config/schema.example.json --host 127.0.0.1 --port 8000
~~~

resolve emits JSON on stdout. Invalid schemas, missing artifacts, configuration
errors, provider failures, and timeouts produce safe JSON on stderr and a nonzero
exit status. serve uses host 127.0.0.1, port 8000, one worker, no reload, and
finally-closes the service.

## Python API and HTTP

The caller owns and closes the resolver:

~~~python
from pathlib import Path

from bootstrap import build_production_service
from contracts.models import ResolveRequest
from runtime.config import load_schema

schema = load_schema(Path("config/schema.example.json"))
service = build_production_service(
    artifact_root_path=Path(".local-artifacts"),
    schema=schema,
)
try:
    result = service.resolve(ResolveRequest(text="Alice works at Acme in Taipei."))
    print(result.model_dump(mode="json"))
finally:
    service.close()
~~~

For an explicitly injected test or integration composition, use
bootstrap.build_service with a runtime.pipeline.PipelineService. The checkout example
is runnable as:

~~~bash
python -m examples.resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
~~~

HTTP hosting is an injected FastAPI app. Create a fresh service for the server
process; the resolver in the preceding example has already been closed:

~~~python
import uvicorn
from pathlib import Path
from bootstrap import build_production_service
from transport.api import create_app
from runtime.config import load_schema

service = build_production_service(
    artifact_root_path=Path(".local-artifacts"),
    schema=load_schema(Path("config/schema.example.json")),
)
try:
    uvicorn.run(create_app(service), host="127.0.0.1", port=8000, workers=1)
finally:
    service.close()
~~~

Readiness and a request can be checked with:

~~~bash
curl -sS http://127.0.0.1:8000/health/ready
curl -sS -X POST http://127.0.0.1:8000/resolve -H 'content-type: application/json' -d '{"text":"Alice works at Acme in Taipei."}'
~~~

The API exposes POST /resolve, GET /health/ready, and /docs. Input validation is
HTTP 422. Readiness, admission, and provider configuration errors are HTTP 503;
candidate/provider failures are HTTP 502; provider timeouts are HTTP 504.

## Output contract and limitations

Entity spans are zero-based and end-exclusive character offsets. mention is the
exact source slice text[start:end]. normalized is the surface annotation used by
the resolver; this package does not ground it to a catalog identifier. confidence
is the original TypeSafe decision probability. sources retain CM/BM candidate
evidence and the decision-provider evidence. A same-span disagreement can be
represented as CONFLICTED with label hypotheses; empty output includes the
NO_ENTITY_EVIDENCE warning. Metadata records schema, policy, decoder, model/artifact,
provider, prompt, and timing identities.

An illustrative result is:

~~~json
{
  "schema_version": "entity-resolution-v1",
  "text": "Alice works at Acme in Taipei.",
  "entities": [{
    "id": "e1",
    "mention": "Acme",
    "label": "ORGANIZATION",
    "normalized": "Acme",
    "span": {"start": 15, "end": 19},
    "confidence": 0.97,
    "sources": [
      {"source": "CANDIDATE_MODEL", "source_id": "CM", "confidence": 0.20},
      {"source": "DECISION_PROVIDER", "source_id": "typesafe", "confidence": 0.97}
    ],
    "resolution_status": "EXTRACTED",
    "conflict": null
  }],
  "warnings": [],
  "metadata": {
    "policy_id": "CONFIDENCE_90_GAP_80",
    "schema_id": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
~~~

The runtime cache has 128 entries per stage and a 60-second TTL. Equivalent
decision requests share in-flight work; admission allows eight active and sixteen
waiting requests. Failed decisions are not cached, and cancelling an async caller
does not cancel shared work.

## Customization map and fixed limits

Customize labels and descriptions in the JSON schema, then provision the same fixed
CM/BM checkpoints. Replace the generic CandidateProposer or DecisionProvider only
through the contracts interfaces and an explicitly injected PipelineService; the
current artifact and model contracts are not a general model registry. The service
expects nonblank text, limits candidates to 64, sends at most 16 TypeSafe questions
per request, and applies 30,000-byte state/question and 60,000-byte request limits.
Each CM/BM candidate probe is bounded by a 1024-token input sequence and a 30-token
span. The TypeSafe side sends at most 16 questions per request, with 30,000-byte
state/question and 60,000-byte request budgets.

The main customization files are config/schema.example.json for labels and
descriptions, model_specs.py for immutable pins, adapters/otter for artifact/model
integration, adapters/typesafe/wire.py for provider request/response contracts,
resolution for selection/recovery policy, and bootstrap.py for composition.

Changing fixed pins or policy contracts requires corresponding tests and a new
review iteration.

## Troubleshooting

- If download reports a damaged target, move it aside and retry; silent replacement
  is intentionally refused.
- If check fails, inspect the model key, revision, manifest, file sizes, and SHA-256
  values. Startup may report TRUSTED_SOURCE_NOT_HASHED; check is the FULL_SHA256 path.
- If resolve or serve reports DECISION_CONFIGURATION_ERROR, export TYPESAFE_API_KEY
  and install the decision extra. A key is never loaded from .env automatically.
- If the CLI reports MISSING_DEPENDENCY or INVALID_CONFIGURATION, install the matching
  `api`, `otter`, or `decision` extra and check the schema/artifact-root arguments.
- If input exceeds the configured runtime limit, the safe code is QUERY_TOO_LONG
  (HTTP 422); shorten the text before retrying.
- If the provider times out or is unavailable, verify network access and use the safe
  typed error code. Raw provider responses are deliberately hidden.
- If the service is not ready, validate both artifacts and confirm the CPU runtime
  extras can load the pinned checkpoints.

## Developer checks and evidence

~~~bash
source .venv/bin/activate
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
git diff --check
~~~

The repository checks cover resolver contracts, artifact trust boundaries,
provisioning failure paths, lifecycle, CLI/API behavior, packaging, and import-time
side effects.

Real local evidence: download produced 12 CM files and 17 BM files with FULL_SHA256;
check and repeat-download reuse passed. With HF_HUB_OFFLINE=1 and
TRANSFORMERS_OFFLINE=1, both pinned Otter models loaded, warmed, proposed four
candidates, and closed cleanly. A live CLI resolve also completed with TypeSafe
model `jev-1.13.0`: the sample returned Alice PERSON [0, 5), Acme ORGANIZATION
[15, 19), and Taipei PLACE [23, 29) with no warnings. The ignored local `.env` was
loaded by the test harness only; the framework itself never loads `.env` automatically.
An injected FastAPI `TestClient` also returned readiness 200, the same English result,
the Chinese result `王小明在台北的台積電工作。` with 王小明 PERSON [0, 3), 台北
PLACE [4, 6), and 台積電 ORGANIZATION [7, 10), an empty `....` result with
`NO_ENTITY_EVIDENCE`, and HTTP 422 for a blank request. These are local validation
results, not a production-readiness guarantee.
