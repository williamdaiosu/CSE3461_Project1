"""Two-way terminal chat using exactly the transport used by the benchmark."""
import argparse
import json
import queue
import threading

from reliable_udp import Endpoint, probability


def main():
    # parse arguments for the chat.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--peer', default='127.0.0.1')
    parser.add_argument('--peer-port', type=int, required=True)
    parser.add_argument('--loss', type=probability, default=.2,
                        help='DATA loss injected on this endpoint reception')
    parser.add_argument('--ack-loss', type=probability, default=0)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=.3)
    parser.add_argument('--retries', type=int, default=5)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    with Endpoint((args.bind, args.port), data_loss=args.loss,
                  ack_loss=args.ack_loss, seed=args.seed) as endpoint:
        stopped = threading.Event()

        def display():
            while not stopped.is_set():
                try:
                    address, packet = endpoint.inbox.get(timeout=.1)
                except queue.Empty:
                    continue
                print(f"\n{address[0]}:{address[1]} [{packet['seq']}]: {packet['text']}", flush=True)

        worker = threading.Thread(target=display, daemon=True)
        worker.start()
        print(f'Listening on {endpoint.address}; DATA loss={args.loss:.0%}, '
              f'ACK loss={args.ack_loss:.0%}; mode={"baseline" if args.baseline else "reliable"}')
        print('Type a message. /stats shows counters; /quit exits.')
        # Main loop for reading user input and commands. 
        try:
            while True:
                text = input('> ')
                # quit command to exit the chat.
                if text == '/quit':
                    break
                # stats command to display endpoint statistics.
                if text == '/stats':
                    print(json.dumps(endpoint.snapshot(), indent=2))
                    continue
                # send the message to the peer endpoint.
                try:
                    result = endpoint.send((args.peer, args.peer_port), text,
                                           reliable=not args.baseline,
                                           timeout=args.timeout, retries=args.retries)
                except (ValueError, OSError) as exc:
                    print(f'Send error: {exc}')
                    continue
                if args.baseline:
                    print('Sent once; delivery unknown (baseline has no ACK).')
                elif result.acknowledged:
                    print(f'ACK received; attempts={result.attempts}; '
                          f'completion={result.ack_completion_ms:.2f} ms')
                else:
                    print(f'Unconfirmed after {result.attempts} attempts; may have arrived.')
        except (EOFError, KeyboardInterrupt):
            print()
        finally:
            stopped.set()
            worker.join(timeout=.5)


if __name__ == '__main__':
    main()
