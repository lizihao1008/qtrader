# ADR-0009: Kalman–CUSUM as the first online regime detector

## Status
Accepted

## Context

The pipeline has a `regime/` slot (market state after features, before ranking)
that was empty. The research question is not "will this bar go up?" but "has
the 1-minute path left a range for an up or down drift, as early as a causal
filter can say so, without lighting up all day in chop?"

A sibling package already owns *consolidation* detection (ADR-0008) and stays
there. This detector is a different object: a three-state (FLAT / UP / DOWN)
filter on one symbol's close. Copying it into `features/` would mix a stateful
online machine with the vectorised causal indicators. Putting it in
`experiments/` would hide a production-shaped API behind a notebook helper.

## Decision

Add `qtrader.regime` with `KalmanCUSUMRegimeDetector`:

* local-linear-trend Kalman **filter** (state = level, slope), `Q` scaled by a
  causal `R`, with an extension point for acceleration-noise `Q`;
* `slope_z` (default) or vol-normalized slope as the CUSUM input, where
  `slope_z` is standardised by the slope's sampling scale **under a driftless
  random walk** — a 3x3 Lyapunov recursion carried beside the Kalman `P`, not
  `sqrt(P[1,1])`;
* two-sided CUSUM, Kaufman ER as an entry gate, hysteresis on exit, optional
  hard reversal;
* Kaufman ER gates in random-walk units (`ER_n * sqrt(n)`, null mean 1.0 for
  every `n`), so the entry window and the tightened window share a threshold;
* after entry, ER is computed only on bars of the current regime; a faded
  `slope_z` tightens that window so a recent against-path can end the trend
  without waiting for the whole-regime ER. That path may only return to FLAT;
* `update(bar)` once per closed bar; `run(df)` is the same loop;
* session reset (overnight gap is not a 1-minute slope);
* evaluation (`delay`, false alarms, flip rate) lives beside the detector and
  may use future-looking `labels.trend_events`. Those labels never enter the
  filter.

It is **not** wired into a strategy in this change. A later gate/veto can
consume the state frame the same way ADR-0008's consolidation mask does.

## Consequences

* `regime/` is no longer "not created yet". `risk/` and `execution/` still are.
* The detector is path-dependent. Tests must replay bar-by-bar; a vectorised
  look-ahead implementation is a defect.
* Presets are quoted by their false-entry rate on simulated driftless random
  walks (`evaluate.null_entry_rate`), which is a property of the thresholds
  alone. They are still not a fit to any universe.
* The detector **describes**, it does not forecast, and this is now measured
  rather than asserted: at entry the previous 10 bars have moved +1.8 sigma in
  the declared direction, while the following 5-30 bars move +/-0.04 sigma.
  Anything downstream must consume the state as a gate or a veto, never as an
  alpha score.
* With `slope_z` correctly scaled the CUSUM is nearly inert — moving `cusum_h`
  from 1.5 to 4.0 changed capture by 0.00 and the null entry rate by 0.07,
  because `slope_z` is smooth and crosses `entry_z` later than the CUSUM
  crosses `h`. It is kept as a persistence knob, not as an ARL-calibrated
  test; its input is autocorrelated (lag-1 ~0.95) so the classical i.i.d.
  calibration does not apply.
* Plotting is plotly (`qtrader.viz.plot_regime`), on the same helpers as
  `price_chart`: exchange-local time, range-breaks, candlesticks, shared
  colours. It is an analyst replay, not a backtest HTML page.

## Alternatives Considered

* **Use `features.trend.drift_zscore` as the regime.** Rejected: that is a
  trailing statistic with a random-walk null, not a CUSUM change-point with
  hysteresis. Both can coexist; they answer different questions. They do now
  share the null the repo standardises everything against.
* **Standardise the slope by the Kalman posterior `sqrt(P[1,1])`.** Rejected
  after measurement: the local-linear-trend model is misspecified for minute
  log prices (standardised innovations have sd 1.10 and lag-1 autocorrelation
  0.62, and no `Q/R` ratio in the family fixes both), so the posterior sd is a
  model artefact. Standardising by it gave a "z-score" whose spread ran 1.44
  (JPM) to 2.03 (TSLA), which is why no preset generalised.
* **Fit a better state-space model.** Deferred, not rejected: the null scale
  makes the thresholds honest without needing the model to be right, and the
  `null_var` seam is where a time-of-day volatility profile would enter.
* **RTS / Kalman smoother, or a centred rolling slope.** Rejected: both use
  the future of the window. Forbidden for a live 1-minute state.
* **Put the class in `features/`.** Rejected: it is a state machine with
  session-reset rules, not a stateless column transform.
