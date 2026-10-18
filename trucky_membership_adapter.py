"""Read-only A&T Trucky membership verification, based on observed roster JSON."""
import asyncio
import os
import aiohttp

COMPANY_ID = 49041
BASE_URL = f"https://e.truckyapp.com/api/v1/company/{COMPANY_ID}/members"
MAX_PAGES = 100

class MembershipUnavailable(Exception):
    """Sanitized Trucky member lookup failure with a stable diagnostic code."""

    def __init__(self, message, diagnostic_code="membership_unavailable"):
        super().__init__(message)
        self.diagnostic_code = diagnostic_code

async def verify_member(account_id):
    """Return canonical member name or None. Raise if any page cannot be trusted."""
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        raise ValueError("Trucky account ID must be a positive numeric ID")
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not token:
        raise MembershipUnavailable("TRUCKY_API_TOKEN not configured")
    headers = {"x-access-token": token, "Accept": "application/json",
               "User-Agent": "A&T Transport LTD Command Bot"}
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_page = None
            matches = []
            page = 1
            while page <= MAX_PAGES:
                params = {"page": page, "perPage": 100, "inactive": "false"}
                async with session.get(BASE_URL, params=params, headers=headers,
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        raise MembershipUnavailable(f"Trucky API HTTP {response.status}")
                    payload = await response.json(content_type=None)
                current, last, members = validate_roster_page(payload, page, last_page)
                last_page = last
                for member in members:
                    if member["id"] == int(account_id):
                        matches.append(member["name"])
                if page == last:
                    if len(matches) > 1:
                        raise MembershipUnavailable("Duplicate Trucky member ID")
                    return matches[0] if matches else None
                page += 1
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        if isinstance(error, ValueError) and "account ID" in str(error):
            raise
        raise MembershipUnavailable("Trucky membership lookup failed") from error
    raise MembershipUnavailable("Trucky roster exceeds supported page limit")


def validate_roster_page(payload, page, previous_last_page=None):
    """Validate the observed Trucky company-members pagination contract."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MembershipUnavailable("Invalid Trucky roster format", "invalid_roster_format")
    current, last = payload.get("current_page"), payload.get("last_page")
    if type(current) is not int or current != page or type(last) is not int or not (page <= last <= MAX_PAGES):
        raise MembershipUnavailable("Invalid Trucky roster pagination", "invalid_pagination")
    if previous_last_page is not None and last != previous_last_page:
        raise MembershipUnavailable("Trucky roster changed during pagination", "pagination_changed")
    for member in payload["data"]:
        if not isinstance(member, dict) or type(member.get("id")) is not int or type(member.get("company_id")) is not int or not isinstance(member.get("name"), str) or not member["name"].strip():
            raise MembershipUnavailable("Invalid Trucky member record", "invalid_member_record")
        if member["company_id"] != COMPANY_ID:
            raise MembershipUnavailable("Unexpected Trucky company in roster", "unexpected_company")
    return current, last, payload["data"]


async def inspect_member_identity_fields(account_id):
    """Read-only diagnostic: identify available roster fields, never expose values.

    A Steam field must NOT be trusted for auto-approval until the Trucky API
    contract and its relationship to the authenticated user are validated.
    """
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        raise ValueError("Trucky account ID must be numeric")
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not token:
        raise MembershipUnavailable("Trucky API token not configured", "missing_token")
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"x-access-token": token, "Accept": "application/json",
               "User-Agent": "A&T Transport LTD Command Bot"}
    last_page = None
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for page in range(1, MAX_PAGES + 1):
                async with session.get(
                    BASE_URL, params={"page": page, "perPage": 100, "inactive": "false"},
                    headers=headers, allow_redirects=False
                ) as response:
                    if response.status != 200:
                        # The HTTP status is safe to report; never include body,
                        # URL query, response headers, or credentials.
                        raise MembershipUnavailable(
                            "Trucky roster HTTP request failed",
                            f"http_{response.status}",
                        )
                    payload = await response.json(content_type=None)
                _, last, members = validate_roster_page(payload, page, last_page)
                last_page = last
                matching = [m for m in members if m["id"] == int(account_id)]
                if len(matching) > 1:
                    raise MembershipUnavailable("Duplicate member ID", "duplicate_member_id")
                if matching:
                    # Only return field names/types, never the account data.
                    return {
                        "found": True,
                        "field_types": {
                            key: type(value).__name__
                            for key, value in matching[0].items()
                        },
                        # Membership proves company membership only. The roster
                        # does not expose authenticated Steam ownership, so a
                        # negative boolean would incorrectly imply a mismatch.
                        "steam_id_verified": None,
                        "steam_ownership_evidence": "not_exposed_by_membership_roster",
                    }
                if page == last:
                    return {
                        "found": False,
                        "field_types": {},
                        "steam_id_verified": None,
                        "steam_ownership_evidence": "not_exposed_by_membership_roster",
                    }
    except MembershipUnavailable:
        raise
    except (asyncio.TimeoutError, TimeoutError) as error:
        raise MembershipUnavailable("Trucky roster request timed out", "timeout") from error
    except aiohttp.ClientError as error:
        raise MembershipUnavailable("Trucky roster transport failed", "transport_error") from error
    except (ValueError, TypeError) as error:
        raise MembershipUnavailable("Trucky roster response could not be decoded", "invalid_response") from error
    raise MembershipUnavailable("Trucky roster pagination exceeded limit", "page_limit")
