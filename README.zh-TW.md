# Canonical Entity Resolution

[English README](README.md)

這個 repository 是 canonical entity resolution 的 clone、客製化與執行 framework，
將原始文字轉成具型別、保留來源區間的 entities。CM/BM candidate probe 使用
description；TypeSafe criteria 使用每個 label 的名稱、description 與 rejection choice。
呼叫端提供 entity 名稱與描述；本套件不假設業務領域、不解析 catalog ID，也不
正規化輸入文字。

本套件包含 flat package、明確的 artifact provisioning、CLI/API transport，以及
保留的 CM/BM 與 TypeSafe pipeline。Runtime 不會自動下載模型。
TypeSafe 即時分類仍需要 TYPESAFE_API_KEY；本機固定版 Otter 的下載、驗證、載入、
warmup、candidate proposal 已完成驗證。

## 架構與邊界

~~~text
原始文字
  → 固定的 CM/BM candidate proposal
  → 由 schema 產生的 TypeSafe decision questions
  → raw probability 信心過濾與同 label containment NMS
  → 從同一 decision pool 做 gap recovery
  → 保留原表面的 annotation 與 provenance/conflict projection
  → EntityResolutionResult
~~~

| 套件 | 責任 |
| --- | --- |
| contracts | 不可變 request/result/schema model 與通用 proposer/provider interface。 |
| resolution | selection、recovery、annotation、provenance、conflict 規則。 |
| runtime | resolver facade、lifecycle、readiness、cache、single-flight、admission、telemetry。 |
| adapters/otter | candidate model runtime、alignment、artifact 驗證與 provisioning。 |
| adapters/typesafe | TypeSafe wire 建立、嚴格 response 驗證與 provider lifecycle。 |
| transport | CLI 與注入式 FastAPI application。 |
| bootstrap.py | 明確的 production composition root。 |

Candidate adapter 使用固定的 CM/BM 模型假設，不是任意模型 registry。TypeSafe
adapter 位於通用 DecisionProvider interface 後方；API 接收注入的 resolver，
transport 不接管 service 關閉責任。

保留的 postprocessing 使用 raw decision probability：保留至少 0.90 的候選，
依固定 containment 規則抑制同 label 的包含區間，再於至少兩個字元的 eligible
uncovered run 內，以至少 0.80 做 recovery。Recovery 重用同一 decision pool，
不執行額外 inference。獨立 lexical operator cue 只保護 gap，不推論應用層
operator 或 preference。

## 第一次 clone 與 locked environment

支援 Python 3.12 與 3.13。`otter` extra 固定 Linux（`x86_64`/`aarch64`）與
Windows（`AMD64`/`ARM64`）的 CPU wheel；本機 candidate inference 請使用有相符
wheel 的平台。以下驗證是在 Linux `x86_64` 完成；目前 extra 沒有固定 macOS 的
torch wheel。新 clone 請先建立環境、啟用環境，再安裝 locked extras：

~~~bash
git clone https://github.com/Gratia2533/decision-entity-extractor.git
cd decision-entity-extractor
uv venv --python 3.13
source .venv/bin/activate
uv sync --frozen --group dev --extra api --extra otter --extra decision
~~~

請依 [uv 官方安裝說明](https://docs.astral.sh/uv/getting-started/installation/) 安裝 uv。

Extras：

| Extra | 用途 |
| --- | --- |
| otter | 固定版本的 local CPU candidate-model runtime。 |
| decision | TypeSafe SDK 0.7.0 與 HTTP client。 |
| api | FastAPI 與 Uvicorn hosting。 |

lock 也固定 candidate model 使用的 runtime：torch 2.9.1+cpu、transformers
4.56.2、tokenizers 0.22.2、numpy 2.5.2、huggingface-hub 0.36.2、safetensors
0.8.0。Candidate model 以 CPU float32 載入。不會自動載入 .env；使用 decision
provider 時請明確 export TYPESAFE_API_KEY。

Core import 不會載入 weights、import optional ML/provider SDK、連線網路或建立
runtime worker。identities、help、download 不需要 TypeSafe key。

## Schema 與 label 客製化

同一份 EntitySchema 的 label description 會提供給兩個 candidate model probe；
TypeSafe criteria 會使用 label 名稱與 description。名稱必須唯一、非空白，不能有前後空白或換行；description
必須包含非空白字元；rejection label 不得與 entity label 相同。Schema 順序提供
deterministic tie breaking。

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

中性範例位於 config/schema.example.json。修改 schema 後再建立 service；schema
identity 與 TypeSafe prompt hash 會改變。Label 可客製化，但目前 CM/BM 的
candidate architecture、revision、檔案 allowlist 仍固定。

等價的 JSON 設定如下：

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

每個 label 都需要唯一名稱與非空白 description。rejection label 用於非 entity、
混合 label 或邊界錯誤，不能與 entity label 重複。Descriptions 同時送給 candidate
probe 與 TypeSafe criteria；label 名稱與 rejection label 是 TypeSafe choice，不會
作為 Otter probe 文字。修改 schema 後要重新組合 service，保持 prompt/schema identity
明確。

## Models、revisions、檔案與 integrity

Candidate runtime 使用以下 Apache-2.0 固定 checkpoint：

| Key | Architecture | Model | Revision | Weight size | Weight SHA-256 | Threshold |
| --- | --- | --- | --- | ---: | --- | ---: |
| CM | cross encoder | whoisjones/otter-cross-mmbert | 8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d | 1,235,084,300 bytes | 8987080bfc3e6672a75fb19ffe904d39e79ad804eabfda8247a62c347fb024b2 | 0.04 |
| BM | bi encoder | whoisjones/otter-bi-mmbert | 53e10a09bc71a2e45980a7a257233a28305a5777 | 1,906,302,124 bytes | 05c4f718fb9e5871d66b8eb68fc40e17d0d8611c5b8e6252e371662bc0f78c91 | 0.05 |

兩者使用 mmBERT、max sequence length 1024、max span length 30。BM 另需
jhu-clsp/mmBERT-base tokenizer revision
c5955035435e2bf121cde7f3c8863ef52ff35d82：

| 檔案 | 大小 | SHA-256 |
| --- | ---: | --- |
| runtime_tokenizer/special_tokens_map.json | 636 | baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351 |
| runtime_tokenizer/tokenizer.json | 17,525,329 | 197d4cc5406ee12cc50c8b5511f2393cc32d9db321545979ce041c1199178356 |
| runtime_tokenizer/tokenizer_config.json | 46,440 | 1d2f82c1341a79748e00efe82e67690f99d00b3c2a894f2b23128fd9d3519da3 |

每個 checkpoint 另需六個 pinned remote-code 檔：collate_fn.py、
configuration_otter.py、loss.py、masks.py、metrics.py、modeling_otter.py；
config.json、model.safetensors、token_encoder_config.json。CM 另需
tokenizer.json、tokenizer_config.json、special_tokens_map.json。BM 另需
type_encoder_config.json、token_tokenizer/tokenizer.json、
token_tokenizer/tokenizer_config.json、type_tokenizer/tokenizer.json、
type_tokenizer/tokenizer_config.json。Provisioner 只複製 allowlist 內的 regular
files，並在完整驗證後寫入 manifest。

兩個 weight 合計約 3.14 GB；這不是最低 RAM 保證。Startup 會檢查 manifest
identity、必要檔案、大小、hash、tokenizer metadata、regular-file 邊界與 symlink
規則。Startup 會信任宣告的 weight identity 並回報 TRUSTED_SOURCE_NOT_HASHED；
明確執行 check 時才會以 FULL_SHA256 驗證目前 bytes。

## 下載與驗證

Provisioning 使用既有 huggingface_hub cache 與 resume 行為。檔案先放在 final
checkpoint 外的 staging，Hub cache symlink 會被複製成 regular file，完整 manifest
驗證成功後才 publish：

~~~bash
entity-resolver identities
entity-resolver download --artifact-root .local-artifacts
entity-resolver check --artifact-root .local-artifacts
~~~

Final path 由 model ID 與 revision 決定：

~~~text
.local-artifacts/whoisjones--otter-cross-mmbert/8729188e4f5fc7948d0e9dfd7d7e6d36c2e7270d/
.local-artifacts/whoisjones--otter-bi-mmbert/53e10a09bc71a2e45980a7a257233a28305a5777/
~~~

健康 target 會重用，不會重複下載。損壞的既有 target 會回報並保留，不會靜默
覆寫。staging 失敗或中斷時不會發佈有效 final manifest，也不會刪除 Hub cache。
若 BM 後續失敗，已完成的 CM target 仍可重用。實作沒有自訂的跨程序 download
lock。

Provisioning 與 Otter inference 使用本機 artifact。實際 decision request 會將
raw text、candidate spans、schema criteria 與受限 context 傳送至 TypeSafe；
credentials 與 raw provider body 不會寫入公開 metadata 或 completion telemetry。
若文字敏感，請先確認這項傳輸符合你的部署政策。

## CLI

安裝後的 console script 是 entity-resolver。help 與 identities 不需要 artifacts
或 TypeSafe key：

~~~bash
entity-resolver --help
entity-resolver identities
~~~

完成 provisioning 並 export TypeSafe key 後，可執行：

~~~bash
export TYPESAFE_API_KEY="..."
entity-resolver resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
entity-resolver serve --artifact-root .local-artifacts --schema config/schema.example.json --host 127.0.0.1 --port 8000
~~~

resolve 將 JSON 輸出到 stdout。錯誤會以安全 JSON 輸出到 stderr 並回傳非零
exit status。serve 預設使用 127.0.0.1:8000、單一 worker、沒有 reload 或多 worker，
並在結束時 finally-close service。

## Python API 與 HTTP

呼叫端擁有並負責關閉 resolver：

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

測試或 integration 若要明確注入組合，可使用 bootstrap.build_service 與
runtime.pipeline.PipelineService。Checkout 範例可執行：

~~~bash
python -m examples.resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
~~~

HTTP hosting 使用注入式 FastAPI app：

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

Readiness 與 request：

~~~bash
curl -sS http://127.0.0.1:8000/health/ready
curl -sS -X POST http://127.0.0.1:8000/resolve -H 'content-type: application/json' -d '{"text":"Alice works at Acme in Taipei."}'
~~~

API 提供 POST /resolve、GET /health/ready、/docs。輸入驗證為 HTTP 422；
readiness、admission、provider configuration 為 HTTP 503；candidate/provider
failure 為 HTTP 502；provider timeout 為 HTTP 504。

## Output contract 與限制

Entity span 是 zero-based、end-exclusive 的字元 offset；mention 必須等於
text[start:end]。normalized 是 resolver 的 surface annotation，不是 catalog
grounding。confidence 是原始 TypeSafe decision probability。sources 保留
CM/BM candidate evidence 與 decision-provider evidence。同一 span 的不同 label
可用 CONFLICTED 與 label hypotheses 表示；沒有 entity 時會有
NO_ENTITY_EVIDENCE warning。Metadata 包含 schema、policy、decoder、model/artifact、
provider、prompt 與 timing identity。

以下是可直接交給 `EntityResolutionResult.model_validate` 驗證的示意 contract；
其中 `schema_id` 是 64 字元的十六進位 SHA-256 摘要示意值，實際值會依 schema
內容計算：

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

`span` 使用 zero-based、end-exclusive 字元 offset，`mention` 必須正好等於
`text[start:end]`。EntitySchema 的名稱必須唯一且非空白，description 必須有
非空白內容，rejection label 不得與 entity label 重複；不符合時 schema loader
會拒絕輸入，結果模型也會拒絕未知或不完整欄位。`rejection_label` 用於非 entity、
混合 label 與邊界錯誤，不代表額外的推論類別。

Runtime cache 每個 stage 128 entries、TTL 60 秒；相同 decision request 共用
in-flight work；admission 允許 8 個 active 與 16 個 waiting。失敗 decision 不
進 cache；取消 async caller 不會取消共用工作。

## 客製化地圖與固定限制

在 JSON schema 修改 labels/descriptions，再使用同一組固定 CM/BM checkpoint。
若要替換 CandidateProposer 或 DecisionProvider，請透過 contracts interface 與
明確注入的 PipelineService；目前 artifact/model contract 不是通用 registry。
Service 要求非空白 text、最多 64 candidates；每個 TypeSafe request 最多 16
questions；state/question 上限 30,000 bytes，request 上限 60,000 bytes。
每個 CM/BM candidate probe 的 input 上限是 1024 tokens、span 上限是 30 tokens；
TypeSafe 每個 request 最多 16 個 questions，state/question 上限 30,000 bytes，
request 上限 60,000 bytes。

主要檔案對照：`config/schema.example.json` 管理 labels/descriptions；
`model_specs.py` 管理不可變 pin；`adapters/otter/` 管理 artifact/model；
`adapters/typesafe/wire.py` 管理 provider request/response；`resolution/` 管理
selection/recovery policy；`bootstrap.py` 管理 composition。要替換 proposer 或
provider，請實作 contracts interface 並注入 `PipelineService`，不要把任意模型
直接放入目前固定的 artifact layout。

若 download 回報 target 損壞，請先移開該 target 再重試；工具刻意拒絕靜默覆寫。
若 check 失敗，請檢查 model key、revision、manifest、檔案大小與 SHA-256。Startup
可能回報 `TRUSTED_SOURCE_NOT_HASHED`，此時應執行 check 取得 `FULL_SHA256` 驗證。
若 resolve/serve 回報 `DECISION_CONFIGURATION_ERROR`，請 export
`TYPESAFE_API_KEY` 並安裝 decision extra；系統不會自動讀取 `.env`。若 provider
timeout，確認網路連線並依安全 error code 處理；raw provider response 不會公開。
若 CLI 回報 `MISSING_DEPENDENCY` 或 `INVALID_CONFIGURATION`，請安裝對應的
`api`、`otter` 或 `decision` extra，並檢查 schema/artifact-root 參數。若輸入超過
runtime 限制，安全錯誤碼是 `QUERY_TOO_LONG`（HTTP 422），請縮短文字後重試。

Runtime 不會自動下載模型：先以 `download` 完成顯式 provisioning，再由 resolve 或
serve 載入已發佈 artifacts。

## Developer checks 與實際證據

~~~bash
source .venv/bin/activate
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
git diff --check
~~~

Repository checks 涵蓋 resolver contract、artifact trust boundary、provisioning
失敗路徑、lifecycle、CLI/API 行為、packaging 與 import-time side effects；可用上方
指令重跑完整檢查。

實際本機證據：download 產生 CM 12 個、BM 17 個檔案並通過 FULL_SHA256；
check 與 repeat-download reuse 通過。在 HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1
下，兩個 pinned Otter model 均成功 load、warmup，提出 4 個候選並成功 close。
實際 CLI resolve 也以 TypeSafe model `jev-1.13.0` 成功完成：範例句回傳 Alice
PERSON [0, 5)、Acme ORGANIZATION [15, 19)、Taipei PLACE [23, 29)，且沒有 warning。
測試 harness 才載入被 gitignore 的本機 `.env`；framework 本身不會自動載入 `.env`。
注入式 FastAPI `TestClient` 也回傳 readiness 200、相同英文結果，以及中文
`王小明在台北的台積電工作。` 的王小明 PERSON [0, 3)、台北 PLACE [4, 6)、台積電
ORGANIZATION [7, 10)；`....` 回傳空 entities 與 `NO_ENTITY_EVIDENCE`，空白 request
回傳 HTTP 422。這些是本機驗證結果，不代表 production-ready 保證。
