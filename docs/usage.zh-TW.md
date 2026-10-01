# 使用指南

[README](../README.zh-TW.md) · [English](usage.md) · [客製化指南](customization.zh-TW.md)

<a id="environment"></a>
## 環境設定

完成[快速開始](../README.zh-TW.md#快速開始)後，請在 repository 根目錄執行指令。
`uv run --no-sync` 使用 `uv sync` 已安裝的環境，保留所選的 extras。

| 需求 | 目前設定 |
| --- | --- |
| Python | 3.12 或 3.13 |
| Linux CPU wheels | `x86_64`、`aarch64` |
| Windows CPU wheels | `AMD64`、`ARM64` |
| macOS | 目前 `otter` extra 尚未設定固定版本的 PyTorch wheel |
| 模型儲存空間 | 權重約 3.14 GB，另需 tokenizer 檔案、快取與下載暫存空間 |
| 決策服務 | TypeSafe API key 與網路連線 |

依賴群組分為本機候選推論使用的 `otter`、決策服務使用的 `decision`，以及 HTTP 服務使用的 `api`。
確切版本請見 [pyproject.toml](../pyproject.toml) 與 [uv.lock](../uv.lock)。本機推論使用 CPU float32。

在啟動 resolver 的 shell 設定金鑰；程式不會讀取 `.env`。

```bash
export TYPESAFE_API_KEY="your-api-key"
```

PowerShell 請使用以下語法；指令寫在同一行，不使用 Bash 的 `\` 換行符號：

```powershell
$env:TYPESAFE_API_KEY = "your-api-key"
uv run --no-sync entity-resolver resolve --artifact-root .local-artifacts --schema config/schema.example.json --text "Alice works at Acme in Taipei."
```

候選擷取在本機執行。決策請求會將原始文字、候選區間、schema 判斷條件與上下文傳送至 TypeSafe。
金鑰與服務原始回應不會寫入公開的結果 metadata 或執行紀錄。

## 模型檔案

```bash
uv run --no-sync entity-resolver identities
uv run --no-sync entity-resolver download --artifact-root .local-artifacts
uv run --no-sync entity-resolver check --artifact-root .local-artifacts
```

- 這些指令與 `--help` 都不需要 TypeSafe 金鑰。
- `download` 使用 Hugging Face 快取，驗證檔案後才將模型放入正式目錄。
- 完整的模型目錄會直接重用；損壞的目錄會保留並回報錯誤，請先移開再重試。
- 下載中斷後仍可重用 Hub 快取；若其中一個 checkpoint 已完成，也可繼續使用。
- 啟動時會檢查 manifest 與檔案結構，權重驗證狀態為 `TRUSTED_SOURCE_NOT_HASHED`；`check` 才會對目前檔案計算雜湊，回報 `FULL_SHA256`。
- 執行時不會下載缺少的模型。下載沒有自訂的跨程序鎖，請避免同時寫入相同目錄。

模型版本與雜湊請見 [model_specs.py](../model_specs.py)；
必要檔案清單請見 [provisioning.py](../adapters/otter/provisioning.py)。

## Python

呼叫端負責建立並關閉 resolver：

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

請在已安裝依賴的環境中執行；完整的 Python 命令列範例位於
[examples/resolve.py](../examples/resolve.py)。

## HTTP

啟動伺服器後，在另一個終端機送出請求：

```bash
uv run --no-sync entity-resolver serve --artifact-root .local-artifacts --schema config/schema.example.json
```

```bash
curl -sS http://127.0.0.1:8000/health/ready
curl -sS -X POST http://127.0.0.1:8000/resolve \
  -H 'content-type: application/json' \
  -d '{"text":"Alice works at Acme in Taipei."}'
```

- 預設為 `127.0.0.1:8000`、單一 worker、不啟用 reload；可用 `--host`、`--port` 調整位址。
- 開啟伺服器的 `/docs` 即可查看 API 文件。
- 整合現有應用時，可將新建的 resolver 傳給 `transport.api.create_app(service)`。伺服器停止後，由呼叫端關閉 resolver；app 本身不管理關閉流程。

| HTTP 狀態碼 | 意義 |
| --- | --- |
| 422 | 輸入無效或文字超過執行限制 |
| 503 | 服務未就緒、請求容量已滿，或決策服務設定有誤 |
| 502 | 候選擷取或決策服務失敗 |
| 504 | 決策服務逾時 |

## 輸出欄位

完整規格請見 [EntityResolutionResult](../contracts/models.py)。

| 欄位 | 意義 |
| --- | --- |
| `text` | 原始輸入 |
| `entities[].mention` | 與 `text[start:end]` 完全相同的片段 |
| `entities[].span` | 從 0 起算的字元位置，不包含 `end` |
| `entities[].label` | 自訂的實體類別之一 |
| `entities[].confidence` | 決策服務回傳的原始機率 |
| `entities[].normalized` | 原文表面標註，不查詢資料目錄 ID |
| `entities[].sources` | 候選與決策服務的來源證據 |
| `entities[].resolution_status` / `conflict` | 擷取狀態與同一區間的類別衝突 |
| `warnings` | 未找到實體時包含 `NO_ENTITY_EVIDENCE` |
| `metadata` | Schema、策略、模型、提示詞與執行時間資訊 |

CLI 結果寫入 stdout；失敗時將 JSON 錯誤寫入 stderr，並回傳非零結束碼。

## 問題排查

| 狀況 | 處理方式 |
| --- | --- |
| `DECISION_CONFIGURATION_ERROR` | 設定 `TYPESAFE_API_KEY` 並安裝 `decision` extra。 |
| `MISSING_DEPENDENCY` | 安裝所需的 `otter`、`decision` 或 `api` extra。 |
| `INVALID_CONFIGURATION` | 檢查 schema 與模型路徑，並執行 `check` 驗證模型。 |
| 模型目錄損壞 | 將受影響的 checkpoint 目錄移開，再執行 `download`。 |
| `QUERY_TOO_LONG` | 縮短輸入文字。 |
| 決策服務逾時或失敗 | 檢查網路與回傳的錯誤碼。 |
| 服務未就緒 | 執行 `check`，並確認目前 CPU 依賴能載入兩個 checkpoint。 |
