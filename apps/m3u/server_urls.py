"""
Backup (failover) server URLs for M3U accounts.

An account keeps its primary ``server_url`` and an ordered list of
``backup_server_urls``. Backups point at the same provider on another host or
port (mirror domains, alternate ports). They are used:

* during refresh, when the primary cannot be reached, and
* during live playback, where the stream manager tries the same stream on each
  server before moving on to the channel's next stream.

The server that last delivered a working stream is remembered in Redis for a
while, so new channels start on it instead of waiting for a slow primary.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

MAX_BACKUP_SERVER_URLS = 10

# How long a working backup server stays preferred for new connections before
# the primary gets another chance.
PREFERRED_SERVER_TTL_SECONDS = 60 * 60


def parse_server_url_list(value) -> List[str]:
    """Normalize user input (list, or newline / pipe separated text) to a clean list.

    Blank entries are dropped and duplicates removed while keeping order.
    """
    if not value:
        return []
    if isinstance(value, str):
        items = value.replace("|", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []

    urls = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        url = item.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def url_origin(url: Optional[str]) -> Optional[str]:
    """Return ``scheme://host[:port]`` for *url*, lower-cased, or None."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def replace_url_origin(url: str, new_origin: str) -> str:
    """Swap the scheme/host/port of *url* for *new_origin*, keeping path and query."""
    parsed = urlparse(url)
    target = urlparse(new_origin)
    return urlunparse(parsed._replace(scheme=target.scheme, netloc=target.netloc))


def get_account_server_urls(account) -> List[str]:
    """Primary server URL followed by the account's backups (deduplicated)."""
    urls = []
    if account.server_url:
        urls.append(account.server_url.strip())
    for url in parse_server_url_list(getattr(account, "backup_server_urls", None)):
        if url not in urls:
            urls.append(url)
    return urls


def has_backup_server_urls(account) -> bool:
    return len(get_account_server_urls(account)) > 1


def _preferred_server_key(account_id) -> str:
    return f"m3u_account:{account_id}:preferred_server"


def get_preferred_server_url(account_id) -> Optional[str]:
    """Server URL that recently delivered a working stream for this account."""
    try:
        from core.utils import RedisClient

        redis_client = RedisClient.get_client()
        if not redis_client:
            return None
        value = redis_client.get(_preferred_server_key(account_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return value or None
    except Exception as exc:
        logger.debug("Could not read preferred server for account %s: %s", account_id, exc)
        return None


def remember_working_server_url(account_id, primary_url: Optional[str], server_url: str) -> None:
    """Prefer *server_url* for new connections, or clear the preference for the primary."""
    try:
        from core.utils import RedisClient

        redis_client = RedisClient.get_client()
        if not redis_client:
            return
        key = _preferred_server_key(account_id)
        if not server_url or server_url == (primary_url or "").strip():
            redis_client.delete(key)
        else:
            redis_client.setex(key, PREFERRED_SERVER_TTL_SECONDS, server_url)
            logger.info(
                "Preferring server %s for M3U account %s for the next %ss",
                url_origin(server_url),
                account_id,
                PREFERRED_SERVER_TTL_SECONDS,
            )
    except Exception as exc:
        logger.debug("Could not store preferred server for account %s: %s", account_id, exc)


def order_server_urls(account, urls: Optional[Iterable[str]] = None) -> List[str]:
    """Account server URLs with the remembered working server moved to the front."""
    urls = list(urls if urls is not None else get_account_server_urls(account))
    if len(urls) < 2:
        return urls
    preferred = get_preferred_server_url(account.id)
    if preferred and preferred in urls:
        urls.remove(preferred)
        urls.insert(0, preferred)
    return urls


def make_stream_url_canonicalizer(account):
    """Return a function that rewrites stream URLs on a backup host to the primary host.

    A playlist downloaded from a backup server lists its streams on that
    server. Storing them under the primary host keeps stream hashes (which may
    include the URL) stable no matter which server answered the refresh;
    playback picks the server separately. Returns None when there is nothing
    to rewrite.
    """
    primary_origin = url_origin(account.server_url)
    if not primary_origin:
        return None
    backup_origins = {
        origin
        for origin in (url_origin(u) for u in get_account_server_urls(account)[1:])
        if origin and origin != primary_origin
    }
    if not backup_origins:
        return None

    def canonicalize(url):
        if url and url_origin(url) in backup_origins:
            return replace_url_origin(url, primary_origin)
        return url

    return canonicalize
