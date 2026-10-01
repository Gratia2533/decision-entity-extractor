# Production runtime and file inventory

This inventory describes the approved M2 flat layout. It records current runtime
ownership and verified boundaries; it is not a model-download or deployment guide.
The approved implementation is
M2-e4e01b0a58290b37cfb83c0cbf6323f1b26a8580ca7e30116daf2b68c7799707.

## Runtime path

~~~text
transport.cli / transport.api
  → bootstrap.build_service / build_production_service
  → runtime.resolver.EntityResolver
  → runtime.pipeline.PipelineService
  → adapters.otter.SelectedCandidateProposer
       → adapters.otter.runtime / alignment / artifact
  → adapters.typesafe.provider.TypeSafeDecisionProvider
       → adapters.typesafe.wire
  → resolution.selection + recovery
  → resolution.annotation + entities
  → contracts.models.EntityResolutionResult
~~~

The caller owns the resolver and closes it. The runtime retains bounded admission,
service-local success caches, single-flight sharing, cancellation shielding, startup
readiness, and idempotent shutdown. Optional provider/model dependencies are loaded
at the adapter boundary, rather than during core module import.

## Current source layout

| Path | Responsibility |
| --- | --- |
| contracts/models.py | Immutable entity schema, request/result, span, provenance, conflict, and runtime identity models. |
| contracts/errors.py | Typed pipeline errors and safe error responses. |
| contracts/interfaces.py | Generic candidate proposer and decision provider interfaces. |
| contracts/pipeline.py | Candidate/decision snapshots and request-builder contract. |
| resolution/selection.py | Primary raw-probability selection and same-label containment suppression. |
| resolution/recovery.py | Same decision-pool gap recovery, thresholds, lexical cue protection, and policy identity. |
| resolution/annotation.py | Surface-preserving annotation of selected predictions. |
| resolution/entities.py | Provenance, source/conflict projection, and final entity contract construction. |
| runtime/resolver.py | Public resolver facade and result projection. |
| runtime/pipeline.py | Lifecycle, readiness, cache/single-flight orchestration, admission, and request execution. |
| runtime/cache.py | Bounded success cache. |
| runtime/config.py | Cache/admission constants and JSON schema loading. |
| runtime/telemetry.py | Safe completion telemetry. |
| adapters/otter/proposer.py | Fixed CM/BM candidate union and model worker ownership. |
| adapters/otter/runtime.py | Local Otter model loading, inference, and warmup. |
| adapters/otter/alignment.py | Token/character offset alignment. |
| adapters/otter/artifact.py | Manifest identity, file inventory, symlink and checksum validation. |
| adapters/otter/contracts.py | Otter adapter contracts. |
| adapters/typesafe/provider.py | TypeSafe SDK adapter, warmup, timeout, safe error mapping, and lifecycle. |
| adapters/typesafe/wire.py | Schema-derived request construction and strict response validation. |
| bootstrap.py | Explicit composition root. |
| model_specs.py | Immutable CM/BM, tokenizer, runtime, decoder, and threshold pins. |
| hashing.py / version.py | Stable content hashing and package version. |
| transport/cli.py | identities, check, resolve, and serve commands. |
| transport/api.py | Injected FastAPI application with /resolve and /health/ready. |
| config/schema.example.json | Neutral sample schema. |
| examples/resolve.py | Explicit injected composition example. |

## Fixed runtime assumptions

M2 keeps two fixed candidate model identities: CM
whoisjones/otter-cross-mmbert at revision
8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d with threshold 0.04, and BM
whoisjones/otter-bi-mmbert at revision
53e10a09bc71a2e45980a7a257233a28305a5777 with threshold 0.05. BM uses the
jhu-clsp/mmBERT-base tokenizer at revision
c5955035435e2bf121cde7f3c8863ef52ff35d82. These are frozen assumptions, not an
arbitrary candidate-model registry; the M2 package does not claim plug-and-play
replacement of these models.

Artifacts are trusted and validated under a host-selected artifact-root. Runtime
does not download or replace artifacts. The M3 downloader will define provisioning
and staging behavior; no M3 command or production-readiness claim belongs in this
M2 inventory.

## Tests and packaging

Tests mirror the ownership boundaries under tests/contracts/, tests/resolution/,
tests/runtime/, tests/adapters/, and tests/transport/. tests/factories.py owns
shared fixtures; tests/packaging/test_imports.py covers fresh-process import and
package-boundary behavior.

The distribution remains entity-resolution version 2.0.0, with flat package
allowlists and root modules declared in pyproject.toml. The console script is
entity-resolver. Tests, docs, weights, caches, agent files, and the old
entity_resolution package are excluded from the wheel.

M2 validation evidence is 103 passing tests, Ruff check/format success, uv
lock --check, wheel and sdist builds, installed-wheel smoke checks from outside
the repository, and whitespace checks. Live TypeSafe/Otter inference is not
verified because this environment has no TypeSafe key or model artifacts.
