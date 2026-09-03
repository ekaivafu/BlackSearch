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
                osint_sites   = await asyncio.wait_for(OSINTService.search_username_sherlock(query), timeout=75.0)
                osint_blocked = []   # not applicable for username searches
                osint_checked = 0
                data = {"count": 0, "results": []}
            else:
                data_future = loop.run_in_executor(
                    duckdb_service.pool, 
                    duckdb_service.run_sync_search, 
                    search_type, 
                    query
                )
                
                # Check OSINT concurrently if it's an email
                if search_type == "email":
                    from bot.services.osint_service import OSINTService
                    osint_future = asyncio.create_task(OSINTService.check_email_holehe(query))
                    data, osint_result = await asyncio.gather(
                        asyncio.wait_for(data_future, timeout=300.0),
                        asyncio.wait_for(osint_future, timeout=120.0)
                    )
                    # Unpack new dict format (with fallback for old list format)
                    if isinstance(osint_result, dict):
                        osint_sites   = osint_result.get("found", [])
                        osint_blocked = osint_result.get("blocked", [])
                        osint_checked = osint_result.get("checked_count", 0)
                    else:
                        osint_sites   = osint_result if isinstance(osint_result, list) else []
                        osint_blocked = []
                        osint_checked = 0
                else:
                    data = await asyncio.wait_for(data_future, timeout=300.0)
                    osint_sites, osint_blocked, osint_checked = [], [], 0
                
            duration = round(time.time() - start_time, 2)
            is_success = bool(data.get("count", 0)) or bool(osint_sites)
            
            # Format Database Results
            if data.get("count", 0):
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  <b>Found:</b> {data['count']} records  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                results_text += "<b>--- Intelligence Records ---</b>\n\n"
                for i, row in enumerate(data["results"], 1):
                    results_text += f"<b>--- Record {i} ---</b>\n"
                    results_text += duckdb_service.format_result(row) + "\n\n"
            elif search_type != "username":
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n<b>--- Intelligence Records ---</b>\n❌ No records found in database.\n\n"
            else:
                results_text = f"🔍 <b>Query:</b> <code>{safe_query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                
            # Format OSINT Results
            if search_type == "email":
                results_text += "<b>--- Connected Online Profiles ---</b>\n"
                results_text += f"<i>Checked {osint_checked} platforms</i>\n"
                if osint_sites:
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected account(s)!</b>\n"
                    for site in osint_sites:
                        results_text += f"🟢 {html.escape(site)}\n"
                else:
                    results_text += "❌ No linked accounts found on checked platforms.\n"
                if osint_blocked:
                    results_text += f"\n⚠️ <b>{len(osint_blocked)} platform(s) protected by firewall</b>:\n"
                    results_text += ", ".join(f"<code>{html.escape(s)}</code>" for s in osint_blocked[:15])
                    if len(osint_blocked) > 15:
                        results_text += f" (+{len(osint_blocked) - 15} more)"
                    results_text += "\n"
            elif search_type == "username":
                results_text += "<b>--- Connected Online Profiles ---</b>\n"
                if osint_sites:
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected accounts!</b>\n"
                    for item in osint_sites:
                        safe_url = html.escape(item.get("url", ""), quote=True)
                        safe_site = html.escape(item.get("site", ""))
                        results_text += f"🟢 <a href='{safe_url}'>{safe_site}</a>\n"
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
