#!/usr/bin/env python3
"""Build a concise boss summary from the frozen preliminary delivery only."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec


ROOT = Path("/Users/Hoshino/Documents/nautilus/outputs/deliverables")
SOURCE = ROOT / "direct_maker_24h_preliminary"
OUTPUT = ROOT / "direct_maker_24h_summary"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "").strip()
    return None if value == "" else float(value)


def integer(row: dict[str, str], key: str) -> int | None:
    value = row.get(key, "").strip()
    return None if value == "" else int(float(value))


def fmt_pct(value: float | None, *, ratio: bool = False) -> str:
    if value is None:
        return "—"
    if ratio:
        value *= 100.0
    return f"{value:.2f}%"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def performance(path: Path) -> tuple[list[datetime], list[float], list[float]]:
    rows = read_rows(path)
    return (
        [parse_time(row["timestamp_utc"]) for row in rows],
        [float(row["Return"]) * 100.0 for row in rows],
        [float(row["drawdown"]) * 100.0 for row in rows],
    )


def text_box(ax, x: float, y: float, text: str, *, size: float, color: str = "#172033",
             weight: str = "normal", ha: str = "left") -> None:
    ax.text(x, y, text, transform=ax.transAxes, fontsize=size, color=color,
            fontweight=weight, ha=ha, va="center")


def main() -> None:
    summary_path = SOURCE / "01_summary/preliminary_direct_vs_maker.csv"
    integrity_path = SOURCE / "01_summary/data_integrity_summary.csv"
    sensitivity_path = SOURCE / "01_summary/gap_sensitivity_summary.csv"
    direct_path = SOURCE / "02_direct/preliminary_performance.csv"
    maker_path = SOURCE / "03_maker/preliminary_performance.csv"
    for path in (summary_path, integrity_path, sensitivity_path, direct_path, maker_path):
        if not path.exists():
            raise FileNotFoundError(path)

    rows = read_rows(summary_path)
    by_mode = {row["execution_mode"]: row for row in rows}
    direct = by_mode["FIRST_TICK_SHADOW"]
    maker = by_mode["L1_BBO_PAPER_MAKER"]
    integrity = read_rows(integrity_path)[0]
    sensitivity = read_rows(sensitivity_path)[0]

    direct_return = number(direct, "PRELIMINARY_Return") * 100.0
    maker_return = number(maker, "PRELIMINARY_Return") * 100.0
    direct_maxdd = number(direct, "PRELIMINARY_MaxDD") * 100.0
    maker_maxdd = number(maker, "PRELIMINARY_MaxDD") * 100.0
    direct_avg_turnover = number(direct, "PRELIMINARY_Avg_Daily_Turnover_pct")
    maker_avg_turnover = number(maker, "PRELIMINARY_Avg_Daily_Turnover_pct")
    direct_total_turnover = number(direct, "PRELIMINARY_Total_Turnover_raw")
    maker_total_turnover = number(maker, "PRELIMINARY_Total_Turnover_raw")
    missing_time = parse_time(integrity["missing_minute_utc"])

    # Write a deliberately small boss-facing table. Values remain numeric.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fields = [
        "mode", "return_pct", "max_drawdown_pct", "avg_daily_turnover_pct",
        "total_turnover_raw", "fill_count", "order_count", "full_fill_orders",
        "partial_fill_orders", "zero_fill_orders", "quantity_fill_ratio",
        "order_fill_rate", "median_first_fill_latency_ms",
        "p95_first_fill_latency_ms", "preliminary_flag",
    ]
    output_rows = [
        {
            "mode": "DIRECT",
            "return_pct": direct_return,
            "max_drawdown_pct": direct_maxdd,
            "avg_daily_turnover_pct": direct_avg_turnover,
            "total_turnover_raw": direct_total_turnover,
            "fill_count": integer(direct, "fill_count"),
            "order_count": "",
            "full_fill_orders": "",
            "partial_fill_orders": "",
            "zero_fill_orders": "",
            "quantity_fill_ratio": "",
            "order_fill_rate": "",
            "median_first_fill_latency_ms": "",
            "p95_first_fill_latency_ms": "",
            "preliminary_flag": "PRELIMINARY_DIAGNOSTIC_ONLY",
        },
        {
            "mode": "MAKER",
            "return_pct": maker_return,
            "max_drawdown_pct": maker_maxdd,
            "avg_daily_turnover_pct": maker_avg_turnover,
            "total_turnover_raw": maker_total_turnover,
            "fill_count": integer(maker, "fill_count"),
            "order_count": integer(maker, "order_count"),
            "full_fill_orders": integer(maker, "full_fill_orders"),
            "partial_fill_orders": integer(maker, "partial_fill_orders"),
            "zero_fill_orders": integer(maker, "zero_fill_orders"),
            "quantity_fill_ratio": number(maker, "quantity_fill_ratio"),
            "order_fill_rate": number(maker, "order_fill_rate"),
            "median_first_fill_latency_ms": number(maker, "median_first_fill_latency_ms"),
            "p95_first_fill_latency_ms": number(maker, "P95_first_fill_latency_ms"),
            "preliminary_flag": "PRELIMINARY_DIAGNOSTIC_ONLY",
        },
    ]
    with (OUTPUT / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    note = [
        "This package is for boss review only.",
        f"The 24h run failed the strict integrity gate: {integrity['observed_bars']}/{integrity['expected_bars']} minutes.",
        "DIRECT and MAKER both lost money in this window.",
        "Maker did not improve performance.",
        "Sensitivity check shows the missing minute did not change decisions or final PnL.",
        "Final conclusion still awaits a clean rerun.",
    ]
    (OUTPUT / "note.txt").write_text("\n".join(note) + "\n", encoding="utf-8")

    direct_t, direct_ret, direct_dd = performance(direct_path)
    maker_t, maker_ret, maker_dd = performance(maker_path)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#C9D0DA",
        "axes.labelcolor": "#344054",
        "xtick.color": "#667085",
        "ytick.color": "#667085",
    })
    fig = plt.figure(figsize=(16, 10.5), facecolor="#F5F7FA")
    grid = GridSpec(18, 24, figure=fig, left=0.045, right=0.975, top=0.90,
                    bottom=0.075, wspace=1.35, hspace=1.75)
    fig.text(0.05, 0.955, "24H DIRECT vs MAKER Summary", fontsize=24,
             fontweight="bold", color="#101828", va="top")
    fig.text(0.05, 0.918,
             f"Preliminary only · {integrity['observed_bars']}/{integrity['expected_bars']} minutes captured · not final clean forward result",
             fontsize=11.5, color="#667085", va="top")

    # Core conclusion block.
    core = fig.add_subplot(grid[0:4, 0:15])
    core.set_facecolor("white")
    core.set_xticks([]); core.set_yticks([])
    for spine in core.spines.values():
        spine.set_visible(False)
    core.patch.set_edgecolor("#D0D5DD"); core.patch.set_linewidth(1.0)
    text_box(core, 0.035, 0.84, "Core result", size=12, weight="bold")
    text_box(core, 0.035, 0.52, f"DIRECT: {direct_return:.2f}%", size=23, color="#B42318", weight="bold")
    text_box(core, 0.39, 0.52, f"MAKER: {maker_return:.2f}%", size=23, color="#B42318", weight="bold")
    text_box(core, 0.035, 0.21, "Result: both lost money in this 24h window", size=12.5, weight="bold")
    text_box(core, 0.60, 0.21, "Maker did not improve performance", size=12.5, color="#B42318", weight="bold")

    # Four-metric comparison.
    metrics = fig.add_subplot(grid[4:9, 0:15])
    metrics.axis("off")
    metrics.set_title("Core metrics", loc="left", fontsize=12, fontweight="bold", color="#172033", pad=7)
    labels = ["Return (%)", "Max Drawdown (%)", "Avg Daily Turnover (%)", "Total Turnover (raw)"]
    cell_text = [
        ["DIRECT", f"{direct_return:.2f}%", f"{direct_maxdd:.2f}%", f"{direct_avg_turnover:.2f}%", f"{direct_total_turnover:.2f}"],
        ["MAKER", f"{maker_return:.2f}%", f"{maker_maxdd:.2f}%", f"{maker_avg_turnover:.2f}%", f"{maker_total_turnover:.2f}"],
    ]
    table = metrics.table(cellText=cell_text, colLabels=["Mode"] + labels,
                          cellLoc="center", colLoc="center", bbox=[0, 0.10, 1, 0.78])
    table.auto_set_font_size(False); table.set_fontsize(10.5)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#E4E7EC")
        if row == 0:
            cell.set_facecolor("#344054"); cell.get_text().set_color("white"); cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor("white")
            if col == 0: cell.get_text().set_weight("bold")
            if col in (1, 2): cell.get_text().set_color("#B42318")

    # Maker execution panel.
    execution = fig.add_subplot(grid[0:9, 16:24])
    execution.set_facecolor("white")
    execution.set_xticks([]); execution.set_yticks([])
    for spine in execution.spines.values(): spine.set_visible(False)
    execution.patch.set_edgecolor("#D0D5DD"); execution.patch.set_linewidth(1.0)
    text_box(execution, 0.07, 0.93, "Maker execution", size=13, weight="bold")
    maker_items = [
        ("Fill ratio", fmt_pct(number(maker, "quantity_fill_ratio"), ratio=True)),
        ("Order fill rate", fmt_pct(number(maker, "order_fill_rate"), ratio=True)),
        ("Full fill orders", str(integer(maker, "full_fill_orders"))),
        ("Partial fill orders", str(integer(maker, "partial_fill_orders"))),
        ("Zero fill orders", str(integer(maker, "zero_fill_orders"))),
        ("Median first-fill latency", f"{number(maker, 'median_first_fill_latency_ms'):.0f} ms"),
        ("P95 first-fill latency", f"{number(maker, 'P95_first_fill_latency_ms'):.1f} ms"),
    ]
    for idx, (label, value) in enumerate(maker_items):
        y = 0.82 - idx * 0.105
        text_box(execution, 0.07, y, label, size=10.5, color="#667085")
        text_box(execution, 0.93, y, value, size=12, weight="bold", ha="right")
        if idx < len(maker_items) - 1:
            execution.plot([0.07, 0.93], [y - 0.055, y - 0.055], transform=execution.transAxes,
                           color="#EAECF0", linewidth=0.8)

    direct_color, maker_color = "#175CD3", "#7A5AF8"
    returns = fig.add_subplot(grid[10:14, 0:24])
    returns.plot(direct_t, direct_ret, color=direct_color, linewidth=1.8, label="DIRECT")
    returns.plot(maker_t, maker_ret, color=maker_color, linewidth=1.8, label="MAKER")
    returns.axhline(0, color="#98A2B3", linewidth=0.8)
    returns.axvline(missing_time, color="#D92D20", linestyle="--", linewidth=1.3)
    returns.annotate("missing minute", xy=(missing_time, max(max(direct_ret), max(maker_ret))),
                     xytext=(5, -4), textcoords="offset points", fontsize=8.5,
                     color="#D92D20", ha="left", va="top")
    returns.set_title("Cumulative return", loc="left", fontsize=12, fontweight="bold")
    returns.set_ylabel("Return (%)")
    returns.legend(frameon=False, ncol=2, loc="lower left")
    returns.grid(axis="y", color="#EAECF0", linewidth=0.8)

    drawdown = fig.add_subplot(grid[14:18, 0:24], sharex=returns)
    drawdown.plot(direct_t, direct_dd, color=direct_color, linewidth=1.7, label="DIRECT")
    drawdown.plot(maker_t, maker_dd, color=maker_color, linewidth=1.7, label="MAKER")
    drawdown.axvline(missing_time, color="#D92D20", linestyle="--", linewidth=1.3)
    drawdown.set_title("Drawdown", loc="left", fontsize=12, fontweight="bold")
    drawdown.set_ylabel("Drawdown (%)")
    drawdown.grid(axis="y", color="#EAECF0", linewidth=0.8)
    drawdown.xaxis.set_major_locator(mdates.HourLocator(interval=4))
    drawdown.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    # Tick labels already make the 24h order clear; omit a separate x-axis
    # label to leave clean space for the mandatory bottom conclusion.

    conclusion = (
        "This is a preliminary diagnostic result, not a clean validated 24h forward run.  "
        "Sensitivity replay indicates the single missing minute did not change decisions or PnL."
    )
    fig.text(0.05, 0.025, conclusion, fontsize=10.5, color="#475467", va="bottom")
    fig.savefig(OUTPUT / "summary.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)

    # Fail if the source evidence does not match the required wording.
    if sensitivity.get("classification") != "NUMERICALLY_INSENSITIVE_TO_SINGLE_GAP":
        raise RuntimeError("gap sensitivity is not the expected passed diagnostic classification")
    if not (direct_return < 0 and maker_return < 0 and maker_return < direct_return):
        raise RuntimeError("boss conclusion does not reconcile to source returns")


if __name__ == "__main__":
    main()
