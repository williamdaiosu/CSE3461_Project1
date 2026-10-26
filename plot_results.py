import argparse
import csv
from pathlib import Path
import matplotlib.pyplot as plt

def plot(path: Path = Path(__file__).resolve().parent):
    if not path.exists():
        print(f"Results directory '{path}' does not exist.")
        return
    # Check if the summary CSV file exists in the specified path
    summary_file = path / 'summary.csv'
    if not summary_file.exists():
        print(f"Summary file '{summary_file}' does not exist.")
        return

    with summary_file.open('r', newline='') as f:
        rows = list(csv.DictReader(f))
    print(rows[0])

    # format the data for plotting
    data = {"baseline": [], "reliable": []}
    for row in rows:
        mode = row['mode']
        loss = float(row['loss'])
        delivery_pct = float(row['delivery_pct'])
        data[mode].append((loss, delivery_pct))

    # sort the data by loss rate for plotting
    baseline_pairs = sorted(data["baseline"])
    reliable_pairs = sorted(data["reliable"])

    loss_rates = [pair[0] for pair in baseline_pairs]
    baseline = [pair[1] for pair in baseline_pairs]
    reliable = [pair[1] for pair in reliable_pairs]

    # plot the data
    x = list(range(len(loss_rates)))
    width = 0.35
    left_positions = [position - width / 2 for position in x]
    right_positions = [position + width / 2 for position in x]
    fig, ax = plt.subplots(figsize=(8, 5))
    bars1 = ax.bar(
        left_positions,
        baseline,
        width,
        label="Baseline UDP",
        color='b'
    )

    bars2 = ax.bar(
        right_positions,
        reliable,
        width,
        label="Reliable UDP",
        color='r'
    )

    # add labels, title, and legend
    ax.set_xticks(x)
    ax.set_xticklabels([f"{loss:.0%}" for loss in loss_rates])
    ax.set_xlabel("Loss Rate")
    ax.set_ylabel("Delivery Percentage(%)")
    ax.set_ylim(0, 110)
    ax.set_yticks(range(0, 101, 10))
    ax.bar_label(bars1, fmt="%.2f%%", padding=3)
    ax.bar_label(bars2, fmt="%.2f%%", padding=3)

    ax.set_title("Delivery Percentage vs Loss Rate")
    ax.legend(loc="lower right",
              ncol=2,
              fancybox=True,
              shadow=True)
    fig.tight_layout()
    plt.savefig(path / 'plot.png')
    plt.show()
    plt.close(fig)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Plot results from benchmark CSV files.")
    parser.add_argument('--results-dir', type=Path, default=Path(__file__).resolve().parent, help='Directory containing benchmark CSV files.')
    args = parser.parse_args()
    plot(args.results_dir)