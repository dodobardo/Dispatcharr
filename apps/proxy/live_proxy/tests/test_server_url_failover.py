"""Tests for live playback failover across an M3U account's backup server URLs."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.proxy.live_proxy.constants import ChannelMetadataField
from apps.proxy.live_proxy.input.manager import StreamManager

PRIMARY = "http://primary:8080/live/u/p/7.ts"
BACKUP1 = "http://backup1/live/u/p/7.ts"
BACKUP2 = "http://backup2/live/u/p/7.ts"
CANDIDATES = [
    ("http://primary:8080", PRIMARY),
    ("http://backup1", BACKUP1),
    ("http://backup2", BACKUP2),
]


def _make_manager(**overrides):
    sm = StreamManager.__new__(StreamManager)
    sm.channel_id = "test-channel"
    sm.url = PRIMARY
    sm.current_stream_id = 7
    sm.tried_stream_ids = {7}
    sm.tried_server_urls = set()
    sm._pending_working_server = None
    sm._failover_rotation_passes = 0
    sm._rotation_cooldown_until = None
    sm._had_successful_connection = True
    sm.buffer = MagicMock()
    sm.buffer.redis_client.hget.return_value = b"3"

    def fake_update_url(new_url, *args, **kwargs):
        sm.url = new_url
        return True

    sm.update_url = MagicMock(side_effect=fake_update_url)
    for key, value in overrides.items():
        setattr(sm, key, value)
    return sm


@patch(
    "apps.proxy.live_proxy.input.manager.get_server_failover_candidates",
    return_value=(1, "http://primary:8080", CANDIDATES),
)
class ServerUrlFailoverTests(SimpleTestCase):
    def test_switches_same_stream_to_next_server(self, mock_candidates):
        sm = _make_manager()

        self.assertTrue(sm._try_next_server_url(reason="buffering_timeout"))

        mock_candidates.assert_called_once_with(7, 3)
        sm.update_url.assert_called_once_with(
            BACKUP1, 7, 3, reason="buffering_timeout", server_url="http://backup1",
        )
        self.assertEqual(sm.current_stream_id, 7)
        self.assertEqual(sm._pending_working_server, (1, "http://primary:8080", "http://backup1"))
        mapping = sm.buffer.redis_client.hset.call_args.kwargs["mapping"]
        self.assertEqual(mapping[ChannelMetadataField.URL], BACKUP1)

    def test_tries_every_server_once_then_gives_up(self, _mock):
        sm = _make_manager()

        self.assertTrue(sm._try_next_server_url())
        self.assertEqual(sm.url, BACKUP1)
        self.assertTrue(sm._try_next_server_url())
        self.assertEqual(sm.url, BACKUP2)
        self.assertFalse(sm._try_next_server_url())
        self.assertEqual(sm.tried_server_urls, {PRIMARY, BACKUP1, BACKUP2})

    def test_stable_connection_allows_servers_again(self, _mock):
        sm = _make_manager(tried_server_urls={PRIMARY, BACKUP1, BACKUP2}, url=BACKUP2)
        sm._note_stable_connection()

        self.assertTrue(sm._try_next_server_url())
        self.assertEqual(sm.url, PRIMARY)

    @patch("apps.proxy.live_proxy.input.manager.get_alternate_streams")
    def test_next_stream_waits_until_servers_are_exhausted(self, mock_alts, _mock):
        sm = _make_manager()
        mock_alts.return_value = []

        self.assertTrue(sm._try_next_stream(reason="max_retries_exceeded"))
        self.assertTrue(sm._try_next_stream(reason="max_retries_exceeded"))
        mock_alts.assert_not_called()

        # All three servers tried: fall through to the channel's other streams
        self.assertFalse(sm._try_next_stream(reason="max_retries_exceeded"))
        mock_alts.assert_called_once()

    def test_working_server_remembered_once(self, _mock):
        sm = _make_manager()
        sm._try_next_server_url()
        with patch(
            "apps.proxy.live_proxy.input.manager.remember_working_server_url"
        ) as remember:
            sm._remember_working_server()
            sm._remember_working_server()
        remember.assert_called_once_with(1, "http://primary:8080", "http://backup1")


class ServerUrlFailoverNoBackupsTests(SimpleTestCase):
    @patch(
        "apps.proxy.live_proxy.input.manager.get_server_failover_candidates",
        return_value=(1, "http://primary:8080", [("http://primary:8080", PRIMARY)]),
    )
    def test_single_server_does_nothing(self, _mock):
        sm = _make_manager()
        self.assertFalse(sm._try_next_server_url())
        sm.update_url.assert_not_called()

    def test_without_profile_metadata_does_nothing(self):
        sm = _make_manager()
        sm.buffer.redis_client.hget.return_value = None
        with patch(
            "apps.proxy.live_proxy.input.manager.get_server_failover_candidates"
        ) as lookup:
            self.assertFalse(sm._try_next_server_url())
        lookup.assert_not_called()

    def test_errors_do_not_break_stream_failover(self):
        sm = _make_manager()
        with patch(
            "apps.proxy.live_proxy.input.manager.get_server_failover_candidates",
            side_effect=RuntimeError("boom"),
        ), patch(
            "apps.proxy.live_proxy.input.manager.get_alternate_streams", return_value=[]
        ) as alts:
            self.assertFalse(sm._try_next_stream())
        alts.assert_called_once()
