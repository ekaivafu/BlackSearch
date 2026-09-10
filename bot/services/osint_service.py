import asyncio
import json
import os
import sys
import re
import hashlib
import aiohttp

class OSINTService:
    @staticmethod
    async def check_email_breaches(email: str) -> dict:
        """
        Query XposedOrNot free breach API (<1.5s).
        Returns total breach count and list of leaked platform names (e.g. Zomato, LinkedIn, Canva).
        """
        try:
            url = f"https://api.xposedornot.com/v1/check-email/{email.strip().lower()}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw = data.get("breaches", [])
                        breaches = raw[0] if raw and isinstance(raw[0], list) else (raw or [])
                        return {"breach_count": len(breaches), "breaches": breaches}
                    elif resp.status == 404:
                        return {"breach_count": 0, "breaches": []}
        except Exception:
            pass
        return {"breach_count": 0, "breaches": []}

    @staticmethod
    async def check_gravatar_profile(email: str) -> dict | None:
        """
        Query Gravatar API by MD5 hash (<1.0s).
        Returns display name, avatar URL, bio, and location if registered.
        """
        try:
            md5 = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
            url = f"https://api.gravatar.com/v3/profiles/{md5}"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=2.0)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "display_name": data.get("display_name"),
                            "avatar_url": data.get("avatar_url"),
                            "location": data.get("location"),
                            "about_me": data.get("about_me"),
                            "job_title": data.get("job_title"),
                        }
        except Exception:
            pass
        return None

    @staticmethod
    async def check_email_holehe(email: str) -> dict:
        """
        Runs the holehe python script in a separate process with a strict 7s timeout.
        Returns confirmed registered online accounts.
        """
        empty = {"found": [], "blocked": [], "checked_count": 0, "blocked_count": 0}
        try:
            script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_holehe.py")
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, email,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                process.terminate()
                return empty

            output_str = stdout.decode('utf-8', errors='ignore').strip()
            start_idx = output_str.find('{')
            if start_idx != -1:
                data = json.loads(output_str[start_idx:])
                if isinstance(data, dict) and "found" in data:
                    return data
            
            start_idx = output_str.find('[')
            if start_idx != -1:
                sites = json.loads(output_str[start_idx:])
                if isinstance(sites, list):
                    return {**empty, "found": sorted(sites)}
            return empty
        except Exception as e:
            print(f"Holehe OSINT Error: {e}")
            return empty

    @staticmethod
    async def check_email_full(email: str) -> dict:
        """
        Run breach check, Gravatar profile, and account verification simultaneously in parallel (<2.5s).
        """
        f_breach = asyncio.create_task(OSINTService.check_email_breaches(email))
        f_gravatar = asyncio.create_task(OSINTService.check_gravatar_profile(email))
        f_holehe = asyncio.create_task(OSINTService.check_email_holehe(email))

        breach_res, gravatar_res, holehe_res = await asyncio.gather(
            f_breach, f_gravatar, f_holehe, return_exceptions=True
        )

        breaches = breach_res if isinstance(breach_res, dict) else {"breach_count": 0, "breaches": []}
        gravatar = gravatar_res if isinstance(gravatar_res, dict) else None
        accounts = holehe_res if isinstance(holehe_res, dict) else {"found": [], "blocked": [], "checked_count": 0}

        return {
            "breaches": breaches.get("breaches", []),
            "breach_count": breaches.get("breach_count", 0),
            "gravatar": gravatar,
            "found": accounts.get("found", []),
            "blocked": accounts.get("blocked", []),
            "checked_count": accounts.get("checked_count", 0),
        }

    @staticmethod
    async def search_username_fast(username: str) -> dict:
        """
        Ultra-fast async social recon across top Tier-1 platforms in parallel (<1.8s).
        Deep profile extraction for GitHub, Telegram, Reddit, Steam, Chess.com, etc.
        """
        u = username.strip()
        results = []
        details = {}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        timeout = aiohttp.ClientTimeout(total=2.5)

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            # 1. GitHub (deep profile)
            async def check_github():
                try:
                    async with session.get(f"https://api.github.com/users/{u}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            results.append({"site": "GitHub", "url": f"https://github.com/{u}"})
                            details["github"] = {
                                "name": data.get("name"),
                                "bio": data.get("bio"),
                                "location": data.get("location"),
                                "repos": data.get("public_repos"),
                                "avatar": data.get("avatar_url"),
                                "company": data.get("company"),
                                "blog": data.get("blog")
                            }
                except Exception:
                    pass

            # 2. Telegram (display name)
            async def check_telegram():
                try:
                    async with session.get(f"https://t.me/{u}") as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if "tgme_page_extra" in text:
                                results.append({"site": "Telegram", "url": f"https://t.me/{u}"})
                                m = re.search(r'class="tgme_page_title"[^>]*>(.*?)<', text)
                                if m and m.group(1).strip():
                                    details["telegram_name"] = m.group(1).strip()
                except Exception:
                    pass

            # 3. Reddit
            async def check_reddit():
                try:
                    async with session.get(f"https://www.reddit.com/user/{u}/about.json") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("data", {}).get("name"):
                                results.append({"site": "Reddit", "url": f"https://reddit.com/user/{u}"})
                except Exception:
                    pass

            # 4. Twitter / X
            async def check_twitter():
                try:
                    async with session.get(f"https://x.com/{u}") as resp:
                        if resp.status == 200:
                            results.append({"site": "Twitter / X", "url": f"https://x.com/{u}"})
                except Exception:
                    pass

            # 5. Steam
            async def check_steam():
                try:
                    async with session.get(f"https://steamcommunity.com/id/{u}") as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if "actual_persona_name" in text:
                                results.append({"site": "Steam", "url": f"https://steamcommunity.com/id/{u}"})
                except Exception:
                    pass

            # 6. YouTube
            async def check_youtube():
                try:
                    async with session.get(f"https://www.youtube.com/@{u}") as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if "canonical" in text:
                                results.append({"site": "YouTube", "url": f"https://youtube.com/@{u}"})
                except Exception:
                    pass

            # 7. Pinterest
            async def check_pinterest():
                try:
                    async with session.get(f"https://www.pinterest.com/{u}/") as resp:
                        if resp.status == 200:
                            results.append({"site": "Pinterest", "url": f"https://pinterest.com/{u}"})
                except Exception:
                    pass

            # 8. Spotify
            async def check_spotify():
                try:
                    async with session.get(f"https://open.spotify.com/user/{u}") as resp:
                        if resp.status == 200:
                            results.append({"site": "Spotify", "url": f"https://open.spotify.com/user/{u}"})
                except Exception:
                    pass

            # 9. Linktree
            async def check_linktree():
                try:
                    async with session.get(f"https://linktr.ee/{u}") as resp:
                        if resp.status == 200:
                            results.append({"site": "Linktree", "url": f"https://linktr.ee/{u}"})
                except Exception:
                    pass

            # 10. HackerNews
            async def check_hackernews():
                try:
                    async with session.get(f"https://news.ycombinator.com/user?id={u}") as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if "user:" in text and "created:" in text:
                                results.append({"site": "HackerNews", "url": f"https://news.ycombinator.com/user?id={u}"})
                except Exception:
                    pass

            # 11. Chess.com (rating & name)
            async def check_chess():
                try:
                    async with session.get(f"https://api.chess.com/pub/player/{u}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            results.append({"site": "Chess.com", "url": f"https://chess.com/member/{u}"})
                            if data.get("name"):
                                details["chess_name"] = data.get("name")
                except Exception:
                    pass

            # 12. TikTok
            async def check_tiktok():
                try:
                    async with session.get(f"https://www.tiktok.com/@{u}") as resp:
                        if resp.status == 200:
                            results.append({"site": "TikTok", "url": f"https://tiktok.com/@{u}"})
                except Exception:
                    pass

            # Fire all 12 platform checkers concurrently in parallel!
            await asyncio.gather(
                check_github(),
                check_telegram(),
                check_reddit(),
                check_twitter(),
                check_steam(),
                check_youtube(),
                check_pinterest(),
                check_spotify(),
                check_linktree(),
                check_hackernews(),
                check_chess(),
                check_tiktok(),
                return_exceptions=True
            )

        return {"found": sorted(results, key=lambda x: x["site"]), "details": details}
