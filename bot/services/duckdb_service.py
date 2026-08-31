import os
import threading
from concurrent.futures import ThreadPoolExecutor
import duckdb

# ── Config ──────────────────────────────────────────────────────────────────
HF_INDEX_BASE = os.environ.get(
    "ICMR_HF_INDEX_BASE",
    "hf://datasets/WipeX00/scrappeddata"
).rstrip("/")
PARALLELISM = int(os.environ.get("ICMR_PARALLEL", "1")) # Reduced to 1 for absolute safety on 512MB RAM!
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
    "hf://datasets/eKaiva/ind_data_final"
).rstrip("/")
INDDATA_INDEX = f"{INDDATA_HF_BASE}/*.parquet" # Reads all 120 perfectly chunked files!

# ── DuckDB Global Connection ──────────────────────────────────────────────────
_global_conn = None
_conn_lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=PARALLELISM, thread_name_prefix="duck")

def _idx_ready(kind: str) -> bool:
    return kind in REMOTE_INDEXES

def _get_conn() -> duckdb.DuckDBPyConnection:
    global _global_conn
    if _global_conn is not None:
        return _global_conn
        
    with _conn_lock:
        if _global_conn is not None:
            return _global_conn
            
        con = duckdb.connect()
        import tempfile
        tmp_dir = tempfile.gettempdir().replace('\\', '/')
        con.execute(f"SET home_directory='{tmp_dir}'")
        con.execute(f"SET extension_directory='{tmp_dir}/duckdb_extensions'")
        con.execute("INSTALL parquet; LOAD parquet;")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        
        # Enable HTTP metadata caching to drastically improve remote query speed
        con.execute("SET enable_http_metadata_cache=true;")
        # Disabled object cache because caching 100GB parquet file metadata causes OOM on 512MB Render instances
        con.execute("SET enable_object_cache=false;")
        
        # Restrict memory strictly for Render Free Tier (512MB max total for python + duckdb)
        con.execute("SET memory_limit='100MB';")
        con.execute("SET preserve_insertion_order=false;")
        
        hf_token = os.environ.get("HF_TOKEN", "")
        if hf_token:
            try:
                con.execute(f"CREATE SECRET (TYPE HUGGINGFACE, TOKEN '{hf_token}');")
            except Exception as e:
                print(f"Could not set HF secret: {e}")
        
        con.execute(f"SET threads = {THREADS_PER_CONN}")
        _global_conn = con
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
    rows = con.execute(sql, [value]).fetchall()
    cols = [d[0] for d in con.description]
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
    try:
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        print(f"Truecaller search error: {e}")
        return []

def _run_inddata_search(field: str, value: str, limit: int = 10) -> list[dict]:
    if field == "phoneNumber":
        query_field = "mobile"
    elif field == "email":
        query_field = "email"
    else:
        return []
    
    v = str(value).replace("'", "''")
    # Columns in parquet: mobile, name, fname, address, alt, circle, id, email
    sql = f"SELECT * FROM read_parquet('{INDDATA_INDEX}') WHERE {query_field} = '{v}' LIMIT {limit}"
    
    con = _get_conn()
    try:
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        raw_results = [dict(zip(cols, r)) for r in rows]
        
        # Map to standard schema
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
                "source": "Inddata (1B)"
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
        
        # Query the newly chunked 120-file Inddata database!
        ind_res = _run_inddata_search("phoneNumber", q, limit)
        
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
        # Query the newly chunked 120-file Inddata database!
        ind_res = _run_inddata_search("email", q, limit)
        
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
        if ind_res:
            main_data["results"].extend(ind_res)
            
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
