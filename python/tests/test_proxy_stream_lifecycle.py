"""Guard the pinned aioquic send-only lifecycle workaround without bypassing ACKs."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aioquic.quic.packet_builder import QuicDeliveryState
from aioquic.quic.stream import QuicStream
import proxy


class SendOnlyLifecycleTests(unittest.TestCase):
    def test_only_local_uni_receive_halves_finish_and_sender_still_needs_ack(self):
        for is_client in (True, False):
            with self.subTest(is_client=is_client):
                initiator = 0 if is_client else 1
                local_uni = initiator | 2
                remote_uni = (1 - initiator) | 2
                streams = {
                    local_uni: QuicStream(stream_id=local_uni, readable=False),
                    remote_uni: QuicStream(stream_id=remote_uni, writable=False),
                    initiator: QuicStream(stream_id=initiator),
                }
                quic = SimpleNamespace(_streams=streams, _datagrams_pending=[], _is_client=is_client)
                outgoing = streams[local_uni]
                outgoing.sender.write(b"payload", end_stream=True)
                self.assertFalse(proxy.transport_over_budget(quic))
                self.assertTrue(outgoing.receiver.is_finished)
                self.assertFalse(outgoing.is_finished)
                self.assertEqual(bytes(outgoing.sender._buffer), b"payload")
                self.assertFalse(streams[remote_uni].receiver.is_finished)
                self.assertFalse(streams[initiator].receiver.is_finished)
                frame = outgoing.sender.get_frame(1200)
                self.assertTrue(frame.fin)
                outgoing.sender.on_data_delivery(QuicDeliveryState.ACKED, 0, 7, True)
                self.assertTrue(outgoing.is_finished)

    def test_unacknowledged_send_only_data_still_counts_against_budget(self):
        stream = QuicStream(stream_id=2, readable=False)
        stream.sender.write(b"x" * (proxy.MAX_QUIC_BUFFER_BYTES + 1), end_stream=True)
        quic = SimpleNamespace(_streams={2: stream}, _datagrams_pending=[], _is_client=True)
        self.assertTrue(proxy.transport_over_budget(quic))
        self.assertFalse(stream.is_finished)


if __name__ == "__main__":
    unittest.main()
