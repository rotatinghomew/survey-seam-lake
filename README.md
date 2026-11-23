# 古建测绘接茬湖

该项目保存古建测绘队交付的控制点、覆盖区和构件观测，并提供一个纯 Python 的接茬台账（`seam_survey`），供设计底图交接使用。`data/facts.json` 是本地合成空间记录，当前只含控制网、相邻责任区与两条跨缝观测，属于**草图**：其中没有批次、控制链接和验收记录，加载器会如实报告缺少的部分，不会凭空补齐。

需要 Python 3.11 或更新版本。测试不依赖网络或第三方包。

```bash
python3 -m unittest discover -s tests
```

## 台账解决什么

`SurveyLedger` 把三件事严格分开：

1. **原始测量**：按交付队伍自己的控制网版本和单位（毫米或米）原样保存，不改写。
2. **控制链接**：只在登记过的同名点号、明确版本之间拟合相似变换，并检查公共点数量、残差和尺度。绝不按“离得最近”猜同名点。
3. **公共坐标覆盖**：设计单位引用的是分区快照；覆盖考核始终在项目公共坐标系里、对事先登记的责任区进行。

一次典型的接茬流程：

```python
from seam_survey import SurveyLedger, Project, ControlNetwork, Zone, SurveyBatch, FeatureObservation, SeamGroup, ReviewRecord

ledger = SurveyLedger(Project("P1", "项目", "SITE", 2), min_common_points=3, residual_tolerance_m=0.02)
ledger.register_network(ControlNetwork("LOCAL-A", "LOCAL-A", "mm", 1,
    {"CP-1": (0, 0), "CP-2": (10000, 0), "CP-3": (0, 10000)}))
ledger.register_network(ControlNetwork("SITE", "SITE", "m", 2,
    {"CP-1": (1000, 1000), "CP-2": (1010, 1000), "CP-3": (1000, 1010), ...}))
ledger.link_networks("LINK-A", ("LOCAL-A", 1), ("SITE", 2))

ledger.register_zone(Zone("ZONE-E", "TEAM-A", [(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)]))
ledger.register_batch(SurveyBatch("BATCH-A", "ZONE-E", "LOCAL-A", 1, coverage=[(0, 0), (10000, 0), (10000, 10000), (0, 10000)]))
ledger.register_seam(SeamGroup("SEAM-1", "ZONE-E", "ZONE-W", ("F-A-01", "F-B-09")))
```

### 控制与单位

* 控制链接用最小二乘相似变换拟合，仅在登记的公共点号上求值；公共点不足、残差超限或尺度冲突都会拒绝并给出可核查的 `details`。
* 毫米 / 米的换算只来自控制网声明的 `unit`，不会因为数值大小“看起来像”而自动缩放。
* 登记新版控制网**不会**自动让旧版失效；旧版可继续服务已挂接的批次。需要正式停用时调用 `supersede_network`，或通过 `advance_common_network` 提升公共坐标框架（提升后旧公共框架会被停用）。
* 新批次若要出具分区，必须走当前有效的控制依据（`superseded_control_basis` 会拦住已停用的依据）；`reassign_batch_network` 可以在不改原始坐标的前提下把批次改挂到新版本。历史快照始终可读。

### 接茬与验收

* 接茬疑点必须**两端各有一条观测**才会登记（`seam_missing_side`）。
* 验收记录只增不改。同一对象（`same_object`）必须指定主测和可对照的复测，且两者在公共坐标下确实落在同一位置；两件相邻对象（`two_objects`）则两条观测都计数。
* 未决接茬的观测在分区里保留为 `pending` 证据，但**不计数**，也不能拿来填覆盖缺口（`duplicate_points_fill_gap`）。
* 验收员之后补交对照或控制依据，可直接让同一批次的草稿通过交付，无需改动原始观测。

### 分区、交付与引用

* `build_partition` 生成不可变快照；`deliver_partition` 才做交付裁决。
* 覆盖在公共坐标下按责任区计算，缺口超过容差即不可交付。
* 设计引用通过 `pin_design` 锁定一组相邻分区；被引用的分区**不得原地换坐标**（`partition_locked`），历史版本始终可按版本号取回（`trace_partition(id, edition)`）。
* `replace_partition` 生成带 `replaces` 血缘的新快照，旧快照保留。
* `handover` 汇总：分组覆盖、同一责任区不得新旧并存、接茬两端控制依据是否共同成立、重名构件是否重复计数、以及逐对分区的接缝距离。
* 快照保留其被切出时的公共坐标框架。不同框架切出的两个分区不会被静默混合，`handover` 会以 `seam_frame_mismatch` 拒绝；需要重新切一份（新分区，而非原地改已引用分区）。在同一个（可能是历史）框架里切出的两个快照，只要相对控制依据仍一致，其接缝可以继续成立。
* 一组新旧测量各自满足本区覆盖、拼起来却留缝或重复构件时，`handover` / `replacement_plan` 会判为不能交割。
* 某个接茬被验收卡住时，其他不受影响的分区仍可单独交付。
* `replacement_plan` 还会列出受影响的接茬、被引用的图纸，以及一组接替分区能否**一起**接手；`can_replace_together` 为假时会给出 `group_issues`。

### 可回查

`trace_partition` 返回分区、责任区、批次、源控制网、公共控制网、完整变换（含每个连接与残差）、原始观测坐标、接茬评估和引用它的图纸。`dump()` 输出可审阅的登记与分区历史。

## 草图加载与检查

```bash
python3 -m seam_survey.cli data/facts.json
python3 -m seam_survey.cli data/facts.json --json
```

```python
from seam_survey.facts import load_facts
ledger, gaps = load_facts("data/facts.json")
```

`gaps` 会列出草图中尚未具备的内容。当前草图会报告：

* `D-22` 引用了分区名而不是已交付分区编号，无法解析；
* 登记的 `ZONE-E` 与 `ZONE-W` 责任区在接缝处相差 0.1 m（`zone_responsibility_gap`）——这正是控制网成果改变局部坐标后需要排查的情形；
* 草图没有登记接茬分组或验收记录。

草图加载不会把缺项补齐，也不会把 0.1 m 的缝“抹平”。

## 目录

* `seam_survey/` — 台账实现
  * `models.py` 领域记录（控制网、责任区、批次、验收、分区、设计引用）
  * `geometry.py` 凸多边形裁剪/差集/并集面积与图形相交判断
  * `transform.py` 控制链接拟合与容差校验
  * `lineage.py` 已登记链接上的坐标谱系与逆变换
  * `ledger.py` 登记、诊断、接茬、分区、交付、交接与回查
  * `facts.py` 草图加载
  * `cli.py` 草图检查命令
* `tests/` — 单元测试（几何、控制、接茬、分区生命周期、交接、控制版本变更、重控流程、草图加载、序列化、命令行）
