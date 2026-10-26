"""Tests for M3U account backup (failover) server URLs."""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.channels.models import Stream
from apps.m3u import tasks as m3u_tasks
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.serializers import M3UAccountSerializer
from apps.m3u.server_urls import (
    get_account_server_urls,
    make_stream_url_canonicalizer,
    order_server_urls,
    parse_server_url_list,
    replace_url_origin,
    url_origin,
)
from apps.proxy.live_proxy.url_utils import (
    _resolve_live_stream_url,
    get_live_stream_url_candidates,
)
from core.xtream_codes import Client as XCClient


class ServerUrlHelperTests(SimpleTestCase):
    def test_parse_accepts_lists_and_separated_text(self):
        self.assertEqual(
            parse_server_url_list(" http://a:1 |http://b:2\nhttp://a:1\n\n"),
            ["http://a:1", "http://b:2"],
        )
        self.assertEqual(parse_server_url_list(["http://a", "", None, "http://a"]), ["http://a"])
        self.assertEqual(parse_server_url_list(None), [])

    def test_origin_helpers(self):
        self.assertEqual(url_origin("HTTP://Host.TV:8080/get.php?x=1"), "http://host.tv:8080")
        self.assertIsNone(url_origin("not a url"))
        self.assertEqual(
            replace_url_origin("http://a:1/live/u/p/1.ts?t=2", "https://b:2"),
            "https://b:2/live/u/p/1.ts?t=2",
        )

    def test_account_server_urls_dedupes_primary(self):
        account = M3UAccount(
            server_url="http://a:1",
            backup_server_urls=["http://a:1", "http://b:2"],
        )
        self.assertEqual(get_account_server_urls(account), ["http://a:1", "http://b:2"])

    def test_preferred_server_moves_to_front(self):
        account = M3UAccount(id=7, server_url="http://a", backup_server_urls=["http://b", "http://c"])
        with patch("apps.m3u.server_urls.get_preferred_server_url", return_value="http://c"):
            self.assertEqual(order_server_urls(account), ["http://c", "http://a", "http://b"])
        with patch("apps.m3u.server_urls.get_preferred_server_url", return_value="http://gone"):
            self.assertEqual(order_server_urls(account), ["http://a", "http://b", "http://c"])

    def test_canonicalizer_maps_backup_hosts_to_primary(self):
        account = M3UAccount(
            server_url="http://primary:8080/get.php?username=u",
            backup_server_urls=["http://mirror:80/get.php?username=u"],
        )
        canonicalize = make_stream_url_canonicalizer(account)
        self.assertEqual(
            canonicalize("http://mirror:80/u/p/42"), "http://primary:8080/u/p/42"
        )
        self.assertEqual(canonicalize("http://cdn/other/1"), "http://cdn/other/1")
        self.assertIsNone(make_stream_url_canonicalizer(M3UAccount(server_url="http://x")))


class BackupServerUrlsSerializerTests(TestCase):
    def _serializer(self, value):
        return M3UAccountSerializer(
            data={
                "name": "Backup test",
                "server_url": "http://primary:8080",
                "account_type": "STD",
                "backup_server_urls": value,
            }
        )

    def test_list_is_cleaned_and_saved(self):
        serializer = self._serializer([" http://b1:80 ", "", "http://b1:80", "http://b2:80"])
        self.assertTrue(serializer.is_valid(), serializer.errors)
        account = serializer.save()
        account.refresh_from_db()
        self.assertEqual(account.backup_server_urls, ["http://b1:80", "http://b2:80"])
        self.assertEqual(
            M3UAccountSerializer(account).data["backup_server_urls"],
            ["http://b1:80", "http://b2:80"],
        )

    def test_multipart_text_is_split(self):
        serializer = self._serializer("http://b1:80,http://b2:80")
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["backup_server_urls"],
            ["http://b1:80", "http://b2:80"],
        )

    def test_invalid_url_rejected(self):
        serializer = self._serializer(["http://ok:80", "not a url"])
        self.assertFalse(serializer.is_valid())
        self.assertIn("backup_server_urls", serializer.errors)

    def test_too_many_rejected(self):
        serializer = self._serializer([f"http://b{i}:80" for i in range(11)])
        self.assertFalse(serializer.is_valid())
        self.assertIn("backup_server_urls", serializer.errors)


class XCClientFailoverTests(SimpleTestCase):
    def _response(self, payload):
        response = MagicMock()
        response.content = b"{}"
        response.text = "{}"
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_fails_over_and_sticks_to_working_server(self):
        client = XCClient(
            "http://primary:8080", "u", "p", backup_urls=["http://backup:80/player_api.php"]
        )
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            if url.startswith("http://primary"):
                raise requests.exceptions.ConnectionError("down")
            return self._response({"user_info": {"auth": 1}})

        client.session.get = fake_get
        client.authenticate()
        client._make_request("player_api.php", {"action": "get_live_categories"})

        self.assertEqual(
            calls,
            [
                "http://primary:8080/player_api.php",
                "http://backup:80/player_api.php",
                "http://backup:80/player_api.php",
            ],
        )
        self.assertEqual(client.server_url, "http://backup:80")
        # Stored playback URLs always use the primary
        self.assertEqual(client.get_stream_url(5), "http://primary:8080/live/u/p/5.ts")

    def test_raises_first_error_when_all_fail(self):
        client = XCClient("http://primary", "u", "p", backup_urls=["http://backup"])
        client.session.get = MagicMock(side_effect=requests.exceptions.ConnectionError("down"))
        with self.assertRaises(requests.exceptions.ConnectionError):
            client._make_request("player_api.php")
        self.assertEqual(client.session.get.call_count, 2)
        self.assertEqual(client.server_url, "http://primary")

    def test_auth_error_does_not_fail_over(self):
        client = XCClient("http://primary", "u", "p", backup_urls=["http://backup"])
        client.session.get = MagicMock(
            return_value=self._response({"user_info": None, "error": "bad login"})
        )
        with self.assertRaises(ValueError):
            client._make_request("player_api.php")
        self.assertEqual(client.session.get.call_count, 1)


class FetchM3ULinesFailoverTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="STD failover",
            account_type="STD",
            server_url="http://primary/get.php",
            backup_server_urls=["http://b1/get.php", "http://b2/get.php"],
        )

    def test_tries_backups_in_order_and_restores_primary(self):
        seen = []

        def fake_fetch(account, use_cache=False):
            seen.append((account.server_url, m3u_tasks._suppress_m3u_error_notify.get()))
            if account.server_url == "http://b2/get.php":
                return "/tmp/file.m3u", True
            return None, False

        with patch.object(m3u_tasks, "_fetch_m3u_lines_from_server", side_effect=fake_fetch), \
                patch.object(m3u_tasks, "send_m3u_update"):
            source, success = m3u_tasks.fetch_m3u_lines(self.account)

        self.assertTrue(success)
        self.assertEqual(source, "/tmp/file.m3u")
        self.assertEqual(
            seen,
            [
                ("http://primary/get.php", True),
                ("http://b1/get.php", True),
                ("http://b2/get.php", False),
            ],
        )
        self.assertEqual(self.account.server_url, "http://primary/get.php")
        self.account.refresh_from_db()
        self.assertEqual(self.account.server_url, "http://primary/get.php")

    def test_without_backups_fetches_once(self):
        self.account.backup_server_urls = []
        with patch.object(
            m3u_tasks, "_fetch_m3u_lines_from_server", return_value=(None, False)
        ) as fetch:
            self.assertEqual(m3u_tasks.fetch_m3u_lines(self.account), (None, False))
        fetch.assert_called_once()


@patch("apps.m3u.server_urls.get_preferred_server_url", return_value=None)
class LiveStreamUrlCandidateTests(TestCase):
    def test_xc_candidates_use_each_server(self, _preferred):
        account = M3UAccount.objects.create(
            name="XC backups",
            account_type="XC",
            server_url="http://primary:8080/player_api.php",
            backup_server_urls=["http://backup1:80", "https://backup2/sub/"],
            username="alice",
            password="secret",
        )
        profile = M3UAccountProfile.objects.get(m3u_account=account, is_default=True)
        stream = Stream.objects.create(
            name="Ch", m3u_account=account, stream_id="12", url="http://primary:8080/live/a/b/12.ts"
        )

        candidates = get_live_stream_url_candidates(stream, account, profile)

        self.assertEqual(
            [url for _, url in candidates],
            [
                "http://primary:8080/live/alice/secret/12.ts",
                "http://backup1:80/live/alice/secret/12.ts",
                "https://backup2/sub/live/alice/secret/12.ts",
            ],
        )

    def test_xc_profile_credentials_apply_to_backups(self, _preferred):
        account = M3UAccount.objects.create(
            name="XC profile",
            account_type="XC",
            server_url="http://primary:8080",
            backup_server_urls=["http://backup1:80"],
            username="alice",
            password="secret",
        )
        profile = M3UAccountProfile.objects.create(
            m3u_account=account,
            name="Second login",
            search_pattern="alice/secret",
            replace_pattern="bob/hunter2",
        )
        stream = Stream.objects.create(name="Ch", m3u_account=account, stream_id="12", url="")

        candidates = get_live_stream_url_candidates(stream, account, profile)

        self.assertEqual(
            [url for _, url in candidates],
            [
                "http://primary:8080/live/bob/hunter2/12.ts",
                "http://backup1:80/live/bob/hunter2/12.ts",
            ],
        )

    def test_std_candidates_swap_host(self, _preferred):
        account = M3UAccount.objects.create(
            name="STD backups",
            account_type="STD",
            server_url="http://primary:8080/get.php?username=u&password=p",
            backup_server_urls=["http://mirror:25461/get.php?username=u&password=p"],
        )
        profile = M3UAccountProfile.objects.get(m3u_account=account, is_default=True)
        stream = Stream.objects.create(
            name="Ch", m3u_account=account, url="http://primary:8080/u/p/99"
        )
        cdn_stream = Stream.objects.create(
            name="CDN", m3u_account=account, url="https://cdn.example/x/99.m3u8"
        )

        self.assertEqual(
            [url for _, url in get_live_stream_url_candidates(stream, account, profile)],
            ["http://primary:8080/u/p/99", "http://mirror:25461/u/p/99"],
        )
        self.assertEqual(
            [url for _, url in get_live_stream_url_candidates(cdn_stream, account, profile)],
            ["https://cdn.example/x/99.m3u8"],
        )

    def test_preferred_server_is_used_first(self, preferred):
        account = M3UAccount.objects.create(
            name="Preferred",
            account_type="XC",
            server_url="http://primary:8080",
            backup_server_urls=["http://backup1:80"],
            username="alice",
            password="secret",
        )
        profile = M3UAccountProfile.objects.get(m3u_account=account, is_default=True)
        stream = Stream.objects.create(name="Ch", m3u_account=account, stream_id="3", url="")
        preferred.return_value = "http://backup1:80"

        self.assertEqual(
            _resolve_live_stream_url(stream, account, profile),
            "http://backup1:80/live/alice/secret/3.ts",
        )


class TestServerUrlsEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pw", user_level=10)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_uses_stored_password_when_blank(self):
        account = M3UAccount.objects.create(
            name="Stored creds",
            account_type="XC",
            server_url="http://primary",
            username="alice",
            password="stored-secret",
        )

        def fake_check(url, **kwargs):
            return {"url": url, "success": True, "response_time_ms": 12, "error": None,
                    "password": kwargs["password"]}

        with patch("apps.m3u.server_url_checks.check_server_url", side_effect=fake_check):
            response = self.client.post(
                "/api/m3u/accounts/test-server-urls/",
                {
                    "urls": ["http://primary", "http://backup"],
                    "account_type": "XC",
                    "username": "alice",
                    "password": "",
                    "account_id": account.id,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        results = response.json()["results"]
        self.assertEqual([r["url"] for r in results], ["http://primary", "http://backup"])
        self.assertTrue(all(r["password"] == "stored-secret" for r in results))

    def test_requires_urls(self):
        response = self.client.post(
            "/api/m3u/accounts/test-server-urls/", {"urls": []}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_non_admin_forbidden(self):
        user = User.objects.create_user(username="viewer", password="pw", user_level=0)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            "/api/m3u/accounts/test-server-urls/", {"urls": ["http://a"]}, format="json"
        )
        self.assertEqual(response.status_code, 403)
