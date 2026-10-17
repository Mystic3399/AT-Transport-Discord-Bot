"""Read-only A&T Trucky membership verification, based on observed roster JSON."""
import os
import aiohttp

COMPANY_ID = 49041
BASE_URL = f"https://e.truckyapp.com/api/v1/company/{COMPANY_ID}/members"
MAX_PAGES = 100

class MembershipUnavailable(Exception):
    pass

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
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise MembershipUnavailable("Invalid Trucky roster format")
                current = payload.get("current_page")
                last = payload.get("last_page")
                if type(current) is not int or current != page or type(last) is not int or not (page <= last <= MAX_PAGES):
                    raise MembershipUnavailable("Invalid Trucky roster pagination")
                if last_page is not None and last != last_page:
                    raise MembershipUnavailable("Trucky roster changed during pagination")
                last_page = last
                for member in payload["data"]:
                    if not isinstance(member, dict) or type(member.get("id")) is not int or type(member.get("company_id")) is not int or not isinstance(member.get("name"), str):
                        raise MembershipUnavailable("Invalid Trucky member record")
                    if member["company_id"] != COMPANY_ID:
                        raise MembershipUnavailable("Unexpected Trucky company in roster")
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
