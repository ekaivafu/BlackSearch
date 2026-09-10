import os
import threading
from concurrent.futures import ThreadPoolExecutor
import duckdb

# ── Config ──────────────────────────────────────────────────────────────────
HF_INDEX_BASE = os.environ.get(
    "ICMR_HF_INDEX_BASE",
    "hf://datasets/eKaiva/scrappeddataset"
).rstrip("/")
PARALLELISM = int(os.environ.get("ICMR_PARALLEL", "15")) # 🚀 Increased to 15 because MotherDuck is handling the load!
THREADS_PER_CONN = int(os.environ.get("ICMR_THREADS_PER_CONN", "2"))
DUPLICATE_CAP = 2

SEARCH_FIELDS = [
    "name", "fathersName", "phoneNumber", "aadharNumber", "otherNumber",
    "address", "district", "pincode", "state", "town", "source",
]
NUMBER_FIELDS = ["phoneNumber", "otherNumber", "phone"]

REMOTE_INDEXES = {
    "phone": f"{HF_INDEX_BASE}/idx_phone.*.parquet",
    "aadhar": f"{HF_INDEX_BASE}/idx_aadhar.*.parquet"
}

TC_INDEX_BASE = os.environ.get(
    "TRUECALLER_HF_INDEX_BASE",
    "hf://datasets/eKaiva/tirucaller"
).rstrip("/")

TRUECALLER_INDEXES = {
    "phone": f"{TC_INDEX_BASE}/idx_phone.parquet",
    "email": f"{TC_INDEX_BASE}/idx_email.parquet"
}

INDDATA_HF_BASE = os.environ.get(
    "INDDATA_HF_INDEX_BASE",
    "hf://datasets/eKaiva/ind_data_finalbot"
).rstrip("/")
INDDATA_INDEX = f"{INDDATA_HF_BASE}/*.parquet" # Reads all 120 perfectly chunked files!

# ── DuckDB Global Connection ──────────────────────────────────────────────────
_global_conn = None
_conn_lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=PARALLELISM, thread_name_prefix="duck")

def _idx_ready(kind: str) -> bool:
    return kind in REMOTE_INDEXES

def _get_conn():
    global _global_conn
    if _global_conn is not None:
        return _global_conn
        
    with _conn_lock:
        if _global_conn is not None:
            return _global_conn
            
        md_token = os.environ.get("MOTHERDUCK_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6InNhLWQ3ZjdiODY5LTcyYTAtNGMwZS1hNmY3LTZjYjlkZjA4MWU3N0BzYS5tb3RoZXJkdWNrLmNvbSIsIm1kUmVnaW9uIjoiYXdzLWFwLW5vcnRoZWFzdC0xIiwic2Vzc2lvbiI6InNhLWQ3ZjdiODY5LTcyYTAtNGMwZS1hNmY3LTZjYjlkZjA4MWU3Ny5zYS5tb3RoZXJkdWNrLmNvbSIsInBhdCI6IjZWV1lZV05DcUtSRzlnVGMtelVMYlNoandvX2s5SmcwdTRmRXNQMFB5V2MiLCJ1c2VySWQiOiJmMDNjZGM1ZC01ZmYwLTRlYTItOTc5MS1kNjk2MmE3NDczOWEiLCJpc3MiOiJtZF9wYXQiLCJyZWFkT25seSI6ZmFsc2UsInRva2VuVHlwZSI6InJlYWRfd3JpdGUiLCJpYXQiOjE3ODgxNzg5MTR9.a8bAHSdpgv5kZfSp1219_RWRUhzgyHrGQJ6XQQdK0mg")
        
        try:
            print("🚀 Connecting to MotherDuck Cloud...")
            con = duckdb.connect(f"md:?motherduck_token={md_token}")
            
            hf_token = os.environ.get("HF_TOKEN", "")
            if hf_token:
                try:
                    con.execute(f"CREATE OR REPLACE SECRET hf_secret (TYPE HUGGINGFACE, TOKEN '{hf_token}');")
                except Exception as e:
                    print(f"Could not set HF secret in MD: {e}")
                    
            _global_conn = con
            
            # 🚀 Turbo Settings for Remote Parquet over HTTP
            try:
                con.execute("SET parquet_metadata_cache = true;")
                con.execute("SET enable_http_metadata_cache = true;")
                con.execute("SET prefetch_all_parquet_files = true;")
                con.execute("SET preserve_insertion_order = false;")
                print("⚡ DuckDB / MotherDuck HTTP & Parquet turbo flags active!")
            except Exception as pe:
                print(f"Notice on DuckDB pragma flags: {pe}")

            print("✅ MotherDuck successfully connected!")
        except Exception as e:
            print(f"❌ FATAL ERROR: MotherDuck connection failed: {e}.")
            print("We are completely disabling local DuckDB fallback to protect Render's 512MB RAM from crashing on the 93GB dataset.")
            raise Exception("MotherDuck cloud is offline. Refusing to run locally to prevent server crash.")
            
    return _global_conn

# ── Dedup & Connected Records ───────────────────────────────────────────────
def _person_key(row: dict) -> tuple:
    ph = (row.get("phoneNumber") or "").strip()
    ad = (row.get("aadharNumber") or "").strip()
    if ph or ad:
        return (ph, ad)
    return (row.get("name") or "").strip(), (row.get("fathersName") or "").strip()

def _connected_numbers(row: dict) -> list[dict]:
    connected, seen = [], set()
    for field in NUMBER_FIELDS:
        raw = row.get(field)
        if raw is None:
            continue
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        connected.append({"field": field, "value": value})
    return connected

def _cap_duplicates(rows: list[dict]) -> list[dict]:
    seen: dict[tuple, int] = {}
    out = []
    for r in rows:
        k = _person_key(r)
        n = seen.get(k, 0)
        if n < DUPLICATE_CAP:
            seen[k] = n + 1
            record = dict(r)
            record["connected_numbers"] = _connected_numbers(record)
            out.append(record)
    return out

# ── Search Logic ────────────────────────────────────────────────────────────
def _run_field_search(field: str, value: str, mode: str, limit: int = 10) -> dict:
    if field not in SEARCH_FIELDS:
        raise ValueError(f"Unknown field: {field}")
    v = str(value).replace("'", "''")

    if mode == "exact":
        if field == "phoneNumber" and _idx_ready("phone"):
            dataset_path = REMOTE_INDEXES["phone"]
            cols_str = ", ".join(SEARCH_FIELDS)
        elif field == "aadharNumber" and _idx_ready("aadhar"):
            dataset_path = REMOTE_INDEXES["aadhar"]
            cols_str = ", ".join(SEARCH_FIELDS)
        else:
            return {"field": field, "value": value, "mode": mode, "count": 0, "results": []}
        
        sql = f"SELECT {cols_str} FROM read_parquet('{dataset_path}') WHERE {field} = ? LIMIT {limit * DUPLICATE_CAP + 20}"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    con = _get_conn()
    cursor = con.cursor()
    rows = cursor.execute(sql, [value]).fetchall()
    cols = [d[0] for d in cursor.description]
    results = _cap_duplicates([dict(zip(cols, r)) for r in rows])[:limit]
    return {"field": field, "value": value, "mode": mode, "count": len(results), "results": results}

def _run_truecaller_search(field: str, value: str, limit: int = 10) -> list[dict]:
    if field == "phoneNumber":
        dataset_path = TRUECALLER_INDEXES["phone"]
        query_field = "Number"
    elif field == "email":
        dataset_path = TRUECALLER_INDEXES["email"]
        query_field = "Email"
    else:
        return []
    
    v = str(value).replace("'", "''")
    cols = "Number, Name, Address, Email, Gender, Carrier"
    sql = f"SELECT {cols} FROM read_parquet('{dataset_path}') WHERE {query_field} = '{v}' LIMIT {limit}"
    
    con = _get_conn()
    cursor = con.cursor()
    try:
        rows = cursor.execute(sql).fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        print(f"Truecaller search error: {e}")
        return []

# ── Chunk List Caching ────────────────────────────────────────────────────────
_inddata_chunks = []

def _get_inddata_chunks():
    global _inddata_chunks
    if not _inddata_chunks:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=os.environ.get("HF_TOKEN", ""))
            files = api.list_repo_files("eKaiva/ind_data_finalbot", repo_type="dataset")
            _inddata_chunks = [f for f in files if f.startswith("chunk_") and f.endswith(".parquet")]
            _inddata_chunks.sort()
        except Exception as e:
            print(f"HF API Chunk fetch failed: {e}. Falling back to 120 chunks.")
            _inddata_chunks = [f"chunk_{i:04d}.parquet" for i in range(1, 121)]
    return _inddata_chunks

def _run_inddata_search(field: str, value: str, limit: int = 10) -> list[dict]:
    if field == "phoneNumber":
        query_field = "mobile"
    elif field == "email":
        query_field = "email"
    else:
        return []
    
    v = str(value).replace("'", "''")
    con = _get_conn()
    cursor = con.cursor()
    
    # 🚀 Native MotherDuck 104M Email Vault (< 50ms)
    sql = f"SELECT mobile, email, name, circle FROM my_db.ind_emails WHERE {query_field} = '{v}' LIMIT {limit}"
    
    try:
        rows = cursor.execute(sql).fetchall()
        cols = [d[0] for d in cursor.description]
        raw_results = [dict(zip(cols, r)) for r in rows]
        
        mapped_results = []
        for row in raw_results:
            mapped_results.append({
                "name": row.get("name"),
                "phoneNumber": row.get("mobile"),
                "state": row.get("circle"),
                "Email": row.get("email"),
                "source": "Inddata Email Vault (104M)"
            })
        return mapped_results
    except Exception as e:
        print(f"Inddata search error: {e}")
        return []

# ── High-Speed In-Memory Search Cache (TTL: 2 Hours) ────────────────────────
import time

_search_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 7200  # 2 hours
MAX_CACHE_SIZE = 1000

def _get_cached_result(cache_key: str) -> dict | None:
    with _cache_lock:
        if cache_key in _search_cache:
            ts, res = _search_cache[cache_key]
            if time.time() - ts < CACHE_TTL:
                return {
                    "field": res.get("field"),
                    "value": res.get("value"),
                    "mode": res.get("mode"),
                    "count": res.get("count", 0),
                    "results": [dict(r) for r in res.get("results", [])]
                }
            else:
                del _search_cache[cache_key]
    return None

def _set_cached_result(cache_key: str, res: dict):
    with _cache_lock:
        now = time.time()
        if len(_search_cache) > MAX_CACHE_SIZE:
            expired = [k for k, (ts, _) in _search_cache.items() if now - ts > CACHE_TTL]
            for k in expired:
                del _search_cache[k]
            if len(_search_cache) > MAX_CACHE_SIZE:
                sorted_keys = sorted(_search_cache.keys(), key=lambda k: _search_cache[k][0])
                for k in sorted_keys[:100]:
                    del _search_cache[k]
        _search_cache[cache_key] = (
            now,
            {
                "field": res.get("field"),
                "value": res.get("value"),
                "mode": res.get("mode"),
                "count": res.get("count", 0),
                "results": [dict(r) for r in res.get("results", [])]
            }
        )

def run_sync_search(search_type: str, query: str, limit: int = 10) -> dict:
    q = query.strip()
    cache_key = f"{search_type}:{q.lower()}:{limit}"
    cached = _get_cached_result(cache_key)
    if cached is not None:
        return cached

    if search_type == "phone":
        # 🚀 Execute ICMR, Truecaller, and native 104M Email Vault simultaneously in PARALLEL (<1.5s total)
        fut_main = pool.submit(_run_field_search, "phoneNumber", q, "exact", limit)
        fut_tc   = pool.submit(_run_truecaller_search, "phoneNumber", q, limit)
        fut_ind  = pool.submit(_run_inddata_search, "phoneNumber", q, limit)

        try:
            main_data = fut_main.result()
        except Exception as e:
            print(f"Main field search error: {e}")
            main_data = {"count": 0, "results": []}

        try:
            tc_res = fut_tc.result()
        except Exception as e:
            print(f"Truecaller search error: {e}")
            tc_res = []

        try:
            ind_res = fut_ind.result()
        except Exception as e:
            print(f"Inddata search error: {e}")
            ind_res = []

        # 1. Enrich main_data with Truecaller info
        if tc_res and main_data.get("results"):
            tc_row = tc_res[0]
            for r in main_data["results"]:
                if not r.get("Email") and tc_row.get("Email"):
                    r["Email"] = tc_row.get("Email")
                r["Carrier"] = tc_row.get("Carrier")
                r["Gender"] = tc_row.get("Gender")
                r["Truecaller_Name"] = tc_row.get("Name")
        elif not main_data.get("results") and tc_res:
            main_data["results"] = tc_res

        # 2. Enrich with verified Email from the 104M Inddata vault
        if ind_res:
            ind_email = ind_res[0].get("Email")
            if ind_email:
                if main_data.get("results"):
                    for r in main_data["results"]:
                        if not r.get("Email"):
                            r["Email"] = ind_email
                else:
                    main_data["results"] = ind_res
            elif not main_data.get("results"):
                main_data["results"] = ind_res

        main_data["count"] = len(main_data.get("results", []))
        _set_cached_result(cache_key, main_data)
        return main_data

    elif search_type == "aadhar":
        res = _run_field_search("aadharNumber", q, "exact", limit)
        _set_cached_result(cache_key, res)
        return res

    elif search_type == "email":
        # ⚡ Query Truecaller and 104M Inddata Email Vault concurrently (<100ms)
        fut_tc  = pool.submit(_run_truecaller_search, "email", q, limit)
        fut_ind = pool.submit(_run_inddata_search, "email", q, limit)

        try:
            tc_res = fut_tc.result()
        except Exception as e:
            print(f"Truecaller email search error: {e}")
            tc_res = []

        try:
            ind_res = fut_ind.result()
        except Exception as e:
            print(f"Inddata email search error: {e}")
            ind_res = []

        main_data = {"count": 0, "results": []}

        # Resolve phone number from either Truecaller or 104M Inddata
        phone = None
        if tc_res and tc_res[0].get("Number"):
            phone = tc_res[0].get("Number")
        elif ind_res and ind_res[0].get("phoneNumber"):
            phone = ind_res[0].get("phoneNumber")

        if phone:
            main_data = _run_field_search("phoneNumber", phone, "exact", limit)
            if main_data.get("results"):
                for r in main_data["results"]:
                    r["Email"] = q
                    if tc_res:
                        r["Carrier"] = tc_res[0].get("Carrier")
                        r["Gender"] = tc_res[0].get("Gender")
                        r["Truecaller_Name"] = tc_res[0].get("Name")
            else:
                main_data["results"] = ind_res or tc_res
        else:
            main_data["results"] = ind_res or tc_res

        main_data["count"] = len(main_data.get("results", []))
        _set_cached_result(cache_key, main_data)
        return main_data

    return {"count": 0, "results": []}

FIELD_EMOJIS = {
    "name": "👤",
    "fathersName": "👨‍👦",
    "phoneNumber": "📱",
    "aadharNumber": "🪪",
    "otherNumber": "📞",
    "address": "🏠",
    "district": "🏢",
    "pincode": "📍",
    "state": "🗺️",
    "town": "🏙️",
    "source": "📂",
    "Email": "📧",
    "Carrier": "📡",
    "Gender": "🚻",
    "Truecaller_Name": "📛",
    "Name": "📛",
    "Number": "📱"
}

FIELD_LABELS = {
    "name": "Full Name",
    "fathersName": "Father's Name",
    "phoneNumber": "Phone Number",
    "aadharNumber": "Aadhaar Number",
    "otherNumber": "Alternate Number",
    "address": "Address",
    "district": "District",
    "pincode": "Pincode",
    "state": "State",
    "town": "Town / City",
    "Email": "Email Address",
    "Carrier": "Telecom Operator",
    "Gender": "Gender",
    "Truecaller_Name": "Alternative Name",
    "Name": "Full Name",
    "Number": "Phone Number"
}

import html

def format_result(row: dict) -> str:
    """Format a single result record as readable text for Telegram."""
    lines = []
    seen_labels = set()
    
    fields_to_use = SEARCH_FIELDS + ["Email", "Carrier", "Gender", "Truecaller_Name", "Name", "Number"]
    
    for field in fields_to_use:
        if field.lower() in ["source", "src"]:
            continue  # Never expose internal database or dataset names to users
        val = row.get(field, "")
        if val:
            safe_val = html.escape(str(val).strip())
            if not safe_val:
                continue
            emoji = FIELD_EMOJIS.get(field, "🔹")
            label = FIELD_LABELS.get(field, field.capitalize())
            # Avoid duplicate rows with same label (e.g. name vs Name, phoneNumber vs Number)
            norm_label = label.strip().lower()
            if norm_label in seen_labels:
                continue
            seen_labels.add(norm_label)
            lines.append(f"{emoji} <b>{label}:</b> {safe_val}")
    
    cn = row.get("connected_numbers", [])
    if cn:
        nums = ", ".join(f"<code>{html.escape(str(c['value']))}</code>" for c in cn)
        lines.append(f"🔗 <b>Connected Numbers:</b> {nums}")
        
    return "\n".join(lines)
