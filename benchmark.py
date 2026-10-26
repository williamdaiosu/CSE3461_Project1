"""Run paired real-UDP loopback experiments and export raw + mean metrics."""
import argparse
import csv
import platform
import statistics
import time
from pathlib import Path

from reliable_udp import Endpoint, probability


def run_trial(*, messages=100, loss=0.1, reliable=True, seed=1,
              timeout=0.3, retries=5, ack_loss=0.0, interval=0.001):
    completions = []
    # Receiver drops DATA; sender optionally drops ACKs. Control/measurement
    # counters are read locally and are not subject to simulated packet loss.
    with Endpoint(data_loss=loss, seed=seed) as receiver, \
            Endpoint(ack_loss=ack_loss, seed=seed) as sender:
        start = time.perf_counter()
        for i in range(messages):
            result = sender.send(receiver.address, f'message-{i:04d}',
                                 reliable=reliable, timeout=timeout, retries=retries)
            if result.acknowledged:
                completions.append(result.ack_completion_ms)
            time.sleep(interval)  # Same application pacing in both modes.
        # Fixed observation grace lets baseline datagrams reach receive thread.
        # Included in elapsed time for both modes; not a throughput capacity test.
        time.sleep(0.05)
        elapsed = time.perf_counter() - start
        tx, rx = sender.snapshot(), receiver.snapshot()
    delivered = rx.get('unique_delivered', 0)
    # return formatted CSV output.
    return dict(mode='reliable' if reliable else 'baseline', loss=loss,
                ack_loss=ack_loss, seed=seed, messages=messages,
                timeout_ms=timeout * 1000, retries=retries,
                interval_ms=interval * 1000, elapsed_s=elapsed,
                delivered=delivered, delivery_pct=100 * delivered / messages,
                acknowledged=tx.get('messages_acknowledged', 0),
                unconfirmed=tx.get('messages_unconfirmed', 0),
                data_attempts=tx.get('data_attempts', 0),
                retransmissions=tx.get('retransmissions', 0),
                data_drops=rx.get('data_simulated_drops', 0),
                ack_drops=tx.get('ack_simulated_drops', 0),
                duplicates=rx.get('duplicates_suppressed', 0),
                mean_ack_ms=statistics.mean(completions) if completions else None,
                goodput_msg_s=delivered / elapsed,
                payload_bytes_s=rx.get('payload_bytes_delivered', 0) / elapsed)


def main():
    # parse arguments for the benchmark.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--messages', type=int, default=100)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--loss', type=probability, nargs='+', default=[0, .1, .3])
    parser.add_argument('--ack-loss', type=probability, default=0)
    parser.add_argument('--timeout', type=float, default=.3, help='seconds')
    parser.add_argument('--retries', type=int, default=5, help='extra attempts')
    parser.add_argument('--interval', type=float, default=.001, help='seconds')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if args.messages < 1 or args.runs < 1 or args.interval < 0:
        parser.error('messages/runs must be positive; interval must be nonnegative')
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    print('mode       loss  seed delivered retransmits mean ACK ms goodput msg/s', flush=True)
    for loss in args.loss:
        for seed in range(1, args.runs + 1):
            for reliable in (False, True):
                row = run_trial(messages=args.messages, loss=loss, reliable=reliable,
                                seed=seed, timeout=args.timeout, retries=args.retries,
                                ack_loss=args.ack_loss, interval=args.interval)
                rows.append(row)
                ack = '-' if row['mean_ack_ms'] is None else f"{row['mean_ack_ms']:.2f}"
                print(f"{row['mode']:10} {loss:4.0%} {seed:4} "
                      f"{row['delivered']:4}/{args.messages} {row['retransmissions']:11} "
                      f"{ack:>11} {row['goodput_msg_s']:13.2f}", flush=True)
    # writing output csv files for raw and summary metrics.
    with (args.output / 'trials.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for loss in args.loss:
        for mode in ('baseline', 'reliable'):
            group = [r for r in rows if r['loss'] == loss and r['mode'] == mode]
            out = dict(mode=mode, loss=loss, runs=len(group))
            for metric in ('delivery_pct', 'retransmissions', 'mean_ack_ms', 'goodput_msg_s'):
                values = [r[metric] for r in group if r[metric] is not None]
                out[metric] = statistics.mean(values) if values else None
            summary.append(out)
    with (args.output / 'summary.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    (args.output / 'environment.txt').write_text(
        f'Python: {platform.python_version()}\nPlatform: {platform.platform()}\n'
        f'Parameters: {vars(args)}\nNetwork: real UDP on IPv4 loopback; synthetic loss\n'
        'Each run uses a fresh session; seeds 1..runs. Fixed 50ms drain included.\n')
    print(f'Results saved in {args.output.resolve()}')


if __name__ == '__main__':
    main()
