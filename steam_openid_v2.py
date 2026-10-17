"""Steam OpenID 2.0 verifier for A&T website callback.

This module MUST be called only after a one-time Discord registration state
has been generated server-side. Never trust a user-entered SteamID.
"""
import re
from urllib.parse import urlencode, urlsplit

import aiohttp

ENDPOINT = "https://steamcommunity.com/openid/login"
NAMESPACE = "http://specs.openid.net/auth/2.0"
IDENTIFIER_SELECT = NAMESPACE + "/identifier_select"
CLAIMED = re.compile(r"^https?://steamcommunity\.com/openid/id/(7656119[0-9]{10})$")


def make_login_url(return_to, realm):
    """Return Steam's login URL; return_to must include opaque, short-lived state."""
    callback = urlsplit(return_to)
    root = urlsplit(realm)
    if callback.scheme != "https" or root.scheme != "https":
        raise ValueError("Steam callbacks require HTTPS")
    if callback.netloc != root.netloc or not callback.path.startswith("/"):
        raise ValueError("Steam callback must use the configured site origin")
    return ENDPOINT + "?" + urlencode({
        "openid.ns": NAMESPACE,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": IDENTIFIER_SELECT,
        "openid.claimed_id": IDENTIFIER_SELECT,
    })


async def verify_steam_callback(params, expected_return_to):
    """Verify Steam assertion. Caller must atomically consume state and nonce.

    The caller must check expected_return_to against its saved one-time state,
    expiry, Discord user, and exact callback URL before invoking this function.
    """
    params = dict(params)
    if params.get("openid.ns") != NAMESPACE:
        raise ValueError("Unexpected OpenID namespace")
    if params.get("openid.mode") != "id_res":
        raise ValueError("Unexpected OpenID response mode")
    if params.get("openid.op_endpoint") != ENDPOINT:
        raise ValueError("Untrusted OpenID provider")
    if params.get("openid.return_to") != expected_return_to:
        raise ValueError("OpenID callback URL mismatch")
    claimed = params.get("openid.claimed_id", "")
    match = CLAIMED.fullmatch(claimed)
    if not match or params.get("openid.identity") != claimed:
        raise ValueError("Invalid Steam claimed identity")
    if not params.get("openid.response_nonce") or not params.get("openid.sig"):
        raise ValueError("Missing OpenID signature/nonce")
    payload = dict(params)
    payload["openid.mode"] = "check_authentication"
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(ENDPOINT, data=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Steam authentication endpoint unavailable")
            answer = await response.text()
    if "is_valid:true" not in answer.splitlines():
        raise ValueError("Steam rejected OpenID assertion")
    return match.group(1)

def steam_ids_match(verified_steam_id, provider_steam_id, trusted_provider_source=False):
    """Only match Steam identities returned by a trusted provider integration."""
    if not trusted_provider_source:
        return False
    return (
        isinstance(verified_steam_id, str)
        and isinstance(provider_steam_id, str)
        and CLAIMED.fullmatch("https://steamcommunity.com/openid/id/" + verified_steam_id) is not None
        and verified_steam_id == provider_steam_id
    )
