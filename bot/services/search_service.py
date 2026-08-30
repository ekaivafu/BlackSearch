import logging
import asyncio
import time
from sqlalchemy.ext.asyncio import AsyncSession
from bot.models.models import SearchLog, User
from bot.services import duckdb_service

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
        
        # 2. Perform search (async)
        loop = asyncio.get_running_loop()
        try:
            start_time = time.time()
            data_future = loop.run_in_executor(
                duckdb_service.pool, 
                duckdb_service.run_sync_search, 
                search_type, 
                query
            )
            
            # Check OSINT concurrently if it's an email or username
            if search_type == "email":
                from bot.services.osint_service import OSINTService
                osint_future = asyncio.create_task(OSINTService.check_email_holehe(query))
                data, osint_sites = await asyncio.gather(
                    asyncio.wait_for(data_future, timeout=300.0),
                    asyncio.wait_for(osint_future, timeout=30.0)
                )
            elif search_type == "username":
                from bot.services.osint_service import OSINTService
                osint_future = asyncio.create_task(OSINTService.search_username_sherlock(query))
                data, osint_sites = await asyncio.gather(
                    asyncio.wait_for(data_future, timeout=300.0),
                    asyncio.wait_for(osint_future, timeout=30.0)
                )
            else:
                data = await asyncio.wait_for(data_future, timeout=300.0)
                osint_sites = []
                
            duration = round(time.time() - start_time, 2)
            is_success = bool(data.get("count", 0)) or bool(osint_sites)
            
            # Format Database Results
            if data.get("count", 0):
                results_text = f"🔍 <b>Query:</b> <code>{query}</code>  |  <b>Found:</b> {data['count']} DB results  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                results_text += "<b>--- Personal Details ---</b>\n\n"
                for i, row in enumerate(data["results"], 1):
                    results_text += f"<b>--- Record {i} ---</b>\n"
                    results_text += duckdb_service.format_result(row) + "\n\n"
            elif search_type != "username":
                results_text = f"🔍 <b>Query:</b> <code>{query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n<b>--- Personal Details ---</b>\n❌ No data found in database.\n\n"
            else:
                results_text = f"🔍 <b>Query:</b> <code>{query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                
            # Format OSINT Results
            if search_type == "email":
                results_text += "<b>--- OSINT Linked Sites ---</b>\n"
                if osint_sites:
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected accounts!</b>\n"
                    for site in osint_sites:
                        results_text += f"🟢 {site}\n"
                else:
                    results_text += "❌ No linked accounts found.\n"
            elif search_type == "username":
                results_text += "<b>--- OSINT Linked Sites ---</b>\n"
                if osint_sites:
                    results_text += f"🔗 <b>Found {len(osint_sites)} connected accounts!</b>\n"
                    for item in osint_sites:
                        results_text += f"🟢 <a href='{item['url']}'>{item['site']}</a>\n"
                else:
                    results_text += "❌ No linked accounts found.\n"
                    
            mock_result = results_text.strip()
            
        except asyncio.TimeoutError:
            logger.error(f"Search timed out for {query}")
            is_success = False
            mock_result = "Search Failed: Request timed out. The database is too large and requires an index, or Hugging Face is slow."
        except Exception as e:
            logger.error(f"Search error: {e}")
            is_success = False
            mock_result = f"Search Failed: {str(e)}"

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
