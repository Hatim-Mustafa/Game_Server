from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

METRICS = (
    ("input_latency_mean_ms", "input latency (ms)"),
    ("position_error_mean_px", "position error (px)"),
    ("correction_rate", "correction rate"),
    ("correction_mean_px", "correction mean (px)"),
    ("snapshot_jitter_ms", "snapshot jitter (ms)"),
    ("snapshot_rate_hz", "snapshot rate (Hz)"),
    ("stalls", "stall events"),
    ("snapshot_gaps", "lost snapshot gaps"),
    ("sent_bytes_per_s", "upload (B/s)"),
    ("recv_bytes_per_s", "download (B/s)"),
)


def latest_results_dir() -> str:
    base = os.path.join(REPO_ROOT, "results")
    if not os.path.isdir(base):
        raise SystemExit("no results directory; run `python -m experiments.run` first")
    candidates = [
        os.path.join(base, name)
        for name in os.listdir(base)
        if name.startswith("exp_") and os.path.isdir(os.path.join(base, name))
    ]
    if not candidates:
        raise SystemExit("no experiment directories under results/")
    return max(candidates, key=os.path.getmtime)


def load_summaries(root: str) -> list[dict]:
    items: list[dict] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".summary.json"):
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as handle:
                data = json.load(handle)
            relative = os.path.relpath(dirpath, root).split(os.sep)
            if len(relative) < 3:
                continue
            data["_condition"] = relative[0]
            data["_mode"] = relative[1]
            data["_run_dir"] = os.path.join(dirpath, name)
            items.append(data)
    return items


def group_summaries(items: list[dict]) -> dict[tuple[str, str], dict[str, tuple[float, float, int]]]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        buckets[(item["_condition"], item["_mode"])].append(item)

    grouped: dict[tuple[str, str], dict[str, tuple[float, float, int]]] = {}
    for key, rows in buckets.items():
        grouped[key] = {}
        for metric, _label in METRICS:
            values = [float(row[metric]) for row in rows if metric in row]
            if not values:
                continue
            mean = statistics.fmean(values)
            std = statistics.stdev(values) if len(values) > 1 else 0.0
            grouped[key][metric] = (mean, std, len(values))
    return grouped


def write_csv(path: str, grouped: dict) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["condition", "mode", "metric", "n", "mean", "std"])
        for (condition, mode) in sorted(grouped):
            for metric, _label in METRICS:
                entry = grouped[(condition, mode)].get(metric)
                if entry is None:
                    continue
                mean, std, n = entry
                writer.writerow(
                    [condition, mode, metric, n, f"{mean:.4f}", f"{std:.4f}"]
                )


def write_markdown(path: str, grouped: dict) -> None:
    conditions = sorted({condition for condition, _ in grouped})
    modes = sorted({mode for _, mode in grouped})
    lines = ["# Experiment summary", ""]
    for metric, label in METRICS:
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| condition | " + " | ".join(modes) + " |")
        lines.append("|---|" + "|".join(["---"] * len(modes)) + "|")
        for condition in conditions:
            cells = []
            for mode in modes:
                entry = grouped.get((condition, mode), {}).get(metric)
                cells.append(f"{entry[0]:.2f} ± {entry[1]:.2f}" if entry else "-")
            lines.append(f"| {condition} | " + " | ".join(cells) + " |")
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def write_plots(out_dir: str, grouped: dict) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    os.makedirs(out_dir, exist_ok=True)
    conditions = sorted({condition for condition, _ in grouped})
    modes = sorted({mode for _, mode in grouped})
    width = 0.8 / max(1, len(modes))
    for metric, label in METRICS:
        fig, axis = plt.subplots()
        for index, mode in enumerate(modes):
            means = []
            errors = []
            for condition in conditions:
                entry = grouped.get((condition, mode), {}).get(metric)
                means.append(entry[0] if entry else 0.0)
                errors.append(entry[1] if entry else 0.0)
            positions = [i + index * width for i in range(len(conditions))]
            axis.bar(positions, means, width, yerr=errors, label=mode, capsize=3)
        axis.set_xticks(range(len(conditions)))
        axis.set_xticklabels(conditions)
        axis.set_ylabel(label)
        axis.set_title(label)
        axis.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"{metric}.png"))
        plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="experiments.aggregate")
    parser.add_argument("--results-dir", default=None)
    args = parser.parse_args(argv)

    results_dir = args.results_dir or latest_results_dir()
    items = load_summaries(results_dir)
    if not items:
        raise SystemExit(f"no *.summary.json found under {results_dir}")

    grouped = group_summaries(items)
    write_csv(os.path.join(results_dir, "summary.csv"), grouped)
    write_markdown(os.path.join(results_dir, "summary.md"), grouped)
    plotted = write_plots(os.path.join(results_dir, "plots"), grouped)

    print(f"summarized {len(items)} client runs from {results_dir}")
    print(f"  {os.path.join(results_dir, 'summary.csv')}")
    print(f"  {os.path.join(results_dir, 'summary.md')}")
    if plotted:
        print(f"  {os.path.join(results_dir, 'plots')}/")
    else:
        print("  (install matplotlib for plots)")


if __name__ == "__main__":
    main()
