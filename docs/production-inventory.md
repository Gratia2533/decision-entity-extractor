# Production runtime and file inventory

This inventory records the approved M3–M5 implementation and its verified local
evidence. It describes runtime ownership and file boundaries; it does not claim
general production readiness.

Approved implementation:
M3-M5-2e29436c78c1223453af18ff87c87943752212bd6d4ea62ebe41340fe08d3e6f

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
  → adapters.otter.SelectedCandidateProposer
       → adapters.otter.runtime / alignment / artifact
  → adapters.typesafe.provider.TypeSafeDecisionProvider
       → adapters.typesafe.wire
  → resolution.selection + recovery
  → resolution.annotation + entities
  → contracts.models.EntityResolutionResult
~~~

The caller owns and closes the resolver. PipelineService owns startup readiness,
one event-loop thread, worker lifecycle, bounded admission, success caches,
single-flight sharing, cancellation shielding, and shutdown. Runtime does not
download artifacts automatically. `transport.cli download` is explicit provisioning;
`resolve` and `serve` only load already published artifacts. `transport.cli` also owns
the explicit check command; `transport.api` owns neither resolver creation nor shutdown.

## Source ownership

| Path | Production responsibility |
| --- | --- |
| contracts/models.py | Immutable schema, request/result, span, provenance, conflict, and source identity models. |
| contracts/errors.py | Typed pipeline errors and safe HTTP/CLI responses. |
| contracts/interfaces.py | Generic candidate proposer and decision provider boundaries. |
| contracts/pipeline.py | Candidate/decision snapshots and fixed request budgets. |
| resolution/selection.py | CN90 raw-probability gate and same-label containment NMS. |
| resolution/recovery.py | GAP80 same-pool recovery, eligible gap geometry, and lexical cue protection. |
| resolution/annotation.py | Surface annotation and normalized mention projection. |
| resolution/entities.py | Final entities, provenance, exact-span conflicts, and output contract. |
| runtime/resolver.py | Public resolver facade and result metadata projection. |
| runtime/pipeline.py | Runtime lifecycle, readiness, cache, single-flight, admission, and orchestration. |
| runtime/cache.py | Bounded success cache implementation. |
| runtime/config.py | Cache/admission constants and JSON schema loader. |
| runtime/telemetry.py | Safe completion telemetry. |
| adapters/otter/proposer.py | CM/BM fixed union and model worker ownership. |
| adapters/otter/runtime.py | CPU float32 Otter load, warmup, inference, and span decoding. |
| adapters/otter/alignment.py | Token/character offset alignment. |
| adapters/otter/artifact.py | Frozen manifest, pin, file, symlink, and checksum validation. |
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

## Frozen model and artifact contract

CM is whoisjones/otter-cross-mmbert, cross encoder, revision
8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d, threshold 0.04, with a
1,235,084,300-byte weight. BM is whoisjones/otter-bi-mmbert, bi encoder, revision
53e10a09bc71a2e45980a7a257233a28305a5777, threshold 0.05, with a
1,906,302,124-byte weight. Both use mmBERT, 1024 sequence tokens, and 30 span
tokens. BM's runtime tokenizer is jhu-clsp/mmBERT-base revision
c5955035435e2bf121cde7f3c8863ef52ff35d82.

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

## Tests and verified evidence

Tests mirror production ownership under tests/contracts/, tests/resolution/,
tests/runtime/, tests/adapters/, tests/transport/, and tests/packaging/. The M3
provisioning suite covers exact pins and allowlists, full manifest validation,
healthy reuse, damaged-target preservation, staging and interruption cleanup,
symlink dereference, regular-file enforcement, cache/network failure redaction,
competing-target preservation, and partial CM/BM completion.

The implementation passed 122 tests, Ruff check/format, uv lock --check, wheel and
sdist build, installed-wheel smoke outside the checkout, and tracked/untracked
whitespace checks. Delegated OCR reviewed 6/6 reviewable changes with no blockers.
The final graph review used revision 2026-10-01T18:01:09 with head_matches_build=true;
provisioning.py has required_checkpoint_files, _snapshot, _copy_files, and
download_artifacts nodes, and transport.cli.main calls download_artifacts.

Real local evidence includes successful CLI download/check/reuse, 12 CM and 17 BM
FULL_SHA256 files, and offline CPU float32 CM/BM load, warmup, four exact-span
candidate proposals, and close. A live CLI resolve also completed with TypeSafe model
`jev-1.13.0`, returning Alice PERSON [0, 5), Acme ORGANIZATION [15, 19), and Taipei
PLACE [23, 29) without warnings. An injected FastAPI TestClient returned readiness 200,
the same English result, the Chinese sample with exact spans for 王小明、台北、台積電,
an empty result with NO_ENTITY_EVIDENCE, and HTTP 422 for a blank request. These local
checks do not establish production readiness or hosted deployment behavior.
