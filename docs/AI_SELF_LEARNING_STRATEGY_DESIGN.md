# Polymarket Copybot AI 自学习与稳定收益设计书

最后更新：2026-04-21（Asia/Tokyo）

## 1. 目的与边界

这份设计书的目标不是让系统“无约束自动交易”，而是把当前跟单系统建设成一套可长期维护、可持续研究、可受控优化的研究与执行平台。

核心目标：

- 用 settled data 而不是浮盈/浮亏做策略学习
- 让 AI 能基于历史样本提出参数调整和实验建议
- 所有高风险改动先进入 shadow / experiment，再决定是否推广
- 让实盘策略尽量追求稳定正期望，而不是追求单日爆发

明确边界：

- AI 不得直接关闭核心防守层并自动投入实盘
- AI 不得依据未结算样本自动调大仓位
- AI 不得绕过人工或治理规则直接修改 `DRY_RUN=false`
- “稳定收益”只能通过长期正期望、低滑点、低回撤来逼近，不能承诺

## 2. 当前基线快照

基线时间：2026-04-21 16:24 JST  
建议刷新命令：

```bash
python report.py --days 3 --top 5
```

当前关键数据：

- `signals = 120950`
- `research_entries = 1595`
- `executed_entries = 89`
- `executed_closed = 70`
- `executed_decision_count = 68`
- `executed_win_rate = 47.1%`
- `executed_realized_pnl = -16.03`
- `shadow_entries = 1499`
- `shadow_closed = 1285`
- `shadow_win_rate = 67.1%`
- `shadow_realized_pnl = +129.08`
- `stage2_repeat_entry_experiment = 7 entries / 2 closed / -1.21 pnl`

当前阻断理由第一焦点：

- `Repeat Entry Limit`
- `512` 个 blocked shadow 样本
- `343` 个已结算
- `35.7%` 决策胜率
- `+74.33` shadow realized pnl

当前结论：

- 第一阶段“最该优化哪种阻断理由”已经完成
- 第二阶段 `Repeat Entry Limit` 实验已经启动，但样本远远不够
- 当前 executed 样本仍然为负，不具备“稳定实盘收入”资格
- 当前 shadow 正收益不能直接当成可执行 edge

## 3. 已实现能力盘点

### 3.1 已实现

#### 市场发现与扫描

- 已支持 `sports + esports` 市场范围控制
- 已支持多 leaderboard slice 合并发现候选交易员
- 已支持并行拉取 trader activity，避免扩大观察池后信号过期

对应实现：

- `config.py`
- `leaderboard.py`
- `monitor.py`
- `market_scope.py`

#### 交易员筛选与风控画像

- 已支持 trader quality score
- 已支持 `micro_trade_ratio / burst_60s / same_second_burst / flip_rate`
- 已支持 trader profile history 持久化
- 已支持 approved / blocked / observe 三态

对应实现：

- `strategy.py`
- `models.py`

#### 复制执行与防守层

- 已实现确认窗口、信号过期校验
- 已实现 whipsaw / reverse trap 防护
- 已实现 orderbook 价差、深度、drift、impact 保护
- 已实现 price band 保护
- 已实现 market / trader / daily risk / max positions 等资本类保护
- 已实现 repeat-entry 限制与 cooldown

对应实现：

- `risk.py`
- `liquidity.py`
- `executor.py`

#### 研究数据留存

- 已支持 `executed` 样本独立记账
- 已支持 blocked signal 写入 `shadow` 样本
- 已支持记录 signal price / tradable price / protected price / final exit
- 已支持 settlement backfill
- 已支持按 `entry_reason` 做阻断理由聚合分析

对应实现：

- `models.py`
- `settlement.py`
- `report.py`

#### 第二阶段实验

- 已实现 `Repeat Entry Limit` 的 stage-2 shadow experiment
- 已支持 `sample_type=experiment`
- 已支持 `experiment_key=repeat_entry_stage2`
- 已支持 CLI / Web 展示 stage2 状态

对应实现：

- `config.py`
- `risk.py`
- `executor.py`
- `models.py`
- `report.py`
- `web.py`
- `templates/index.html`

### 3.2 部分实现

#### 研究流程治理

- 已有 experiment 样本与 control 样本分离
- 但 experiment framework 仍是单点实现，只覆盖 repeat-entry

#### 报表与运营观察

- 已支持 Web + CLI 基础研究报表
- 但还没有统一的 strategy registry、experiment lifecycle、promotion dashboard

## 4. 当前不足

下面这些不足，是基于当前实际运行结果，不是空想。

### 4.1 执行样本仍为负，说明实盘稳定性没有被证明

当前 executed 样本：

- `89` 笔 executed
- `70` 笔已平仓
- `realized_pnl = -16.03`

这意味着：

- 观察样本量已经能说明问题
- 当前主执行策略不能被认定为稳定盈利
- 任何“直接上实盘做稳收入”的结论都不成立

### 4.2 shadow 为正，不等于可执行为正

当前 shadow 数据明显好于 executed：

- `shadow_realized_pnl = +129.08`
- `executed_realized_pnl = -16.03`

说明两个问题：

- 被挡掉的单里确实存在机会
- 但当前可执行入口、可成交价格、可持仓路径仍然没有被打磨好

结论：

- 不能简单放松阻断
- 必须先做更细粒度的实验和归因

### 4.3 第二阶段样本极少，暂时不能据此动默认参数

当前 stage2 repeat-entry experiment：

- `7` 笔样本
- `2` 笔已结算
- `realized_pnl = -1.21`
- 市场集中度极高：closed 样本全部集中在一个 market

这只能说明：

- 功能已打通
- 尚未形成可判断的统计结论

### 4.4 当前系统还不是“AI 自学习”，而是“规则驱动 + AI 辅助分析”的雏形

当前系统能做：

- 收集样本
- 输出统计
- 做局部实验

当前系统还不能做：

- 自动构建 feature store
- 自动跑 walk-forward evaluation
- 自动给出可信的参数推荐排序
- 自动版本化策略
- 自动把“研究结论”转成“受控配置变更”

### 4.5 缺少策略版本与配置快照

当前 journal 已记录价格、样本类型、实验 key，但还没有完整记录：

- 当时的策略版本
- 当时的 `.env` 核心参数快照
- 当时 trader score / orderbook feature / market state feature 的冻结值

这会导致后续归因困难：

- 无法完全回答“这笔单为什么被允许/阻断”
- 无法精确比较“新旧参数”的真实差异

### 4.6 缺少统一的指标口径治理

当前系统已经有较丰富的数据输出，但还缺：

- 单一权威指标定义
- 统一的 win rate 口径
- control / shadow / experiment / executed 的统一可视化比较

长期维护时，指标口径不统一会直接误导 AI 和人。

## 5. 长期设计原则

### 5.1 AI 只能提出建议，不能直接无门槛改实盘

必须坚持：

- AI 提建议
- 系统跑实验
- 指标达标后才进入候选配置
- 实盘推广必须经过明确门槛

### 5.2 所有学习都基于 settled data

禁止：

- 用 open PnL 训练
- 用未结算浮盈驱动参数放宽
- 用短期热手 trader 直接提权

### 5.3 先隔离实验，再决定是否推广

所有参数调整路径必须遵守：

1. control 观察
2. shadow experiment
3. repeated validation
4. staged live rollout

### 5.4 核心防守层默认是 hard guard，不是 soft preference

默认不可自动放松：

- `whipsaw`
- `orderbook drift`
- `spread`
- `impact`
- `trader quality`
- `price band`

这些层只有在独立实验充分证明后，才允许做窄范围调整。

## 6. AI 自学习总体架构

目标架构分为六层。

### 6.1 Data Capture Layer

负责记录：

- raw signal
- trader profile snapshot
- orderbook assessment
- executed / shadow / experiment journal
- settlement outcome

现状：

- 大部分已实现

仍需补充：

- strategy version
- config snapshot
- richer per-trade features

### 6.2 Feature Store Layer

目标是把每一笔样本结构化为 AI 可分析特征：

- trader features
- market features
- execution features
- timing features
- risk reason features
- experiment context features

建议新增：

- `trade_features` 表
- `config_snapshots` 表
- `strategy_versions` 表

### 6.3 Evaluation Layer

负责输出：

- overall metrics
- per-market metrics
- per-trader metrics
- per-block-reason metrics
- control vs experiment 对比
- time-sliced / sport-sliced / regime-sliced 分析

建议新增：

- `analysis.py` 或 `evaluator.py`
- 支持滚动窗口、按 sport code 分层、按 market regime 分层

### 6.4 Recommendation Layer

AI 在这一层做的不是“直接调参数”，而是：

- 识别主要瓶颈
- 生成参数变更候选
- 对每个候选给出风险、影响面、样本覆盖度
- 决定先开哪一个实验

输出格式建议：

- `hypothesis`
- `target_parameter`
- `expected_effect`
- `risk`
- `required_sample_size`
- `promotion_rule`

### 6.5 Experiment Layer

实验层必须通用化，不应只支持 repeat-entry。

建议未来支持：

- `repeat_entry_stage2`
- `small_drift_band_stage2`
- `delayed_recheck_no_book_stage2`
- `thin_top_level_small_size_stage2`
- `consensus_gate_stage2`

实验统一字段建议：

- `experiment_key`
- `experiment_group`
- `parent_rule`
- `control_definition`
- `eligibility_reason`
- `skip_reason`

### 6.6 Promotion Layer

Promotion 不应直接写实盘参数，而是：

1. 进入 `candidate`
2. 低资金 live canary
3. 分阶段放量
4. 任一阶段失真立即 rollback

## 7. AI 自学习闭环设计

长期维护推荐使用如下闭环。

### 7.1 日内循环

每个 cycle：

- 扫描新 signal
- 执行 current policy
- 记录 blocked shadow
- 对满足条件的 experiment 单独记样本

### 7.2 每日循环

每日固定时间：

- 刷新 settlement
- 更新 experiment metrics
- 重新生成 block-reason ranking
- 输出“最值得优化的 1-2 个方向”

### 7.3 每周循环

每周进行：

- feature importance 回顾
- trader stability review
- per-sport / per-market regime review
- experiment continuation / abort / graduate decision

### 7.4 每次参数变更循环

参数变更必须满足：

1. 有明确假设
2. 有明确 control
3. 有明确样本门槛
4. 有明确回滚条件

## 8. 实盘稳定化设计

### 8.1 当前不具备稳定实盘资格

因为：

- executed pnl 当前为负
- executed 样本量仍不够大
- stage2 样本太少
- control 与 experiment 尚未形成可稳定复制的正期望

### 8.2 实盘推广门槛

建议最低门槛：

- 主执行策略最近 100+ 个已结算 executed 样本为正
- 最近 30 天滚动窗口为正
- 最大单一市场占比不过高
- 最大单一交易员贡献不过高
- 关键 hard guard 没有被放松后失真

### 8.3 live rollout 分级

建议分级：

- `L0`: DRY_RUN only
- `L1`: 小额 canary，例如 `$5-$20`
- `L2`: 中额 canary，例如 `$20-$50`
- `L3`: 正式低仓位 live

每一级都必须满足：

- pnl 为正
- drawdown 在阈值内
- slip / drift / impact 不劣化

### 8.4 自动回滚条件

必须存在：

- 连续亏损阈值
- 单日回撤阈值
- drift 恶化阈值
- experiment 失真阈值

触发即自动退回更保守配置。

## 9. 已实现 / 不足 / 下一步清单

### 9.1 已实现

- 市场范围控制
- 扩大 leaderboard discovery
- 并行 trader activity 抓取
- trader profile / history
- blocked shadow journaling
- settlement backfill
- block reason grouped analytics
- repeat-entry stage2 experiment
- web / cli 报表

### 9.2 仍然不足

- strategy versioning
- config snapshotting
- richer feature store
- experiment diagnostics（eligible / skipped by reason）
- walk-forward evaluator
- automatic parameter ranking
- promotion governance
- live canary orchestration

### 9.3 立即优先级

优先级 1：

- 加 `strategy_version`、`config_snapshot_id`
- 加 stage2 diagnostics
- 统一指标定义

优先级 2：

- 做通用 experiment registry
- 做 delayed recheck / small drift 实验框架

优先级 3：

- 做 AI recommendation pipeline
- 做 candidate -> canary -> live promotion pipeline

## 10. GitHub 维护规范

这份设计书建议作为仓库内长期维护文档。

推荐规则：

- 每次关键策略改动必须更新本设计书中的“当前状态”或“路线图”
- 每次新增 experiment 都要补 `experiment_key` 说明
- 每次报告口径变化都要更新“指标定义”
- 每次进入新 live 等级都必须写明 promotion 条件和 rollback 条件

建议维护流程：

1. 在 GitHub 上以 issue 记录问题或假设
2. 用 branch / PR 实现实验或修复
3. 合并前附最近一轮 settled data 结果
4. 合并后更新本设计书

推荐 issue 分类：

- `design`
- `risk`
- `experiment`
- `metrics`
- `live-readiness`

## 11. 结论

当前系统已经从“单纯跟单脚本”进入了“研究型执行系统”的阶段，但离“可稳定实盘收入系统”还有明显距离。

最重要的现实判断是：

- 第一阶段分析已经完成
- 第二阶段实验已经打通
- 当前还没有证据证明主执行策略具备稳定正收益

因此，长期正确方向不是让 AI 自由改策略，而是让 AI 成为一个受控研究引擎：

- 自动整理 settled data
- 自动识别瓶颈
- 自动生成实验建议
- 自动比较 control 与 experiment
- 只在满足硬性门槛时推进更高一级执行

这才是这个项目走向长期可维护、并朝稳定收益逼近的正确路线。
