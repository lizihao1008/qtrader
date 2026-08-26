"""Signal-quality diagnostics.

An equity curve tells you what happened; it does not tell you whether the signal
had any information. Rank IC does: at each timestamp, how well did the score
order the stocks compared with how they actually went on to perform?

The target is the **cross-sectionally demeaned** forward return, because a
dollar-neutral book earns the spread between its names, not the market's move.
The forward window is a label — it deliberately looks ahead — and is used only
for evaluation, never as an input to a strategy.

Reading the numbers: for a cross-sectional intraday signal, a mean rank IC of a
few thousandths is ordinary and a few hundredths is strong. What matters more
than the level is the t-statistic and whether the sign is stable across time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date

#: A cross-section smaller than this cannot support a meaningful correlation.
MIN_NAMES = 5


def forward_return(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Log return over the next ``horizon`` bars, never crossing a session end.

    Windows that would run past the close are ``NaN``: the return simply does
    not exist for a strategy that must be flat overnight.
    """
    from ..features.relative import bar_log_returns

    returns = bar_log_returns(close, within_session=True)
    ahead = returns.rolling(horizon, min_periods=horizon).sum().shift(-horizon)

    day = session_date(close.index).to_numpy()
    same_session = pd.Series(day, index=close.index).shift(-horizon).to_numpy() == day
    ahead.loc[~same_session] = np.nan
    return ahead


def rank_ic(
    scores: pd.DataFrame,
    forward: pd.DataFrame,
    eligible: pd.DataFrame,
    *,
    min_names: int = MIN_NAMES,
) -> pd.Series:
    """Spearman correlation between score and forward return, per timestamp."""
    valid = eligible & scores.notna() & forward.notna()
    ranked_score = scores.where(valid).rank(axis=1)
    ranked_forward = forward.where(valid).rank(axis=1)

    score_dev = ranked_score.sub(ranked_score.mean(axis=1), axis=0)
    forward_dev = ranked_forward.sub(ranked_forward.mean(axis=1), axis=0)

    covariance = (score_dev * forward_dev).sum(axis=1)
    scale = np.sqrt((score_dev**2).sum(axis=1) * (forward_dev**2).sum(axis=1))

    ic = covariance / scale.where(scale > 0)
    return ic.where(valid.sum(axis=1) >= min_names).rename("rank_ic")


def rank_ic_summary(
    scores: pd.DataFrame,
    close: pd.DataFrame,
    eligible: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (5, 15, 30),
) -> dict[str, dict]:
    """Rank IC statistics at several forward horizons.

    ``t_stat`` is the plain ``mean / stderr`` of the per-bar IC series. It
    overstates significance because consecutive overlapping windows are
    correlated — treat it as a screening number, not a p-value.
    """
    summary = {}
    for horizon in horizons:
        series = rank_ic(scores, forward_return(close, horizon), eligible).dropna()
        if series.empty:
            summary[f"{horizon}b"] = {"n_obs": 0}
            continue
        mean = float(series.mean())
        std = float(series.std(ddof=1))
        summary[f"{horizon}b"] = {
            "n_obs": int(len(series)),
            "mean_ic": mean,
            "std_ic": std,
            "ic_ir": mean / std if std > 0 else float("nan"),
            "t_stat": mean / std * np.sqrt(len(series)) if std > 0 else float("nan"),
            "share_positive": float((series > 0).mean()),
        }
    return summary
