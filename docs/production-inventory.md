# Runtime architecture and file inventory

This inventory describes the entity extraction runtime, module responsibilities,
pinned model artifacts, and verification coverage. For setup and commands, see
[Usage](usage.md); for selection rules and extension points, see
[Customization](customization.md).

The documented local checks do not establish deployment readiness, service-level
objectives, or hosted artifact availability.

## Provisioning and runtime paths

~~~text
transport.cli download
  → adapters.otter.provisioning
  → staging / manifest validation / atomic publish
  → artifact root

artifact root
  → bootstrap.build_service / build_production_service
  → runtime.resolver.EntityResolver
  → runtime.pipeline.PipelineService
  → adapters.otter.proposer.SelectedCandidateProposer
       → adapters.otter.runtime / alignment / artifact
  → adapters.typesafe.provider.TypeSafeDecisionProvider
       → adapters.typesafe.wire
  → resolution.selection + recovery
  → resolution.annotation + entities
  → contracts.models.EntityResolutionResult
~~~

The caller owns and closes the resolver. PipelineService owns startup readiness,
one event-loop thread, worker lifecycle, bounded admission, success caches,
single-flight sharing, cancellation shielding, and shutdown. Single-flight sharing
lets equivalent decision requests await one in-progress task. Cancellation shielding
allows that shared task to continue when an individual caller cancels its wait.
Bounded admission limits the number of active and waiting requests. Runtime does not
download artifacts automatically. `transport.cli download` is explicit provisioning;
`resolve` and `serve` only load already published artifacts. `transport.cli` also owns
the explicit check command; `transport.api` owns neither resolver creation nor shutdown.

## Source ownership

| Path | Responsibility |
| --- | --- |
| contracts/models.py | Immutable schema, request/result, span, provenance, conflict, and source identity models. |
| contracts/errors.py | Typed pipeline errors and safe HTTP/CLI responses. |
| contracts/interfaces.py | Generic candidate proposer and decision provider boundaries. |
| contracts/pipeline.py | Candidate/decision snapshots and fixed request budgets. |
| resolution/selection.py | Filter raw decision probabilities at 0.90 and suppress same-label contained spans. |
| resolution/recovery.py | Recover candidates at 0.80 from existing decisions inside eligible uncovered intervals, respecting protected lexical cues. |
| resolution/annotation.py | Validate selected spans against the source text and copy each mention into the normalized field unchanged. |
| resolution/entities.py | Final entities, provenance, exact-span conflicts, and output contract. |
| runtime/resolver.py | Public resolver facade and result metadata projection. |
| runtime/pipeline.py | Runtime lifecycle, readiness, cache, single-flight, admission, and orchestration. |
| runtime/cache.py | Bounded success cache implementation. |
| runtime/config.py | Cache/admission constants and JSON schema loader. |
| runtime/telemetry.py | Safe completion telemetry. |
| adapters/otter/proposer.py | Merge Otter cross-encoder and bi-encoder candidates by exact span boundaries; own model workers. |
| adapters/otter/runtime.py | CPU float32 Otter load, warmup, inference, and span decoding. |
| adapters/otter/alignment.py | Token/character offset alignment. |
| adapters/otter/artifact.py | Validate manifests, pinned revisions, required files, symlink boundaries, and checksums. |
| adapters/otter/provisioning.py | Hub-cache download, allowlisted staging copy, manifest validation, and atomic publish. |
| adapters/otter/contracts.py | Otter candidate and inference contracts. |
| adapters/typesafe/provider.py | TypeSafe SDK lifecycle, timeout, warmup, and safe failure mapping. |
| adapters/typesafe/wire.py | Schema descriptions, decision requests, strict response validation, and prompt identity. |
| bootstrap.py | Explicit injected production composition. |
| model_specs.py | Immutable model, tokenizer, runtime, decoder, threshold, and checksum pins. |
| hashing.py / version.py | Stable identity hashing and package version. |
| transport/cli.py | identities, download, check, resolve, and serve commands. |
| transport/api.py | Injected FastAPI /resolve and /health/ready transport. |
| config/schema.example.json | Neutral PERSON/ORGANIZATION/PLACE schema. |
| examples/resolve.py | Runnable injected composition example. |

## Pinned models and artifact validation

| Source key | Model | Architecture | Candidate threshold | Weight bytes |
| --- | --- | --- | --- | ---: |
| `CM` | [whoisjones/otter-cross-mmbert](https://huggingface.co/whoisjones/otter-cross-mmbert) | Cross-encoder | ≥ 0.04 | 1,235,084,300 |
| `BM` | [whoisjones/otter-bi-mmbert](https://huggingface.co/whoisjones/otter-bi-mmbert) | Bi-encoder | ≥ 0.05 | 1,906,302,124 |

`CM` and `BM` are stable source keys used in manifests and result provenance.
The cross-encoder encodes type descriptions and text together; the bi-encoder
encodes them separately. Both use an mmBERT text encoder and accept at most 1024
sequence tokens and 30 tokens per span. The bi-encoder's runtime tokenizer is
`jhu-clsp/mmBERT-base`. Exact revisions and checksums are defined in
[model_specs.py](../model_specs.py).

Candidate scores determine which spans reach the decision provider. The later
0.90 primary-selection and 0.80 recovery thresholds apply to decision
probabilities, as described in [selection and recovery](customization.md#selection-and-recovery).

Provisioning downloads only required checkpoint/code/config/tokenizer files from
the pinned revisions, copies regular bytes from the Hub cache, validates all
manifest entries and checksums, then atomically renames the completed checkpoint.
Healthy targets are reused. Damaged targets, competing targets, interruptions,
nonregular files, symlinks at the artifact root, incomplete staging, and validation
failures are preserved or rejected without silent replacement. Hub cache contents
are not removed. There is no custom cross-process download lock.

## Transport contracts

The CLI requires no key for help and identities. download defaults artifact-root to
.local-artifacts and does not construct a schema, provider, or service. check verifies
current bytes with FULL_SHA256. resolve and serve require an artifact root and schema;
the host must export TYPESAFE_API_KEY for a live decision request. serve defaults to
127.0.0.1:8000, one worker, no reload, and closes its service in finally.

The API accepts POST /resolve, GET /health/ready, and exposes /docs. The injected
service owner handles closure. Validation is 422; service readiness, admission, and
provider configuration are 503; candidate/provider failure is 502; provider timeout
is 504. CLI errors are safe JSON on stderr with nonzero exit status.

## Verification coverage

Tests mirror production ownership under tests/contracts/, tests/resolution/,
tests/runtime/, tests/adapters/, tests/transport/, and tests/packaging/. The model
provisioning tests cover exact pins and allowlists, full manifest validation,
healthy reuse, damaged-target preservation, staging and interruption cleanup,
symlink dereference, regular-file enforcement, cache/network failure redaction,
competing-target preservation, and reuse of a completed checkpoint when the other
model's download fails.

Run the [developer checks](customization.md#developer-checks) for tests, lint,
formatting, lock consistency, package builds, and whitespace validation. The
packaging tests check that core imports do not load optional model/provider SDKs
or create background threads, and that model pins cannot be mutated.

To verify actual model files, follow [download and check commands](usage.md#model-files).
Live inference additionally requires local model loading and a TypeSafe request
using the [CLI, Python, or HTTP examples](usage.md). Passing unit tests alone does
not verify model downloads, live provider availability, or deployment behavior.
