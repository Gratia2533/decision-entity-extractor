# Canonical Entity Resolution

This repository contains a standalone Python package that turns raw text into
typed, span-grounded entities. The caller supplies entity names and descriptions;
the package does not assume a business domain, resolve catalog identifiers, or
normalize input text.

The current M2 checkpoint completes the flat package layout and local commands.
Model downloading is planned for M3 and is not part of this checkpoint. Runtime
still requires host-provisioned artifacts and a `TYPESAFE_API_KEY`.

## Architecture

```text
raw text
  → pinned local CM/BM candidate proposal
  → generic DecisionProvider classification
  → confidence filtering and same-label containment selection
  → gap recovery from the same decision pool
  → annotation and surface/conflict resolution
  → EntityResolutionResult
```

`contracts/` contains the shared models and provider interfaces. `resolution/`
contains selection, recovery, annotation, and entity projection rules. `runtime/`
owns the facade, pipeline lifecycle, cache, admission, and telemetry. Otter and
TypeSafe integrations live under `adapters/`; HTTP and CLI entry points live under
`transport/`. `bootstrap.py` is the composition root.

The retained policy uses raw decision probabilities, a primary threshold of `0.90`,
same-label containment suppression, and a second pass at `0.80` for candidates in
eligible uncovered runs of at least two characters. Lexical operator cues protect
recovery gaps; they do not infer application operators or preferences. Recovery
performs no additional inference. The CM/BM candidate union keeps model scores
separate, and the TypeSafe adapter selects one configured label per candidate.

## Installation

Python 3.12 and 3.13 are supported. The core dependency is Pydantic; Otter, the
TypeSafe adapter, and HTTP hosting are optional extras.

```bash
uv sync --group dev --extra api --extra otter --extra decision
source .venv/bin/activate
```

The package can also be built and installed from the checkout:

```bash
uv build
pip install "dist/entity_resolution-2.0.0-py3-none-any.whl[api,otter,decision]"
```

The extras are:

| Extra | Purpose |
| --- | --- |
| `otter` | Pinned local CPU candidate-model runtime. |
| `decision` | TypeSafe SDK `0.7.0` and its HTTP client. |
| `api` | FastAPI and Uvicorn HTTP hosting. |

Core imports do not load model weights, import optional ML/provider SDKs, download
artifacts, or contact a provider. The `.env` file is not loaded automatically.

## Entity schema

Names and descriptions come from the caller. Names must be unique, nonblank, and
free of surrounding whitespace or line breaks. The rejection choice must differ from
every entity label, and schema order defines deterministic tie breaking.

```python
from contracts.models import EntityLabel, EntitySchema

schema = EntitySchema(
    labels=(
        EntityLabel(name="ORGANIZATION", description="A named organization"),
        EntityLabel(name="PLACE", description="A named geographic place"),
    ),
    rejection_label="NOT_ENTITY_OR_MIXED",
)
```

The sample schema at `config/schema.example.json` uses neutral `PERSON`,
`ORGANIZATION`, and `PLACE` labels. `runtime.config.load_schema` validates a JSON
file with `EntitySchema`; changing a schema changes the decision prompt identity and
requires a separately configured service.

## Pinned models and artifacts

The candidate adapter uses the fixed CM/BM Otter identities in `model_specs.py`:

| Key | Model | Revision | Threshold |
| --- | --- | --- | ---: |
| CM | `whoisjones/otter-cross-mmbert` | `8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d` | 0.04 |
| BM | `whoisjones/otter-bi-mmbert` | `53e10a09bc71a2e45980a7a257233a28305a5777` | 0.05 |

Both use the pinned `mmBERT` runtime configuration. BM additionally requires the
`jhu-clsp/mmBERT-base` tokenizer at revision
`c5955035435e2bf121cde7f3c8863ef52ff35d82`.

M2 does not download models. The host must provision manifests and files below the
paths derived from the model IDs and revisions, then use `check` for full byte-level
verification:

```text
<artifact-root>/whoisjones--otter-cross-mmbert/<CM revision>/manifest.json
<artifact-root>/whoisjones--otter-bi-mmbert/<BM revision>/manifest.json
```

The artifact validator checks the frozen identities, required remote-code/config and
weight hashes, tokenizer files, regular-file boundaries, and manifest inventory. It
rejects symlinks and mismatched files. Runtime does not auto-download or replace an
artifact. `entity-resolver identities` needs no API key or weights; `check` requires
the local artifact tree.

The service also requires `TYPESAFE_API_KEY` in the host environment. This checkpoint
has no live provider or model-inference evidence because credentials and artifacts are
not included in the repository.

## CLI

The installed command is `entity-resolver` and can also be run as
`python -m transport.cli` from the checkout.

```bash
entity-resolver --help
entity-resolver identities
entity-resolver check --artifact-root .local-artifacts
entity-resolver resolve --artifact-root .local-artifacts \
  --schema config/schema.example.json \
  --text "Alice visited Taipei."
entity-resolver serve --artifact-root .local-artifacts \
  --schema config/schema.example.json --host 127.0.0.1 --port 8000
```

`resolve` writes result JSON to stdout. Invalid configuration and runtime
failures write safe typed JSON to stderr and return a nonzero exit status. `serve`
uses loopback and one worker by default; it does not enable reload or multiple
workers, and closes the service on exit. `examples/resolve.py` demonstrates injected
CM/BM and TypeSafe composition:

```bash
python -m examples.resolve --artifact-root .local-artifacts \
  --schema config/schema.example.json --text "Alice visited Taipei."
```

## Python and HTTP APIs

The caller owns the service and must close it:

```python
from pathlib import Path

from bootstrap import build_production_service
from contracts.models import EntitySchema, ResolveRequest
from runtime.config import load_schema

schema: EntitySchema = load_schema(Path("config/schema.example.json"))
service = build_production_service(
    artifact_root_path=Path(".local-artifacts"),
    schema=schema,
)
try:
    result = service.resolve(ResolveRequest(text="Alice visited Taipei."))
    print(result.model_dump(mode="json"))
finally:
    service.close()
```

`bootstrap.build_service(pipeline)` accepts an explicitly composed
`runtime.pipeline.PipelineService`; this is the test and integration seam. Requests
contain only nonblank `text` and reject unknown fields. Spans use zero-based,
end-exclusive Python character offsets, and every mention equals `text[start:end]`.
Results contain the original text, ordered entities, warnings, schema and policy
identities, source provenance, and timings. Empty selections carry
`NO_ENTITY_EVIDENCE`; a label disagreement is represented as `CONFLICTED` metadata.

The optional HTTP transport is created with an injected resolver:

```python
import uvicorn

from transport.api import create_app

try:
    uvicorn.run(create_app(service), host="127.0.0.1", port=8000, workers=1)
finally:
    service.close()
```

It exposes `POST /resolve`, `GET /health/ready`, and FastAPI's `/docs`. Input
validation returns HTTP 422; readiness and configuration failures return 503;
candidate/provider failures return 502 or 504 according to the typed error.
Creating the app does not transfer service ownership.

## Runtime behavior and checks

The production composition retains a service-local success cache with 128 entries
per stage and a 60-second TTL, in-flight single-flight sharing for equivalent
decision requests, admission for eight active and sixteen waiting requests, and
idempotent shutdown. Failed decisions are not cached, and cancelling an async caller
does not cancel shared work.

Run the repository checks with the project environment activated:

```bash
source .venv/bin/activate
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
git diff --check
```

The approved M2 checkpoint passes 103 tests, Ruff check/format validation, lock
validation, wheel and sdist builds, installed-wheel core/import smoke checks, and
whitespace checks. The delegated review covered all 61 reviewable files; four
unsupported files were manually checked. Live TypeSafe/Otter inference remains
unverified until credentials and artifacts are available. The M3 downloader and the
full bilingual tutorial are intentionally deferred.
