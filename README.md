# 04-checklane（检道）

Checklane — 数据质检通道：入检批次装载 → 规则命中与严重度裁决 → 隔离/放行签发 → 签发清单与源批对账。

**不是** Feed 多源归并（Feedstitch），**不是** 作业依赖编排（Batchgate），**不是** CDC 变更流按序落库（Changeflow）。
**也不是** 报表看板 / CRUD 后台 / 电商订单 / OA 工单。

## 启动

`ash
docker compose up --build
`

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

本地开发（可选）：

`ash
# backend
cd backend && pip install -r requirements.txt
set DATA_DIR=..\\data
uvicorn app.main:app --reload --port 10400

# frontend
cd frontend && npm install && npm run dev
`

## 主链

登记数据集与规则 → 装载夹具批次入检 → 按规则裁决放行/隔离 → 签发到精选区 → **金标差分闸门** → 批次报告与各列表数字同源

## 金标差分（06 题）

登记过金标的数据集，规则入检签发之后必须再按 `entity_key` 跑一次与金标的差分，**差分通过才算发布成功**：

- **三类差异**：金标缺（精选有、金标无）、精选缺（金标有、精选无）、同键字段不一致（逐字段给出两边值）。
- **任一失败即发布失败**：批次状态转为 `golden_failed`、`released_count` 归零，并撤回该批已写入精选区的行；
  若该键此前已由更早批次签发，撤回时还原旧值，不会误删历史发布。
- **互证**：金标表条数 = 登记条数 = 差分金标键数；精选区本批行数 = 批次签发数 = 规则放行候选数；
  三类差异键数 + 一致键数 = 金标与精选键并集。
- **独立一步**：「金标差分」页可重新触发某批次的差分；通过则补签发，失败则再次撤回。
- 未登记金标的数据集保持原有链路（仅规则入检 + 签发）。

接口：`GET/PUT /api/golden`、`GET /api/golden/{dataset_id}`、`GET /api/golden-diffs`、
`GET/POST /api/batches/{id}/golden-diff`。

## 种子说明

- **Orders Snapshot**：JSONL 商品快照，含必填缺失与类型错误行，用于演示入检与隔离（无金标）。
- **Inventory Snapshot**：库存快照，独立数据集，用于第二路规则演示（无金标）。
- **Refunds Snapshot**：与 `refunds_golden.jsonl` 完全一致 → 差分通过、成功发布。
- **Shipments Snapshot**：与 `shipments_golden.jsonl` 故意不一致（SHP-3 承运商不同、SHP-4 金标有而精选无）
  → 差分失败、批次发布失败并撤回已签发行；另有 `shipments_golden_fixed.jsonl` 可用于演示修正后重跑恢复。

打开「入检台」分别跑 Refunds（成功）与 Shipments（失败），再到「金标差分」/「精选区」/「批次」核对三类差异条数、撤回结果与批次状态。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现，见题单）

当前仓库已实现「单批入检 + 基础规则 + 放行签发」主链。严重度整批阻断、规则包版本、抽样入检、跨字段规则、批次重开、金标对照、批内重复键、放行冻结窗、规则临时豁免、跨数据集参照检查等均在 ackend/app/modules/* 留空包，按题单逐题换主链路实现。
