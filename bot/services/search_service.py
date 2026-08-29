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
            data = await asyncio.wait_for(data_future, timeout=300.0)
            duration = round(time.time() - start_time, 2)
            is_success = bool(data.get("count", 0))
            
            if is_success:
                results_text = f"🔍 <b>Query:</b> <code>{query}</code>  |  <b>Found:</b> {data['count']} results  |  ⏱️ <b>Time:</b> {duration}s\n\n"
                for i, row in enumerate(data["results"], 1):
                    results_text += f"<b>--- Record {i} ---</b>\n"
                    results_text += duckdb_service.format_result(row) + "\n\n"
                mock_result = results_text
            else:
                mock_result = f"🔍 <b>Query:</b> <code>{query}</code>  |  ⏱️ <b>Time:</b> {duration}s\n❌ <b>No data found.</b>"
                
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
