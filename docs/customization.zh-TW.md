# 客製化指南

[README](../README.zh-TW.md) · [English](customization.md) · [使用指南](usage.zh-TW.md)

## 定義實體類別

修改 [config/schema.example.json](../config/schema.example.json)，
或透過 `--schema` 指定另一份 JSON 檔案：

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

- 名稱需唯一且非空白，前後不可有空白，也不可包含換行。
- 描述需包含非空白內容，說明哪些實體符合該類別。
- `rejection_label` 用於非實體、混合類別或邊界錯誤，不可與實體名稱重複。
- 候選擷取使用描述；decision model 使用名稱、描述與拒絕選項。分數相同時依 schema 順序判定。
- 修改 schema 後需重新建立服務，讓 schema 與提示詞的識別資訊一併更新。

Python 呼叫端也可直接建立 `EntitySchema` 與 `EntityLabel`，詳見
[定義與驗證規則](../contracts/models.py)。

## 篩選與撈回

README 呈現概念流程，目前實作使用以下規則：

| 步驟 | 規則 |
| --- | --- |
| 信心篩選 | 保留已接受且原始決策機率 ≥ 0.90 的候選。 |
| 包含關係 NMS | 同類別片段任一方完整包含另一方，且已保留片段的分數不低於另一候選時，抑制另一候選（最小分數差為 0.0）。 |
| 空缺撈回 | 在至少兩個字元的合格未覆蓋區間內，選回已接受、分數 ≥ 0.80 且完整位於該區間的候選。 |
| 最終選擇 | 對撈回候選套用 NMS，再與主要結果合併。 |

NMS 為非極大值抑制（Non-Maximum Suppression）。候選依分數由高至低處理；
同分時依起點、終點、候選 ID 由小至大決定順序。只有部分重疊、沒有完整包含關係的片段
不會因此被抑制。IoU（Intersection over Union，交集占聯集比例）只記錄於抑制追蹤資料，
不作為篩選門檻。

撈回沿用原本的決策結果。合格空缺排除主要結果與受保護詞語，長度至少兩個字元，
且至少包含一個非空白、非標點、非符號的字元；補回候選必須完整落在其中一個空缺內。
此步驟不會額外呼叫模型。詞語保護會比對設定中獨立出現的線索，例如「不要」、「或」，
前後需為原文起訖或非 Unicode 文數字及底線的字元，因此不會比對連續中文名稱內的線索；
不推論應用邏輯或偏好。

內部設定識別值 `CN90`、`CN90_GAP80_V1` 與公開 metadata 識別值
`CONFIDENCE_90_GAP_80` 保持固定，以維持序列化與追蹤資訊。它們分別對應上述主要篩選
與結合空缺補回的規則，並非其他模型名稱。

完整策略請見 [selection.py](../resolution/selection.py) 與
[recovery.py](../resolution/recovery.py)；修改規則時需一併調整相關測試。

## 限制

| 邊界 | 目前上限 | 來源 |
| --- | --- | --- |
| 候選模型輸入／片段 | 每次模型探測 1,024／30 tokens | [模型規格](../model_specs.py) |
| 候選集合 | 64 個候選 | [流程資料規格](../contracts/pipeline.py) |
| 決策請求 | 16 個問題；每份 state/question 30,000 bytes；每次 request 60,000 bytes | [流程資料規格](../contracts/pipeline.py) |
| 請求容量 | 同時處理 8 個、等待 16 個 | [執行設定](../runtime/config.py) |
| 快取 | 每階段 128 筆；TTL 60 秒 | [執行設定](../runtime/config.py) |

輸入文字不可為空白。相同的決策請求共用進行中的工作；失敗結果不會快取。
取消非同步呼叫端不會取消共用工作。模型權重的下載大小不代表最低 RAM 需求。

## 模型介面與程式碼位置

概念圖不綁定模型；目前內建實作使用本機 Otter 候選模型與 TypeSafe 決策服務。
修改 schema 只會改變實體類別，不會更換模型架構或 checkpoint。

自訂整合可實作[候選擷取／決策服務介面](../contracts/interfaces.py)，
組成 [PipelineService](../runtime/pipeline.py) 後傳入 [`build_service`](../bootstrap.py)。
目前候選資料仍會驗證固定模型的來源資訊，resolver 也依賴這些來源識別。
替換候選模型時，除了 adapter，也需要調整資料規格與測試；目前沒有任意模型註冊機制。

| 路徑 | 責任 |
| --- | --- |
| [contracts/](../contracts/) | Schema、請求與結果模型、候選與決策資料規格及介面 |
| [resolution/](../resolution/) | 信心篩選、NMS、撈回與結果組合 |
| [runtime/](../runtime/) | Resolver 生命週期、就緒狀態、快取與請求容量 |
| [adapters/otter/](../adapters/otter/) | 本機候選推論、位置對齊與模型下載 |
| [adapters/typesafe/](../adapters/typesafe/) | 決策請求、回應驗證與服務生命週期 |
| [transport/](../transport/) | CLI 與 HTTP 端點 |
| [bootstrap.py](../bootstrap.py) | 服務組合 |
| [model_specs.py](../model_specs.py) | 模型識別、雜湊、tokenizer 檔案與固定依賴版本 |

## 開發檢查

完成快速開始的環境安裝後，可執行：

```bash
uv sync --frozen --group dev --extra api --extra otter --extra decision
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv lock --check
uv build
git diff --check
```

測試涵蓋資料規格、篩選、模型驗證、下載、生命週期、CLI／HTTP 行為與套件打包。
調整文件描述的行為時，請同步維護兩份 README，以及使用指南、客製化指南的中英文版本。
