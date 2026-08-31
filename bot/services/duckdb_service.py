import os
import threading
from concurrent.futures import ThreadPoolExecutor
import duckdb

# ── Config ──────────────────────────────────────────────────────────────────
HF_INDEX_BASE = os.environ.get(
    "ICMR_HF_INDEX_BASE",
    "hf://datasets/WipeX00/scrappeddata"
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
    
    # 🚀 Since we are now using MotherDuck, we don't need to batch the queries!
    # MotherDuck's 64GB+ cloud servers can easily handle searching all 120 files at the exact same time without freezing!
    sql = f"SELECT * FROM read_parquet('{INDDATA_INDEX}') WHERE {query_field} = '{v}' LIMIT {limit}"
    
    try:
        rows = cursor.execute(sql).fetchall()
        cols = [d[0] for d in cursor.description]
        raw_results = [dict(zip(cols, r)) for r in rows]
        
        mapped_results = []
        for row in raw_results:
            mapped_results.append({
                "name": row.get("name"),
                "fathersName": row.get("fname"),
                "phoneNumber": row.get("mobile"),
                "otherNumber": row.get("alt"),
                "address": row.get("address"),
                "state": row.get("circle"),
                "Email": row.get("email"),
                "source": "Inddata (1.7B)"
            })
        return mapped_results
    except Exception as e:
        print(f"Inddata search error: {e}")
        return []

def run_sync_search(search_type: str, query: str, limit: int = 10) -> dict:
    q = query.strip()
    if search_type == "phone":
        main_data = _run_field_search("phoneNumber", q, "exact", limit)
        tc_res = _run_truecaller_search("phoneNumber", q, limit)
        
        # Calculate remaining limit
        rem_limit = limit - len(main_data["results"])
        ind_res = []
        if rem_limit > 0:
            ind_res = _run_inddata_search("phoneNumber", q, rem_limit)
        
        # Enrich main_data with Truecaller info if available
        if tc_res and main_data["results"]:
            tc_row = tc_res[0]
            for r in main_data["results"]:
                r["Email"] = tc_row.get("Email")
                r["Carrier"] = tc_row.get("Carrier")
                r["Gender"] = tc_row.get("Gender")
                r["Truecaller_Name"] = tc_row.get("Name")
        elif not main_data["results"] and tc_res:
            main_data["results"] = tc_res
        
        # Append Inddata results
        if ind_res:
            main_data["results"].extend(ind_res)
            
        # Update total count
        main_data["count"] = len(main_data["results"])
            
        return main_data
        
    elif search_type == "aadhar":
        return _run_field_search("aadharNumber", q, "exact", limit)
        
    elif search_type == "email":
        tc_res = _run_truecaller_search("email", q, limit)
        main_data = {"count": 0, "results": []}
        
        if tc_res:
            phone = tc_res[0].get("Number")
            if phone:
                main_data = _run_field_search("phoneNumber", phone, "exact", limit)
                tc_row = tc_res[0]
                if main_data["results"]:
                    for r in main_data["results"]:
                        r["Email"] = tc_row.get("Email")
                        r["Carrier"] = tc_row.get("Carrier")
                        r["Gender"] = tc_row.get("Gender")
                        r["Truecaller_Name"] = tc_row.get("Name")
                else:
                    main_data["results"] = tc_res
            else:
                main_data["results"] = tc_res
                
        # Append Inddata results for email
        rem_limit = limit - len(main_data["results"])
        if rem_limit > 0:
            ind_res = _run_inddata_search("email", q, rem_limit)
            if ind_res:
                main_data["results"].extend(ind_res[:rem_limit])
            
        main_data["count"] = len(main_data["results"])
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
    "name": "Name",
    "fathersName": "Father's Name",
    "phoneNumber": "Phone Number",
    "aadharNumber": "Aadhaar Number",
    "otherNumber": "Other Number",
    "address": "Address",
    "district": "District",
    "pincode": "Pincode",
    "state": "State",
    "town": "Town",
    "source": "Source",
    "Email": "Email",
    "Carrier": "Carrier",
    "Gender": "Gender",
    "Truecaller_Name": "Truecaller Name",
    "Name": "Name",
    "Number": "Number"
}

def format_result(row: dict) -> str:
    """Format a single result record as readable text for Telegram."""
    lines = []
    
    fields_to_use = SEARCH_FIELDS + ["Email", "Carrier", "Gender", "Truecaller_Name", "Name", "Number"]
    
    for field in fields_to_use:
        val = row.get(field, "")
        if val:
            emoji = FIELD_EMOJIS.get(field, "🔹")
            label = FIELD_LABELS.get(field, field.capitalize())
            lines.append(f"{emoji} <b>{label}:</b> {str(val).strip()}")
    
    cn = row.get("connected_numbers", [])
    if cn:
        nums = ", ".join(f"<code>{c['value']}</code>" for c in cn)
        lines.append(f"🔗 <b>Connected Numbers:</b> {nums}")
        
    return "\n".join(lines)
