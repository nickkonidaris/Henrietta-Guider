import socket
import time

import pytest

from henrietta_guider.core.autoguider_server import AutoGuiderServer, ConnectionState


@pytest.mark.unit
class TestAutoGuiderServer:
    def _make_with_pair(self, pacing_s=0.0):
        a, b = socket.socketpair()
        client = AutoGuiderServer.from_connected_socket(a, pacing_interval_s=pacing_s)
        return client, b  # b is the test-side "TCS"

    def test_initial_state_when_seeded_is_connected(self):
        client, peer = self._make_with_pair()
        assert client.state is ConnectionState.CONNECTED
        peer.close()

    def test_send_guide_emits_correct_bytes(self):
        client, peer = self._make_with_pair()
        ok = client.send_guide(0.50, -0.05)
        assert ok is True
        assert peer.recv(6) == b"G1099\r"
        peer.close()

    def test_send_when_disconnected_returns_false(self):
        # Build a client, then force DISCONNECTED — exactly the state a
        # caller would see after a network drop. No timing-dependent
        # buffering games.
        client, peer = self._make_with_pair()
        client._force_state(ConnectionState.DISCONNECTED)
        assert client.send_guide(0.0, 0.0) is False
        assert client.commands_suppressed_disconnected == 1
        peer.close()

    def test_pacing_blocks_within_window(self):
        # Use a short pacing window with a generous proportional slack
        # (60 ms wait for a 50 ms window — 20 % slack — robust on a
        # loaded CI runner without slowing the test).
        client, peer = self._make_with_pair(pacing_s=0.05)
        assert client.send_guide(0.0, 0.0) is True
        peer.recv(6)
        # Immediately again: should be suppressed.
        assert client.send_guide(0.05, 0.0) is False
        assert client.commands_suppressed_pacing == 1
        time.sleep(0.06)
        # Outside the window: should send.
        assert client.send_guide(0.05, 0.0) is True
        peer.recv(6)
        peer.close()

    def test_clip_then_encode(self):
        # 3.0" exceeds the wire range; the client should clip to 2.50"
        # before encoding.
        client, peer = self._make_with_pair()
        ok = client.send_guide(3.0, -3.0)
        assert ok is True
        assert peer.recv(6) == b"G5051\r"  # +2.50" RA, -2.45" Dec
        peer.close()
