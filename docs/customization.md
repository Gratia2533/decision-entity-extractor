# Customization

[README](../README.md) · [繁體中文](customization.zh-TW.md) · [Usage](usage.md)

## Define entity types

Edit [config/schema.example.json](../config/schema.example.json), or pass another
JSON file with `--schema`:

```json
{
  "labels": [
    {"name": "PERSON", "description": "A named person"},
    {"name": "ORGANIZATION", "description": "A named organization"},
    {"name": "PLACE", "description": "A named geographic place"}
  ],
  "rejection_label": "NOT_ENTITY_OR_MIXED"
}
```

- Names must be unique and nonblank, without surrounding whitespace or line breaks.
- Descriptions must contain non-whitespace text. Describe what qualifies for each type.
- `rejection_label` covers non-entities, mixed labels and broken boundaries; it must differ from every entity name.
- Candidate probes use descriptions. The decision model uses names, descriptions and the rejection choice. Schema order breaks score ties.
- Rebuild the service after changing the schema so its schema and prompt identities update together.

Python callers can construct `EntitySchema` and `EntityLabel` directly; see their
[definitions and validation rules](../contracts/models.py).

## Selection and recovery

The README shows the conceptual flow. The current policy applies these rules:

| Step | Rule |
| --- | --- |
| Confidence filter | Keep accepted predictions with raw decision probability ≥ 0.90. |
| Containment NMS | Within each label, a retained span suppresses another when either fully contains the other and the retained score is at least as high (minimum difference 0.0). |
| Gap recovery | Select accepted candidates scoring ≥ 0.80 fully inside eligible uncovered runs of at least two characters. |
| Final selection | Apply NMS to recovered candidates and merge them with primary results. |

NMS means non-maximum suppression. Candidates are considered by descending score;
ties use ascending start offset, end offset, then candidate ID. Partial overlaps
without full containment are not suppressed. Intersection over union (IoU) is
recorded in the suppression trace but is not a selection threshold.

Recovery reuses the original decision pool. Eligible gaps exclude primary results
and protected lexical cues, are at least two characters long, and contain at least
one character that is neither whitespace, punctuation, nor a symbol. A recovered
candidate must fit entirely inside one such gap. No additional model requests are
made. Lexical protection matches configured standalone cues such as `不要` and `或`
at non-word or text boundaries; it does not interpret application logic or preferences.

Internal configuration IDs `CN90` and `CN90_GAP80_V1`, and the public metadata ID
`CONFIDENCE_90_GAP_80`, remain stable for serialization and traceability. They refer
to the primary selection and combined recovery rules above, not separate models.

See [selection.py](../resolution/selection.py) and [recovery.py](../resolution/recovery.py)
for the exact policy. Changes to these rules need corresponding tests.

## Limits

| Boundary | Current limit | Source |
| --- | --- | --- |
| Candidate input / span | 1,024 / 30 tokens per model probe | [Model specs](../model_specs.py) |
| Candidate pool | 64 candidates | [Pipeline contracts](../contracts/pipeline.py) |
| Decision request | 16 questions; 30,000 bytes per state/question; 60,000 bytes per request | [Pipeline contracts](../contracts/pipeline.py) |
| Admission | 8 active and 16 waiting requests | [Runtime config](../runtime/config.py) |
| Cache | 128 entries per stage; 60-second TTL | [Runtime config](../runtime/config.py) |

Text must be nonblank. Equivalent decision requests share in-flight work; failed
decisions are not cached. Cancelling an async caller does not cancel shared work.
The weight download size is not a minimum RAM requirement.

## Adapters and source map

The concept diagram is model-neutral. The bundled implementation uses local Otter
candidate models and a TypeSafe decision provider. Changing the schema changes
entity types, not model architectures or checkpoints.

For a custom integration, implement the [proposer/provider protocols](../contracts/interfaces.py),
compose a [PipelineService](../runtime/pipeline.py), and pass it to
[`build_service`](../bootstrap.py). The current candidate snapshots still validate
fixed model provenance, and the resolver expects those source identities. Replacing
candidate models requires updating these contracts and tests as well as the adapter;
there is no arbitrary model registry.

| Path | Responsibility |
| --- | --- |
| [contracts/](../contracts/) | Schema, request/result models, candidate/decision contracts and interfaces |
| [resolution/](../resolution/) | Confidence filtering, NMS, recovery and result projection |
| [runtime/](../runtime/) | Resolver lifecycle, readiness, caching and request admission |
| [adapters/otter/](../adapters/otter/) | Local candidate inference, alignment and model provisioning |
| [adapters/typesafe/](../adapters/typesafe/) | Decision requests, response validation and provider lifecycle |
| [transport/](../transport/) | CLI and HTTP endpoints |
| [bootstrap.py](../bootstrap.py) | Service composition |
| [model_specs.py](../model_specs.py) | Model identities, checksums, tokenizer files and runtime pins |

## Developer checks

With the quick-start environment installed:

```bash
uv sync --frozen --group dev --extra api --extra otter --extra decision
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv lock --check
uv build
git diff --check
```

Tests cover contracts, selection, artifact validation, provisioning, lifecycle,
CLI/HTTP behavior and packaging. Keep both READMEs and each English/Traditional
Chinese guide pair aligned when changing documented behavior.
