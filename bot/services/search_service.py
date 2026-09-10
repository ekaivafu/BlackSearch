import logging
import asyncio
import time
from sqlalchemy.ext.asyncio import AsyncSession
from bot.models.models import SearchLog, User
from bot.services import duckdb_service

import html

logger = logging.getLogger(__name__)

class SearchService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(self, user: User, query: str, search_type: str = "phone") -> dict:
        """
        Perform a search using DuckDB.
        """
        # 1. Validate query
        if len(query) < 4:
            return {"success": False, "message": "Query too short."}
        
        safe_query = html.escape(query)

        # 2. Perform search (async)
        loop = asyncio.get_running_loop()
        try:
            start_time = time.time()
            
            if search_type == "username":
                from bot.services.osint_service import OSINTService
                db_future = loop.run_in_executor(
                    duckdb_service.pool, 
                    duckdb_service.run_sync_search, 
                    "username", 
                    query
                )
                osint_future = asyncio.create_task(OSINTService.search_username_fast(query))
                data, osint_resp = await asyncio.gather(
                    asyncio.wait_for(db_future, timeout=10.0),
                    asyncio.wait_for(osint_future, timeout=6.0)
                )
                osint_sites = osint_resp.get("found", []) if isinstance(osint_resp, dict) else []
                osint_details = osint_resp.get("details", {}) if isinstance(osint_resp, dict) else {}
                osint_blocked = []
                osint_checked = len(osint_sites)
                email_breaches, breach_count, gravatar_info = [], 0, None
            elif search_type == "email":
                from bot.services.osint_service import OSINTService
                data_future = loop.run_in_executor(
                    duckdb_service.pool, 
                    duckdb_service.run_sync_search, 
                    search_type, 
                    query
                )
                osint_future = asyncio.create_task(OSINTService.check_email_full(query))
                data, osint_result = await asyncio.gather(
                    asyncio.wait_for(data_future, timeout=15.0),
                    asyncio.wait_for(osint_future, timeout=10.0)
                )
                if isinstance(osint_result, dict):
                    osint_sites   = osint_result.get("found", [])
                    osint_blocked = osint_result.get("blocked", [])
                    osint_checked = osint_result.get("checked_count", 0)
                    email_breaches = osint_result.get("breaches", [])
                    breach_count  = osint_result.get("breach_count", 0)
                    gravatar_info = osint_result.get("gravatar")
                else:
                    osint_sites, osint_blocked, osint_checked = [], [], 0
                    email_breaches, breach_count, gravatar_info = [], 0, None
                osint_details = {}
            else:
                data_future = loop.run_in_executor(
                    duckdb_service.pool, 
                    duckdb_service.run_sync_search, 
                    search_type, 
                    query
                )
                data = await asyncio.wait_for(data_future, timeout=300.0)
                osint_sites, osint_blocked, osint_checked = [], [], 0
                osint_details = {}
                email_breaches, breach_count, gravatar_info = [], 0, None
                
            duration = round(time.time() - start_time, 2)
            is_success = bool(data.get("count", 0)) or bool(osint_sites) or bool(breach_count)
            
            # Format Database Results
            if data.get("count", 0):
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  <b>Found:</b> {data['count']} records  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                title = "--- Identity Linkage (from Vault) ---" if search_type == "username" else "--- Intelligence Records ---"
                results_text += f"<b>{title}</b>\n\n"
                for i, row in enumerate(data["results"], 1):
                    results_text += f"<b>--- Record {i} ---</b>\n"
                    results_text += duckdb_service.format_result(row) + "\n\n"
            elif search_type not in ("username", "email"):
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n<b>--- Intelligence Records ---</b>\n❌ No records found in database.\n\n"
            else:
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                
            # Format Gravatar Profile if present
            if gravatar_info:
                results_text += "<b>--- Public Profile (Gravatar) ---</b>\n"
                if gravatar_info.get("display_name"):
                    results_text += f"👤 <b>Name:</b> {html.escape(gravatar_info['display_name'])}\n"
                if gravatar_info.get("job_title"):
                    results_text += f"💼 <b>Title:</b> {html.escape(gravatar_info['job_title'])}\n"
                if gravatar_info.get("location"):
                    results_text += f"📍 <b>Location:</b> {html.escape(gravatar_info['location'])}\n"
                if gravatar_info.get("about_me"):
                    results_text += f"📝 <b>Bio:</b> {html.escape(gravatar_info['about_me'])[:200]}\n"
                results_text += "\n"

            # Format Data Breach History
            if breach_count > 0:
                results_text += f"🛡️ <b>Data Breach History:</b> Found in <b>{breach_count}</b> leaked database(s)!\n"
                top_breaches = ", ".join(f"<code>{html.escape(b)}</code>" for b in email_breaches[:12])
                results_text += f"⚠️ <b>Compromised on:</b> {top_breaches}"
                if len(email_breaches) > 12:
                    results_text += f" <i>(+{len(email_breaches)-12} more leaks)</i>"
                results_text += "\n\n"

            # Format OSINT Results
            if search_type == "email":
                if osint_sites:
                    results_text += "<b>--- Connected Online Profiles ---</b>\n"
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected account(s)!</b>\n"
                    for site in osint_sites:
                        results_text += f"🟢 {html.escape(site.capitalize())}\n"
                if osint_blocked:
                    results_text += f"\n⚠️ <b>{len(osint_blocked)} platform(s) protected by firewall</b>:\n"
                    results_text += ", ".join(f"<code>{html.escape(s)}</code>" for s in osint_blocked[:12])
                    results_text += "\n"
            elif search_type == "username":
                results_text += "<b>--- Connected Online Profiles ---</b>\n"
                if osint_sites:
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected account(s)!</b>\n"
                    gh_details = osint_details.get("github")
                    tg_name = osint_details.get("telegram_name")
                    chess_name = osint_details.get("chess_name")

                    for item in osint_sites:
                        safe_url = html.escape(item.get("url", ""), quote=True)
                        site_name = item.get("site", "")
                        safe_site = html.escape(site_name)
                        
                        extra = ""
                        if site_name == "GitHub" and gh_details:
                            parts = []
                            if gh_details.get("name"):
                                parts.append(html.escape(gh_details["name"]))
                            if gh_details.get("location"):
                                parts.append(html.escape(gh_details["location"]))
                            if gh_details.get("repos") is not None:
                                parts.append(f"{gh_details['repos']} repos")
                            if parts:
                                extra = f" — <i>{' | '.join(parts)}</i>"
                        elif site_name == "Telegram" and tg_name:
                            extra = f" — <i>{html.escape(tg_name)}</i>"
                        elif site_name == "Chess.com" and chess_name:
                            extra = f" — <i>{html.escape(chess_name)}</i>"

                        results_text += f"🟢 <a href='{safe_url}'><b>{safe_site}</b></a>{extra}\n"
                else:
                    results_text += "❌ No linked accounts found on scanned platforms.\n"
                    
            mock_result = results_text.strip()
            
        except asyncio.TimeoutError:
            logger.error(f"Search timed out for {query}")
            is_success = False
            mock_result = "<b>Search Timed Out:</b> The query took longer than expected to resolve. Please try again in a few moments."
        except Exception as e:
            logger.error(f"Search error for {query}: {e}", exc_info=True)
            is_success = False
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str or "too many requests" in err_str:
                mock_result = "<b>High Network Load:</b> The intelligence database is currently handling heavy search volume. Please wait 1-2 minutes and try again."
            else:
                mock_result = "<b>Service Temporarily Busy:</b> Could not retrieve records from the intelligence database. Please try again shortly."

        # 3. Log the search
        log = SearchLog(
            user_id=user.id,
            query_metadata={"query": query, "type": search_type},
            success=1 if is_success else 0,
            credits_used=1 if is_success else 0
        )
        self.session.add(log)
        
        # 4. Increment total searches
        if is_success:
            user.total_searches += 1
            
        await self.session.flush()

        return {"success": is_success, "data": mock_result}

    async def search_deep_phone(self, user: User, phone: str) -> dict:
        """
        Executes a 4-hop Deep OSINT investigation on a phone number, costing 3 credits.
        Pivots: Target profile -> Aadhaar reverse SIMs -> Alt contacts -> Family/Households -> Digital footprint.
        """
        if len(phone) < 10:
            return {"success": False, "message": "Query too short."}

        safe_query = html.escape(phone)
        loop = asyncio.get_running_loop()
        start_time = time.time()
        is_success = False

        try:
            deep_future = loop.run_in_executor(
                duckdb_service.pool,
                duckdb_service.run_deep_phone_search,
                phone
            )
            raw_res = await asyncio.wait_for(deep_future, timeout=35.0)

            deep_data = raw_res.get("deep_data")
            is_success = bool(raw_res.get("count", 0)) and bool(deep_data)

            if is_success:
                target_email = deep_data.get("email")
                email_osint = None
                if target_email and not duckdb_service.is_invalid_val(target_email):
                    from bot.services.osint_service import OSINTService
                    try:
                        email_osint = await asyncio.wait_for(
                            OSINTService.check_email_full(str(target_email).strip()),
                            timeout=6.0
                        )
                    except Exception as oe:
                        logger.debug(f"Email OSINT pivot notice: {oe}")

                duration = round(time.time() - start_time, 2)
                mock_result = duckdb_service.format_deep_phone_result(deep_data, duration=duration, email_osint=email_osint)
            else:
                duration = round(time.time() - start_time, 2)
                mock_result = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n<b>--- Intelligence Records ---</b>\n❌ No records found in database."

        except asyncio.TimeoutError:
            logger.error(f"Deep search timed out for {phone}")
            is_success = False
            mock_result = "<b>Search Timed Out:</b> The deep investigation took longer than expected to resolve. Please try again in a few moments."
        except Exception as e:
            logger.error(f"Deep search error for {phone}: {e}", exc_info=True)
            is_success = False
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str or "too many requests" in err_str:
                mock_result = "<b>High Network Load:</b> The intelligence database is currently handling heavy search volume. Please wait 1-2 minutes and try again."
            else:
                mock_result = "<b>Service Temporarily Busy:</b> Could not retrieve records from the intelligence database. Please try again shortly."

        # Log the search with credits_used = 3
        log = SearchLog(
            user_id=user.id,
            query_metadata={"query": phone, "type": "phone_deep"},
            success=1 if is_success else 0,
            credits_used=3 if is_success else 0
        )
        self.session.add(log)

        if is_success:
            user.total_searches += 1

        await self.session.flush()

        return {"success": is_success, "data": mock_result}

