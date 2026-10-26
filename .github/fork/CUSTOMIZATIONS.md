# Fork customizations

This fork (`dodobardo/Dispatcharr`) tracks upstream
[`Dispatcharr/Dispatcharr`](https://github.com/Dispatcharr/Dispatcharr) `main`
and carries the features below on top of it. `fork-upstream-sync.yml` merges
upstream regularly; when that merge conflicts or breaks tests, Claude resolves
it in CI using this file to know what must survive. Keep it up to date when you
add or change a fork feature.

## Backup server URLs for M3U accounts (failover)

An M3U account has an ordered list of backup server URLs (mirror hosts or
ports of the same provider) next to its primary `server_url`.

- Model: `M3UAccount.backup_server_urls` (JSONField, list of strings),
  migration `apps/m3u/migrations/0020_m3uaccount_backup_server_urls.py`.
  This migration is already applied on the running deployment: never rename
  or renumber it. If upstream adds its own `m3u` migrations, add a merge
  migration (`python manage.py makemigrations --merge --noinput`) instead.
- Helpers: `apps/m3u/server_urls.py` (URL list parsing, preferred-server Redis
  key, origin swapping, stream URL canonicalization) and
  `apps/m3u/server_url_checks.py` (reachability/latency probe).
- API: `backup_server_urls` field on `M3UAccountSerializer`
  (`BackupServerUrlsField`), and `POST /api/m3u/accounts/test-server-urls/`
  (`M3UAccountViewSet.test_server_urls`, admin only).
- Refresh failover:
  - `core/xtream_codes.py` `Client(..., backup_urls=...)`: `_make_request`
    retries the same request on the next server; `primary_server_url` is used
    for stored stream URLs. Every `XCClient(` / `XtreamCodesClient(` call site
    in `apps/m3u/tasks.py` and `apps/vod/tasks.py` passes
    `backup_urls=account.backup_server_urls`; new upstream call sites should
    pass it too.
  - `apps/m3u/tasks.py`: `fetch_m3u_lines` loops over the server URLs and calls
    `_fetch_m3u_lines_from_server`; `_suppress_m3u_error_notify` silences
    errors until the last URL fails; parsed STD stream URLs go through
    `make_stream_url_canonicalizer` so they are stored under the primary host.
- Live playback failover (`apps/proxy/live_proxy`):
  - `url_utils.py`: `_resolve_live_stream_url` returns the first of
    `get_live_stream_url_candidates` (preferred server first);
    `get_server_failover_candidates` serves the stream manager.
  - `input/manager.py`: `_try_next_stream` first calls `_try_next_server_url`
    (same stream, next server) before moving to the channel's next stream;
    `tried_server_urls` / `_pending_working_server` bookkeeping;
    `_remember_working_server` on the first data chunk; `update_url(...,
    server_url=...)`.
- Frontend: `frontend/src/components/forms/M3UBackupUrls.jsx` (editor + Test
  URLs), wired into `forms/M3U.jsx` (`backupUrls` state, submitted as
  `backup_server_urls`); `+N backups` badge in `tables/M3UsTable.jsx`;
  `API.testM3UServerUrls` in `api.js`; helpers in `utils/forms/M3uUtils.js`.
- Tests: `apps/m3u/tests/test_backup_server_urls.py`,
  `apps/proxy/live_proxy/tests/test_server_url_failover.py`, and the backup URL
  cases in `frontend/src/components/forms/__tests__/M3U.test.jsx` and
  `frontend/src/utils/forms/__tests__/M3uUtils.test.js`.

## CI

`.github/workflows/fork-*.yml` and `.github/fork/` are fork-only. Upstream
workflows are disabled in the repository's Actions settings, not deleted, so
upstream merges never conflict on them.
