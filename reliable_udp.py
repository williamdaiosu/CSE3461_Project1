"""F3: stop-and-wait reliability over IPv4 UDP; Python 3.10+, stdlib only.

One receive thread handles DATA and ACK packets; send() is serialized so only
one outgoing reliable message is outstanding. Both peers can send concurrently.
ACK means accepted into the receiver's in-memory queue, not read by a human.
"""
from __future__ import annotations

import hashlib
import json
import math
import queue
import socket
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass

MAX_DATAGRAM = 1200  # Keep the whole JSON datagram below typical path MTUs.


def probability(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('loss probability must be between 0 and 1')
    return value


def encode(packet: dict) -> bytes:
    data = json.dumps(packet, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(data) > MAX_DATAGRAM:
        raise ValueError(f'encoded packet exceeds {MAX_DATAGRAM} bytes; shorten the message')
    return data


def decode(data: bytes) -> dict | None:
    """Reject malformed packets before they can affect protocol state."""
    if len(data) > MAX_DATAGRAM:
        return None
    try:
        p = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(p, dict) or p.get('v') != 1:
        return None
    if p.get('type') not in ('DATA', 'ACK'):
        return None
    if not isinstance(p.get('session'), str) or len(p['session']) != 32:
        return None
    if any(type(p.get(k)) is not int or p[k] < 0 for k in ('seq', 'attempt')):
        return None
    if p['type'] == 'DATA':
        if not isinstance(p.get('text'), str) or type(p.get('reliable')) is not bool:
            return None
    return p


@dataclass(frozen=True)
class SendResult:
    sequence: int
    attempts: int
    acknowledged: bool
    ack_completion_ms: float | None


class Endpoint:
    """Reusable chat transport. Read delivered (address, packet) via inbox.

    Loss is injected AFTER recvfrom, BEFORE acceptance/ACK processing. A stable
    hash of seed, packet type, sequence and attempt makes first-attempt DATA loss
    comparable between baseline and reliable runs. This is a controlled model,
    not a measurement of physical Wi-Fi packet loss. Session is excluded from
    the hash intentionally; separate clients may have correlated loss patterns.
    drop_hook, when supplied, overrides seeded loss for deterministic tests.
    """

    def __init__(self, bind=('127.0.0.1', 0), *, data_loss=0.0,
                 ack_loss=0.0, seed=1, drop_hook=None):
        self.data_loss = probability(data_loss)
        self.ack_loss = probability(ack_loss)
        self.seed = seed
        self.drop_hook = drop_hook
        self.session = uuid.uuid4().hex
        self.inbox = queue.Queue()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(bind)
        self._socket.settimeout(0.05)
        self.address = self._socket.getsockname()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending = {}  # (peer, local session, seq) -> Event
        self._seen = set()  # (source address, source session, seq)
        self._counts = Counter()
        self._next_seq = 0
        self._thread = threading.Thread(target=self._receive, daemon=True)
        self._thread.start()

    def _count(self, key, amount=1):
        with self._lock:
            self._counts[key] += amount

    def snapshot(self):
        with self._lock:
            return dict(self._counts)

    def _drop(self, packet):
        if self.drop_hook is not None:
            return bool(self.drop_hook(packet))
        rate = self.data_loss if packet['type'] == 'DATA' else self.ack_loss
        key = f"{self.seed}:{packet['type']}:{packet['seq']}:{packet['attempt']}"
        draw = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big') / 2**64
        return draw < rate

    def _receive(self):
        while not self._stop.is_set():
            try:
                data, peer = self._socket.recvfrom(MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 10040:  # Handle Windows WSAEMSGSIZE for oversized datagrams.
                    self._count('malformed_packets')
                    continue
                break
            p = decode(data)
            if p is None:
                self._count('malformed_packets')
                continue
            kind = p['type']
            self._count(kind.lower() + '_received_on_socket')
            if self._drop(p):
                self._count(kind.lower() + '_simulated_drops')
                continue
            key = (peer, p['session'], p['seq'])
            if kind == 'ACK':
                with self._lock:
                    event = self._pending.get(key)
                    if event is not None:
                        event.set()
                continue
            # Deduplicate before delivering, but ACK duplicates too: the earlier
            # ACK may have been lost. State is kept until this endpoint closes.
            if key not in self._seen:
                self._seen.add(key)
                self.inbox.put((peer, p))
                self._count('unique_delivered')
                self._count('payload_bytes_delivered', len(p['text'].encode('utf-8')))
            else:
                self._count('duplicates_suppressed')
            if p['reliable']:
                ack = {k: p[k] for k in ('v', 'session', 'seq', 'attempt')}
                ack['type'] = 'ACK'
                try:
                    self._socket.sendto(encode(ack), peer)
                    self._count('acks_sent')
                except OSError:
                    self._count('socket_errors')

    def send(self, peer, text, *, reliable=True, timeout=0.3, retries=5):
        # validate arguments
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        if type(retries) is not int or retries < 0:
            raise ValueError('retries must be a nonnegative integer')
        if not isinstance(text, str) or type(reliable) is not bool:
            raise ValueError('text must be str and reliable must be bool')
        # convert peer to (host,port) tuple with numeric IP and port.
        peer = (socket.gethostbyname(peer[0]), int(peer[1]))
        # set an synchronization lock so only one reliable message is outstanding at a time.
        with self._send_lock:
            if self._stop.is_set():
                raise RuntimeError('endpoint is closed')
            seq = self._next_seq
            # create data packet with attempt=0; attempt is incremented in the loop below.
            p = dict(v=1, type='DATA', session=self.session, seq=seq,
                     attempt=0, reliable=reliable, text=text)
            # Validate before incrementing counters; also reserve space for the
            # largest retry number so oversize payloads cannot fail mid-send.
            # encode() with check on size.
            encode(dict(p, attempt=retries))
            self._next_seq += 1
            event = threading.Event()
            key = (peer, self.session, seq)
            with self._lock:
                self._pending[key] = event
            started = time.perf_counter()
            self._count('messages_submitted')
            try:
                for attempt in range(1 + (retries if reliable else 0)):
                    if self._stop.is_set():
                        raise RuntimeError('endpoint closed during send')
                    p['attempt'] = attempt
                    self._socket.sendto(encode(p), peer)
                    self._count('data_attempts')
                    if attempt:
                        self._count('retransmissions')
                    if not reliable:
                        return SendResult(seq, 1, False, None)
                    # wait for ACK or timeout; if ACK arrives, return success.
                    if event.wait(timeout):
                        if self._stop.is_set():
                            raise RuntimeError('endpoint closed during send')
                        self._count('messages_acknowledged')
                        return SendResult(seq, attempt + 1, True,
                                          (time.perf_counter() - started) * 1000)
                self._count('messages_unconfirmed')
                return SendResult(seq, retries + 1, False, None)
            finally:
                with self._lock:
                    self._pending.pop(key, None)

    def close(self):
        self._stop.set()
        with self._lock:
            for event in self._pending.values():
                event.set()
        self._thread.join(timeout=1)
        self._socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
