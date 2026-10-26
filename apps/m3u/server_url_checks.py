"""Reachability / latency checks for M3U account server URLs."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

CHECK_TIMEOUT = (5, 10)  # connect, read


def check_server_url(url, account_type="STD", username="", password="", user_agent=None):
    """Probe one server URL.

    XC accounts are checked by authenticating against ``player_api.php``;
    standard M3U URLs by fetching the first bytes of the playlist. The reported
    time is until the server answered, which is what makes a server feel slow
    when a channel starts.
    """
    from apps.m3u.models import M3UAccount
    from core.xtream_codes import normalize_server_url

    result = {"url": url, "success": False, "response_time_ms": None, "error": None}
    headers = {"User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    start = time.monotonic()

    try:
        if account_type == M3UAccount.Types.XC:
            if not (username and password):
                result["error"] = "Username and password are required"
                return result
            api_url = f"{normalize_server_url(url).rstrip('/')}/player_api.php"
            response = requests.get(
                api_url,
                params={"username": username, "password": password},
                headers=headers,
                timeout=CHECK_TIMEOUT,
            )
            result["response_time_ms"] = int((time.monotonic() - start) * 1000)
            if response.status_code >= 400:
                result["error"] = f"HTTP {response.status_code}"
                return result
            try:
                data = response.json()
            except ValueError:
                result["error"] = "Invalid response (not JSON)"
                return result
            user_info = data.get("user_info") if isinstance(data, dict) else None
            if not user_info:
                result["error"] = "Login rejected"
            elif str(user_info.get("auth", "1")) == "0":
                result["error"] = "Login rejected"
            else:
                result["success"] = True
        else:
            with requests.get(
                url, headers=headers, timeout=CHECK_TIMEOUT, stream=True
            ) as response:
                if response.status_code >= 400:
                    result["response_time_ms"] = int((time.monotonic() - start) * 1000)
                    result["error"] = f"HTTP {response.status_code}"
                    return result
                next(response.iter_content(chunk_size=1024), b"")
                result["response_time_ms"] = int((time.monotonic() - start) * 1000)
                result["success"] = True
    except requests.exceptions.Timeout:
        result["error"] = "Timed out"
    except requests.exceptions.ConnectionError as exc:
        text = str(exc)
        if "Name or service not known" in text or "getaddrinfo failed" in text or "NameResolutionError" in text:
            result["error"] = "DNS lookup failed"
        elif "Connection refused" in text:
            result["error"] = "Connection refused"
        else:
            result["error"] = "Connection error"
    except requests.exceptions.RequestException as exc:
        result["error"] = type(exc).__name__
    except Exception:
        logger.exception("Server URL check failed for %s", url)
        result["error"] = "Check failed"

    if result["response_time_ms"] is None:
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
    return result
