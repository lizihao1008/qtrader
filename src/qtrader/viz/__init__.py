"""Plotly figures and the HTML backtest report."""

from .charts import equity_chart, exposure_chart, price_chart
from .regime import plot_delay_far, plot_regime
from .report import write_report

__all__ = [
    "price_chart",
    "equity_chart",
    "exposure_chart",
    "write_report",
    "plot_regime",
    "plot_delay_far",
]
