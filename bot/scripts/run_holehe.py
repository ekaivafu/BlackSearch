"""
run_holehe.py — Comprehensive email OSINT checker
  • holehe   → 45 verified working sites
  • custom   → hand-written checkers for 10 major sites holehe breaks on
  • result   → JSON object printed to stdout:
      {
        "found":         ["discord", ...],   # confirmed registered
        "blocked":       ["instagram", ...], # couldn't check (CAPTCHA/firewall/changed API)
        "checked_count": 55,                 # sites that gave a clear answer
        "blocked_count": 12                  # sites that were blocked/inconclusive
      }
"""

import sys
import re
import json
import random
import trio
import httpx
from holehe.core import import_submodules, get_functions, launch_module

# ── Tuning ───────────────────────────────────────────────────────────────────
CONCURRENCY  = 12       # max parallel requests
JITTER_MIN   = 0.05     # seconds between slots (lower bound)
JITTER_MAX   = 0.25
TIMEOUT      = 15       # per-request timeout

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Custom checkers ──────────────────────────────────────────────────────────
# Each function returns:
#   True  – email IS registered on that site
#   False – email is NOT registered
#   None  – couldn't determine (site blocked / changed / error)

async def _spotify(email: str, client: httpx.AsyncClient):
    """Spotify signup validation — status 20 = already registered."""
    try:
        r = await client.get(
            "https://spclient.wg.spotify.com/signup/public/v1/account",
            params={"validate": "1", "email": email},
        )
        return r.json().get("status") == 20
    except Exception:
        return None


async def _github(email: str, client: httpx.AsyncClient):
    """GitHub signup email check — empty body = email taken."""
    try:
        r = await client.get("https://github.com/signup")
        m = re.search(
            r'name="authenticity_token"[^>]+value="([^"]+)"', r.text
        )
        if not m:
            return None
        r2 = await client.post(
            "https://github.com/signup_check/email",
            data={"value": email, "authenticity_token": m.group(1)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        body = r2.text.strip()
        # Empty body → email already taken; non-empty → validation msg (available)
        return body == "" or "taken" in body.lower()
    except Exception:
        return None


async def _instagram(email: str, client: httpx.AsyncClient):
    """Instagram account recovery — 'ok' message = email is registered."""
    try:
        r = await client.get(
            "https://www.instagram.com/accounts/password/reset/"
        )
        csrf = r.cookies.get("csrftoken", "")
        r2 = await client.post(
            "https://www.instagram.com/api/v1/web/accounts/account_recovery_send_ajax/",
            data={"email_or_username": email},
            headers={
                "X-CSRFToken": csrf,
                "Referer": "https://www.instagram.com/accounts/password/reset/",
                "X-Instagram-Ajax": "1",
            },
        )
        return r2.json().get("message") == "ok"
    except Exception:
        return None


async def _patreon(email: str, client: httpx.AsyncClient):
    """Patreon login attempt — error code 9 = wrong password (email exists),
    error code 8 = email not found."""
    try:
        r = await client.post(
            "https://www.patreon.com/api/auth",
            params={"json-api-version": "1.0"},
            json={
                "data": {
                    "type": "user",
                    "attributes": {
                        "email": email,
                        "password": "X!wrong_pass_xyz_99",
                    },
                }
            },
        )
        for err in r.json().get("errors", []):
            code = err.get("code")
            if code == 9:   # wrong password → email exists
                return True
            if code == 8:   # email not found
                return False
        return None
    except Exception:
        return None


async def _pinterest(email: str, client: httpx.AsyncClient):
    """Pinterest signup email validation."""
    try:
        r = await client.post(
            "https://www.pinterest.com/resource/UserSessionResource/create/",
            json={
                "options": {
                    "username_or_email": email,
                    "password": "wrong_pass_xyz",
                }
            },
            headers={"Referer": "https://www.pinterest.com/"},
        )
        body = r.text.lower()
        # 'password' in response = account exists (wrong password)
        # 'not found' or 'no account' = not registered
        if "password" in body and "incorrect" in body:
            return True
        if "not found" in body or "no account" in body:
            return False
        return None
    except Exception:
        return None


async def _snapchat(email: str, client: httpx.AsyncClient):
    """Snapchat forgot password — 200 with 'success' = email registered."""
    try:
        r = await client.post(
            "https://accounts.snapchat.com/accounts/forgot_password",
            data={"email": email},
            headers={"Referer": "https://accounts.snapchat.com/accounts/forgot_password"},
        )
        body = r.text.lower()
        return r.status_code == 200 and (
            "sent" in body or "check your" in body or "reset" in body
        )
    except Exception:
        return None


async def _tumblr(email: str, client: httpx.AsyncClient):
    """Tumblr login attempt — wrong-password error = email exists."""
    try:
        r = await client.post(
            "https://www.tumblr.com/login",
            data={
                "determine_email": email,
                "user[email]": email,
                "user[password]": "wrong_pass_xyz_99",
                "tumblelog[name]": "",
                "user[age]": "",
                "PA": "0",
                "S": "signup_index",
                "N": "",
            },
            headers={"Referer": "https://www.tumblr.com/login"},
        )
        body = r.text.lower()
        if "password" in body and ("wrong" in body or "incorrect" in body):
            return True
        if "email" in body and ("not found" in body or "exist" in body):
            return False
        return None
    except Exception:
        return None


async def _soundcloud(email: str, client: httpx.AsyncClient):
    """SoundCloud sign-in — response shape differs by whether email exists."""
    try:
        r = await client.post(
            "https://api-v2.soundcloud.com/sign-in/password",
            json={"identifier": email, "password": "wrong_pass_xyz"},
            headers={
                "Origin": "https://soundcloud.com",
                "Referer": "https://soundcloud.com/signin",
            },
        )
        body = r.text.lower()
        # 'password' in 400 body = email exists (wrong pass)
        # 'identifier' error = email not found
        if r.status_code == 400 and "password" in body:
            return True
        if "identifier" in body or "not found" in body:
            return False
        return None
    except Exception:
        return None


async def _replit(email: str, client: httpx.AsyncClient):
    """Replit sign-up identifier check — hasPwAuth = email already registered."""
    try:
        r = await client.post(
            "https://replit.com/signup_v2/identifier",
            json={"email": email},
            headers={"Referer": "https://replit.com/signup"},
        )
        data = r.json()
        return bool(data.get("hasPwAuth") or data.get("requiresConfirmation"))
    except Exception:
        return None


async def _discord(email: str, client: httpx.AsyncClient):
    """Discord register attempt — email errors indicate account exists."""
    try:
        r = await client.post(
            "https://discord.com/api/v9/auth/register",
            json={
                "email": email,
                "username": f"chk{random.randint(10000,99999)}",
                "password": "Wr0ng!P@ss99",
                "date_of_birth": "1995-06-15",
                "consent": True,
                "gift_code_sku_id": None,
                "captcha_key": None,
            },
            headers={"Origin": "https://discord.com"},
        )
        errors = r.json().get("errors", {}).get("email", {}).get("_errors", [])
        for e in errors:
            msg = e.get("message", "").lower()
            if "taken" in msg or "registered" in msg or "already" in msg:
                return True
        return None  # captcha wall or different response — can't determine
    except Exception:
        return None


# Registry: name → async checker function
CUSTOM_CHECKERS: dict = {
    "spotify":    _spotify,
    "github":     _github,
    "instagram":  _instagram,
    "patreon":    _patreon,
    "pinterest":  _pinterest,
    "snapchat":   _snapchat,
    "tumblr":     _tumblr,
    "soundcloud": _soundcloud,
    "replit":     _replit,
    "discord":    _discord,
}

# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print(json.dumps({"found": [], "blocked": [], "checked_count": 0, "blocked_count": 0}))
        return

    email = sys.argv[1]
    found:   list[str] = []   # confirmed registered
    blocked: list[str] = []   # couldn't determine (CAPTCHA / firewall / stale API)

    client = httpx.AsyncClient(
        timeout=TIMEOUT,
        headers=BROWSER_HEADERS,
        follow_redirects=True,
    )
    sem = trio.Semaphore(CONCURRENCY)

    # ── 1. holehe (45 verified working modules) ──────────────────────────────
    modules = import_submodules("holehe.modules")

    class Args:
        onlyused          = True
        nopasswordrecovery = False

    websites   = get_functions(modules, Args())
    holehe_out: list[dict] = []

    async def run_holehe_site(website):
        async with sem:
            await trio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            await launch_module(website, email, client, holehe_out)

    async with trio.open_nursery() as nursery:
        for w in websites:
            nursery.start_soon(run_holehe_site, w)

    for item in holehe_out:
        is_blocked = item.get("rateLimit") or item.get("error")
        if is_blocked:
            # holehe flagged as rate-limited / error = site blocked our request
            blocked.append(item["name"])
        elif item.get("exists"):
            found.append(item["name"])
        # exists=False and no block = cleanly confirmed not registered (no action needed)

    # ── 2. Custom checkers for major sites holehe can't handle ───────────────
    custom_results: dict[str, bool | None] = {}

    async def run_custom(name, checker):
        async with sem:
            await trio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            custom_results[name] = await checker(email, client)

    async with trio.open_nursery() as nursery:
        for name, checker in CUSTOM_CHECKERS.items():
            nursery.start_soon(run_custom, name, checker)

    for name, result in custom_results.items():
        if result is True:
            found.append(name)
        elif result is None:
            # None = site blocked us (CAPTCHA, Cloudflare, changed endpoint)
            blocked.append(name)
        # result is False = cleanly confirmed not registered

    await client.aclose()

    found_set   = set(found)
    # A site confirmed FOUND should never appear in blocked, even if holehe
    # also flagged it as rateLimit (holehe has a stale discord module, for example).
    blocked_set = set(blocked) - found_set

    output = {
        "found":         sorted(found_set),
        "blocked":       sorted(blocked_set),
        "checked_count": len(holehe_out) + len(custom_results) - len(blocked_set),
        "blocked_count": len(blocked_set),
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        trio.run(main)
    except Exception:
        print(json.dumps({"found": [], "blocked": [], "checked_count": 0, "blocked_count": 0}))
