# Current Task

## Goal

把 regime detector 的每一个门限都变成「无漂移随机游走零假设下的枢轴量」，
使阈值跨标的、跨时段、跨窗口长度含义一致。

## Current State (2026-09-11)

已完成并全部验证。`qtrader.regime` 现在的核心：

* `slope_z = slope / slope_rw_std`，`slope_rw_std` 来自与 Kalman `P` 并行推进的
  3x3 Lyapunov 递推 `V <- A V A' + s^2 b b'`（`z = (level, slope, y)`）。
  这是线性滤波器在零假设下的抽样尺度，与 `features.trend` /
  `features.momentum.session_reset_norms` 同一原则。不再用 `sqrt(P[1,1])`。
* `er_rw = ER_n * sqrt(n)`，零假设均值 1.0，**与窗口长度无关**。
* `exit_margin >= 0`（越大越粘），取代语义反直觉的 `exit_z`。
* weaken 分支只能回 FLAT，不能直接对翻；`_weakened` 可释放。
* 预设用「模拟随机游走上的每日误入场次数」标定：7.8 / 3.0 / 0.8。

## Completed

- `kalman.py`：`FilterStep`、`slope_rw_std`、独立的 `null_var` 入口
- `config.py`：`er_entry_rw` / `er_hold_rw` / `er_flip_rw` / `exit_margin`，预设重推
- `detector.py`：RW 单位门限、weaken 只回 FLAT 且可释放、`run()` 每帧算一次 session
- `evaluate.py`：`null_entry_rate`、`describe_entries`、空事件表改为报错
- `viz/regime.py`：`plot_regime(..., config=cfg)`
- `notebooks/exploratory_only/regime.ipynb`：改配置 → Run All 的一键校验
- 删除 `er_min_entry` / `er_min_hold` / `exit_z` / `er` / `signed_er`
- tests、CONTEXT §7c、C00 `regime/`、ADR-0009、CHANGELOG

## Findings

QQQ/AAPL/NVDA/TSLA/JPM，2026-06-01→2026-08-01，43 个交易日：

| | 修前 (balanced) | 修后 (balanced) |
|---|---|---|
| `slope_z` sd（跨标的） | 1.44 – 2.03 | **1.03 – 1.07** |
| `P(\|z\|>2)` | 0.13 – 0.30 | ≈0.046 |
| flips / session | 55 – 59 | **4.9 – 6.1** |
| mean trend duration | 5.0 bars | **8.9 – 11.5 bars** |
| 每日误入场（模拟随机游走） | 未测量 | 3.0 |

1. **Lyapunov 零尺度已对蒙特卡洛验证**（4000 条路径，最大偏差 < 6%，与采样噪声同量级）。
2. **`E[ER_n] = 1/sqrt(n)` 精确成立**（n=5,10,20,30,60 全部吻合），
   所以 `ER * sqrt(n)` 是枢轴量：均值 1.00、sd 0.74、q90 = 2.05，与 n 无关。
3. **模型本身修不好，但不必修。** 扫 `lambda_level ∈ [0.01, 100]`，没有任何取值
   能同时让新息变白（acf1 0.62）且 `slope_z` 的 sd 到 1。零尺度绕开了这个问题：
   不需要模型正确，只需要知道噪声会产生多大的斜率。
4. **CUSUM 在 `slope_z` 正确标定后几乎失效。** `cusum_h` 从 1.5 调到 4.0，
   capture 变化 0.00，误入场率变化 0.07 —— 因为 `slope_z` 平滑（lag-1 0.95），
   它穿过 `entry_z` 的时刻晚于 CUSUM 穿过 `h`。保留为持续性旋钮，已在文档中说明。
5. **它在「描述」，不在「预测」，且这一点现在是测出来的**
   （`describe_entries`）：入场时前 10 根已朝声明方向走了 **+1.95σ**（100% 的入场
   前 5/10 根方向一致）；入场后 5–30 根为 **±0.04σ**，`frac_right_way` 0.44–0.53。
   → 下游只能当 gate / veto，**绝不能当 alpha score**。
6. 之前测得的「趋势段内反向 -0.13σ」是**停止规则偏差**，不是真实反转：
   固定视界的前瞻收益是 0。

## Open Issues

- 尚未接到任何 strategy / entry_veto。
- 入场延迟仍有 ~21 根（对 H=30 的标签）。这是 delay/误报的内在权衡，
  notebook 第 6 格把整条前沿画出来了；要更早只能接受更高的误入场率。
- `R` 仍是 40 根滚动方差，跨 session 不清空：开盘 0–10 分钟真实 sd / 所用 sd = 1.33。
  `LocalLinearTrendFilter.step(..., null_var=...)` 就是给 `features.seasonality`
  剖面留的接口，接上即可。
- 只用 `close`。未用 high/low（Parkinson 波动，同样根数方差小得多）与成交量。
- 未做「相对 SPY 残差」的 regime。日内个股趋势大部分是 beta。

## Next Actions

1. `R` / `null_var` 接 `features.seasonality` 的分钟内波动率剖面。
2. 加 Parkinson/真实波幅作为 `R` 的估计量。
3. 做 residual regime（个股 − beta × SPY），与 raw regime 并列。
4. 要当入场门控时，做成 `timestamp x symbol` 的 state frame。

## Files Touched

- `src/qtrader/regime/{kalman,config,detector,evaluate,__init__}.py`
- `src/qtrader/viz/regime.py`
- `tests/unit/test_kalman_cusum.py`
- `notebooks/exploratory_only/regime.ipynb`
- `docs/context/CONTEXT.md`, `docs/context/C00_CODEBASE.md`,
  `docs/adr/ADR-0009-kalman-cusum-regime.md`, `docs/CHANGELOG.md`

## Validation

- `tests/unit/test_kalman_cusum.py` 34 条，全仓 **537 条全过**。
- 新增校准型回归测试：`slope_rw_std` 对蒙特卡洛；`slope_z` 在模拟随机游走上
  sd = 1.00 ± 0.15、`P(|z|>2)` = 0.046 ± 0.03；`E[ER_n·√n] = 1` 于 n = 5/10/20/40；
  每个预设的**零假设误入场率上限**（振荡型检测器会在这里挂掉，而不是在 review 里）；
  weaken 标志可释放；weaken 路径不可直达反向；`exit_margin` 单调变粘。
- notebook 已用 `nbconvert --execute` 全格跑通。
