# Usage

[README](../README.md) · [繁體中文](usage.zh-TW.md) · [Customization](customization.md)

<a id="environment"></a>
## Environment

Run commands from the repository root after the [quick start](../README.md#quick-start).
`uv run --no-sync` uses the environment installed by `uv sync`, keeping its selected extras.

| Requirement | Current setup |
| --- | --- |
| Python | 3.12 or 3.13 |
| Linux CPU wheels | `x86_64`, `aarch64` |
| Windows CPU wheels | `AMD64`, `ARM64` |
| macOS | No pinned PyTorch wheel in the current `otter` extra |
| Model storage | About 3.14 GB of weights, plus tokenizer files, cache and staging copies |
| Decision service | TypeSafe API key and network access |

Dependency groups are `otter` for local candidate inference, `decision` for the decision
provider, and `api` for HTTP hosting. Exact versions are in
[pyproject.toml](../pyproject.toml) and [uv.lock](../uv.lock). Local inference uses CPU float32.

Set the key in the shell that launches the resolver. The program does not load `.env` files.

```bash
export TYPESAFE_API_KEY="your-api-key"
```

PowerShell uses the following syntax. Keep commands on one line instead of using Bash's `\` continuation:

```powershell
$env:TYPESAFE_API_KEY = "your-api-key"
uv run --no-sync entity-resolver resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
```

Candidate inference runs locally. Decision requests send raw text, candidate spans,
schema criteria and context to TypeSafe. Credentials and raw provider responses are
excluded from public result metadata and completion telemetry.

## Model files

The bundled candidate models are Otter multilingual entity recognizers. Their
stable source keys appear in CLI output, manifests, and result provenance:

| Source key | Model | Architecture | Candidate score threshold |
| --- | --- | --- | --- |
| `CM` | [whoisjones/otter-cross-mmbert](https://huggingface.co/whoisjones/otter-cross-mmbert) | Cross-encoder: encodes type descriptions and text together | ≥ 0.04 |
| `BM` | [whoisjones/otter-bi-mmbert](https://huggingface.co/whoisjones/otter-bi-mmbert) | Bi-encoder: encodes text and type descriptions separately | ≥ 0.05 |

Both models use an mmBERT text encoder. This adapter supplies your schema's label
descriptions as the model's type inputs and merges candidates with identical
character boundaries, preserving each model's score. These are this project's
candidate thresholds, not the upstream models' default prediction thresholds.
The later [selection and recovery thresholds](customization.md#selection-and-recovery)
of 0.90 and 0.80 apply to TypeSafe decision probabilities instead.

The bundled decision provider uses TypeSafe model `jev-1.13.0` to classify candidate
spans against your entity labels and return probabilities. Its model identifier and
response validation are defined in [adapters/typesafe/wire.py](../adapters/typesafe/wire.py).
This model runs through the TypeSafe API; the download commands below provision
only the local Otter candidate models.

```bash
uv run --no-sync entity-resolver identities
uv run --no-sync entity-resolver download --artifact-root .local-artifacts
uv run --no-sync entity-resolver check --artifact-root .local-artifacts
```

- These commands and `--help` do not need a TypeSafe key.
- `download` uses the Hugging Face cache and validates files before publishing them.
- Healthy targets are reused. Damaged targets are preserved and reported; move them aside before retrying.
- An interrupted download leaves the Hub cache available for retry. If one checkpoint completed, it can be reused.
- Startup checks the manifest and file structure but reports `TRUSTED_SOURCE_NOT_HASHED` for weights. `check` hashes current file bytes and reports `FULL_SHA256`.
- Runtime does not download missing models. Avoid concurrent downloads to the same target; there is no custom cross-process download lock.

See [model_specs.py](../model_specs.py) for model revisions and checksums, and
[provisioning.py](../adapters/otter/provisioning.py) for required files.

## Python

The caller owns the resolver and must close it:

```python
from pathlib import Path

from bootstrap import build_production_service
from contracts.models import ResolveRequest
from runtime.config import load_schema

service = build_production_service(
    artifact_root_path=Path(".local-artifacts"),
    schema=load_schema(Path("config/schema.example.json")),
)
try:
    result = service.resolve(ResolveRequest(text="Alice works at Acme in Taipei."))
    print(result.model_dump(mode="json"))
finally:
    service.close()
```

Run this from the installed environment. A complete command-line Python example is
available in [examples/resolve.py](../examples/resolve.py).

## HTTP

Start the server, then send a request from another terminal:

```bash
uv run --no-sync entity-resolver serve --artifact-root .local-artifacts --schema config/schema.example.json
```

```bash
curl -sS http://127.0.0.1:8000/health/ready
curl -sS -X POST http://127.0.0.1:8000/resolve \
  -H 'content-type: application/json' \
  -d '{"text":"Alice works at Acme in Taipei."}'
```

- Defaults: `127.0.0.1:8000`, one worker, no reload. Override `--host` and `--port` as needed.
- Open `/docs` on the running server for the API reference.
- For an existing application, call `transport.api.create_app(service)` with a fresh resolver. Its owner must close it after the server stops; the app does not manage shutdown.

| HTTP status | Meaning |
| --- | --- |
| 422 | Invalid input or text exceeding the runtime limit |
| 503 | Not ready, admission capacity reached, or decision-provider configuration error |
| 502 | Candidate or decision-provider failure |
| 504 | Decision-provider timeout |

## Output

The full contract is [EntityResolutionResult](../contracts/models.py).

| Field | Meaning |
| --- | --- |
| `text` | Original input |
| `entities[].mention` | Exact `text[start:end]` slice |
| `entities[].span` | Zero-based character offsets; `end` is exclusive |
| `entities[].label` | One of your configured entity types |
| `entities[].confidence` | Original decision probability |
| `entities[].normalized` | Currently identical to `mention`; preserves source text without name standardization or catalog ID lookup |
| `entities[].sources` | Candidate and decision-provider evidence |
| `entities[].resolution_status` / `conflict` | Extraction status and any same-span label disagreement |
| `warnings` | Includes `NO_ENTITY_EVIDENCE` when no entities are found |
| `metadata` | Schema, policy, model, prompt and timing information |

`metadata.policy_id` is `CONFIDENCE_90_GAP_80`: primary selection at 0.90 followed
by eligible-gap recovery at 0.80, with same-label containment NMS. This is a stable
machine identifier; the full rules are in the customization guide. Candidate
`source_id` values `CM` and `BM` refer to the models listed above.

CLI results go to stdout. Failures produce JSON on stderr and a nonzero exit status.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `DECISION_CONFIGURATION_ERROR` | Set `TYPESAFE_API_KEY` and install the `decision` extra. |
| `MISSING_DEPENDENCY` | Install the required `otter`, `decision` or `api` extra. |
| `INVALID_CONFIGURATION` | Check schema and artifact paths; validate artifacts with `check`. |
| Damaged model target | Move the affected checkpoint directory aside and retry `download`. |
| `QUERY_TOO_LONG` | Shorten the input text. |
| Provider timeout or failure | Check network access and the returned error code. |
| Service not ready | Run `check` and confirm both checkpoints load with the installed CPU dependencies. |
