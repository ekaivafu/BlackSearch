import os
import re
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
                con.execute("SET enable_object_cache = true;")
                con.execute("SET http_keep_alive = true;")
                con.execute("SET http_timeout = 10000;")
                con.execute("SET http_retries = 3;")
                print("⚡ DuckDB / MotherDuck HTTP & Parquet turbo flags active!")
            except Exception as pe:
                print(f"Notice on DuckDB pragma flags: {pe}")

            print("✅ MotherDuck successfully connected!")
        except Exception as e:
            print(f"❌ FATAL ERROR: MotherDuck connection failed: {e}.")
            print("We are completely disabling local DuckDB fallback to protect Render's 512MB RAM from crashing on the 93GB dataset.")
            raise Exception("MotherDuck cloud is offline. Refusing to run locally to prevent server crash.")
            
    return _global_conn

def warmup_cache():
    """Background warm-up: touches chunks so footers and TCP connections are warm in DuckDB memory."""
    try:
        con = _get_conn()
        cur = con.cursor()
        print("🔥 Pre-warming Parquet chunk footers in background...")
        for i in range(7):
            try:
                url = f"{HF_INDEX_BASE}/idx_phone.{i}.parquet"
                cur.execute(f"SELECT phoneNumber FROM read_parquet('{url}') LIMIT 1").fetchall()
            except Exception as e:
                print(f"Warmup chunk {i} notice: {e}")
        print("🚀 Parquet chunk footers fully warmed in memory!")
    except Exception as e:
        print(f"Warmup notice: {e}")

def start_background_warmup():
    """Start warmup in a detached daemon thread so bot startup is never blocked."""
    t = threading.Thread(target=warmup_cache, name="duck-warmup", daemon=True)
    t.start()

# ── Dedup, Sanitization & Connected Records ───────────────────────────────────
def is_invalid_val(val) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return not s or s.upper() in (
        "NA", "N/A", "NONE", "NULL", "0", "UNDEFINED", "N.A", "N.A.", "UNKNOWN", "NOT AVAILABLE"
    )

def clean_address(raw_addr: str) -> str:
    if is_invalid_val(raw_addr):
        return ""
    parts = [p.strip() for p in re.split(r'[!|]', str(raw_addr)) if p.strip()]
    cleaned_parts = []
    seen = set()
    for part in parts:
        part_clean = part.strip()
        if is_invalid_val(part_clean):
            continue
        norm = part_clean.lower()
        if norm in seen:
            continue
        seen.add(norm)
        cleaned_parts.append(part_clean)
        
    if not cleaned_parts:
        return ""
    if len(cleaned_parts) > 1 and re.match(r'^\d{6}$', cleaned_parts[-1]):
        pincode = cleaned_parts.pop()
        return ", ".join(cleaned_parts) + f" - {pincode}"
    return ", ".join(cleaned_parts)

ADDRESS_ABBREVIATIONS = {
    r'\bsec\b': 'sector',
    r'\bsect\b': 'sector',
    r'\bst\b': 'street',
    r'\bstr\b': 'street',
    r'\brd\b': 'road',
    r'\bh\.?\s*no\.?\b': 'hno',
    r'\bhouse\s*no\.?\b': 'hno',
    r'\bflat\s*no\.?\b': 'flat',
    r'\bflt\b': 'flat',
    r'\bnagr\b': 'nagar',
    r'\bngr\b': 'nagar',
    r'\bclny\b': 'colony',
    r'\bcol\b': 'colony',
    r'\bextn?\b': 'extension',
}

ADDRESS_STOP_WORDS = {'na', 'null', 'none', 'near', 'opp', 'opposite', 'behind', 'dist', 'district', 'state', 'india', 'po'}

def normalize_address(addr: str) -> str:
    if not addr or is_invalid_val(addr):
        return ""
    text = re.sub(r'[!|,/\\-]', ' ', str(addr).lower())
    for pat, rep in ADDRESS_ABBREVIATIONS.items():
        text = re.sub(pat, rep, text)
    tokens = [w for w in text.split() if w not in ADDRESS_STOP_WORDS and len(w) > 1]
    return " ".join(tokens)

def extract_pincode(addr: str) -> str:
    if not addr or is_invalid_val(addr):
        return ""
    m = re.search(r'\b\d{6}\b', str(addr))
    return m.group(0) if m else ""

def extract_house_number(addr: str) -> str:
    if not addr or is_invalid_val(addr):
        return ""
    m = re.search(r'\b(?:house|hno|h|flat|plot|#)\.?\s*(?:no\.?)?\s*([a-z0-9\-\/]*\d+[a-z0-9\-\/]*)', str(addr), re.I)
    if m and m.group(1):
        val = m.group(1).strip().upper().strip('.-/ ')
        val = re.sub(r'^(?:HNO|H|NO)[\.\-\s]*', '', val)
        if re.search(r'\d', val):
            return val
    parts = [p.strip() for p in re.split(r'[!,]', str(addr)) if p.strip()]
    for p in parts[:2]:
        m2 = re.match(r'^(?:(?:house|hno|h|flat|plot)\.?\s*(?:no\.?)?\s*)?([a-z0-9\-\/]*\d+[a-z0-9\-\/]*)$', p, re.I)
        if m2:
            val = m2.group(1).strip().upper().strip('.-/ ')
            val = re.sub(r'^(?:HNO|H|NO)[\.\-\s]*', '', val)
            if re.search(r'\d', val) and not re.match(r'^\d{6}$', val):
                return val
    return ""

GENERIC_ADDRESS_WORDS = {
    "colony", "vihar", "nagar", "road", "gali", "street", "block", "sector", "phase",
    "enclave", "extension", "pur", "puri", "bazar", "bazaar", "delhi", "haryana", "uttar",
    "pradesh", "ghaziabad", "yamunanagar", "yamuna", "near", "opposite", "behind",
    "dist", "district", "city", "state", "post", "office", "so", "do", "wo", "hno", "house", "no"
}

def match_address_affinity(addr1: str, addr2: str) -> dict:
    """Advanced address affinity matcher handling spelling variations, abbreviations, house numbers and pincodes."""
    if not addr1 or not addr2 or is_invalid_val(addr1) or is_invalid_val(addr2):
        return {"matched": False, "score": 0.0, "reason": ""}

    pin1 = extract_pincode(addr1)
    pin2 = extract_pincode(addr2)

    hno1 = extract_house_number(addr1)
    hno2 = extract_house_number(addr2)

    norm1 = normalize_address(addr1)
    norm2 = normalize_address(addr2)

    tokens1 = set(norm1.split())
    tokens2 = set(norm2.split())

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    jaccard = len(intersection) / len(union) if union else 0.0

    specific_t1 = {t for t in tokens1 if t not in GENERIC_ADDRESS_WORDS and len(t) > 2 and not t.isdigit()}
    specific_t2 = {t for t in tokens2 if t not in GENERIC_ADDRESS_WORDS and len(t) > 2 and not t.isdigit()}
    spec_inter = specific_t1.intersection(specific_t2)

    same_pin = bool(pin1 and pin2 and pin1 == pin2)
    same_hno = bool(hno1 and hno2 and hno1 == hno2)

    if same_hno and (same_pin or bool(spec_inter)):
        colony_str = f" in {next(iter(spec_inter)).title()}" if spec_inter else ""
        return {
            "matched": True,
            "confidence": "HIGH",
            "score": 0.95,
            "reason": f"Same House ({hno1}){colony_str}"
        }
    elif bool(spec_inter) and same_pin:
        colony_name = next(iter(spec_inter)).title()
        return {
            "matched": True,
            "confidence": "MEDIUM",
            "score": 0.80,
            "reason": f"Same Locality ({colony_name}) & Pincode ({pin1})"
        }
    elif same_hno and jaccard >= 0.35:
        return {
            "matched": True,
            "confidence": "HIGH",
            "score": 0.85,
            "reason": f"Same House ({hno1})"
        }
    elif same_pin:
        return {
            "matched": False,
            "confidence": "LOW",
            "score": 0.30,
            "reason": f"Same Pincode Area ({pin1})"
        }
    else:
        # Extract location city/district if present
        parts = [p.strip() for p in str(addr2).split('!') if p.strip() and not is_invalid_val(p)]
        loc = parts[-2] if len(parts) >= 2 else (pin2 if pin2 else "")
        return {
            "matched": False,
            "confidence": "NONE",
            "score": jaccard,
            "reason": f"Current Loc: {loc.title()}" if loc else ""
        }

def _person_key(row: dict) -> tuple:
    ph = (row.get("phoneNumber") or "").strip()
    ad = (row.get("aadharNumber") or "").strip()
    if is_invalid_val(ad):
        ad = ""
    if ph or ad:
        return (ph, ad)
    return (row.get("name") or "").strip(), (row.get("fathersName") or "").strip()

def _connected_numbers(row: dict) -> list[dict]:
    connected, seen = [], set()
    for field in NUMBER_FIELDS:
        raw = row.get(field)
        if is_invalid_val(raw):
            continue
        value = str(raw).strip()
        digits = "".join(c for c in value if c.isdigit())
        clean_num = digits[-10:] if len(digits) >= 10 else digits
        if not clean_num or len(clean_num) < 10 or clean_num in seen:
            continue
        seen.add(clean_num)
        connected.append({"field": field, "value": clean_num})
    return connected

def _cap_duplicates(rows: list[dict]) -> list[dict]:
    # 1. First coalesce/merge rows belonging to the same phone so valid Aadhaar/otherNumber isn't lost
    by_phone: dict[str, dict] = {}
    other_rows: list[dict] = []
    
    for r in rows:
        ph_raw = str(r.get("phoneNumber") or "").strip()
        ph_digits = "".join(c for c in ph_raw if c.isdigit())
        ph_10 = ph_digits[-10:] if len(ph_digits) >= 10 else ""
        
        if ph_10:
            if ph_10 not in by_phone:
                by_phone[ph_10] = dict(r)
            else:
                existing = by_phone[ph_10]
                for k, v in r.items():
                    if k == "connected_numbers":
                        continue
                    if is_invalid_val(existing.get(k)) and not is_invalid_val(v):
                        existing[k] = v
                    elif k == "address" and not is_invalid_val(v):
                        if len(str(v)) > len(str(existing.get(k) or "")):
                            existing[k] = v
        else:
            other_rows.append(dict(r))
            
    merged_rows = list(by_phone.values()) + other_rows
    seen: dict[tuple, int] = {}
    out = []
    for record in merged_rows:
        k = _person_key(record)
        n = seen.get(k, 0)
        if n < DUPLICATE_CAP:
            seen[k] = n + 1
            record["connected_numbers"] = _connected_numbers(record)
            out.append(record)
    return out

# ── Partition Boundaries for HF Dataset Chunks (0 to 6) ──────────────────────
# The dataset is sorted contiguously across 7 parts.
# Routing directly to the matching chunk avoids scanning 97GB across 7 files over HTTP,
# dropping query latency from 27s to ~1.2-1.5s!
PHONE_CHUNKS = [
    (0, "", "7310463827"),
    (1, "7310463827", "8077394733"),
    (2, "8077394734", "8780538380"),
    (3, "8780538381", "9313336069"),
    (4, "9313336069", "9703053944"),
    (5, "9703053945", "9990701360"),
    (6, "9990701361", "9999999999"),
]

AADHAR_CHUNKS = [
    (0, "", "351104219878"),
    (1, "351104219878", "549490723501"),
    (2, "549490723501", "746293615015"),
    (3, "746293615015", "944410175347"),
    (4, "944410175348", "999999999999"),
]

def get_phone_chunks(phone: str) -> list[int]:
    digits = "".join(c for c in str(phone) if c.isdigit())
    p = digits[-10:] if len(digits) >= 10 else digits
    if len(p) != 10:
        return list(range(7))
    chunks = []
    for idx, low, high in PHONE_CHUNKS:
        if low and high:
            if low <= p <= high:
                chunks.append(idx)
        elif not low and high:
            if p <= high:
                chunks.append(idx)
        elif low and not high:
            if p >= low:
                chunks.append(idx)
    return chunks or list(range(7))

def get_aadhar_chunks(aadhar: str) -> list[int]:
    digits = "".join(c for c in str(aadhar) if c.isdigit())
    a = digits[-12:] if len(digits) >= 12 else digits
    if len(a) != 12:
        return list(range(5))
    chunks = []
    for idx, low, high in AADHAR_CHUNKS:
        if low and high:
            if low <= a <= high:
                chunks.append(idx)
        elif not low and high:
            if a <= high:
                chunks.append(idx)
        elif low and not high:
            if a >= low:
                chunks.append(idx)
    return chunks or list(range(5))

def _run_field_search(field: str, value: str, mode: str, limit: int = 10) -> dict:
    if field not in SEARCH_FIELDS:
        raise ValueError(f"Unknown field: {field}")

    if mode != "exact":
        raise ValueError(f"Unknown mode: {mode}")

    cols_str = ", ".join(SEARCH_FIELDS)
    safe_val = str(value).replace("'", "''").strip()

    if field == "phoneNumber" and _idx_ready("phone"):
        chunks = get_phone_chunks(value)
        if len(chunks) == 1:
            dataset_target = f"'{HF_INDEX_BASE}/idx_phone.{chunks[0]}.parquet'"
        elif len(chunks) < 7:
            paths = [f"'{HF_INDEX_BASE}/idx_phone.{c}.parquet'" for c in chunks]
            dataset_target = f"[{', '.join(paths)}]"
        else:
            dataset_target = f"'{REMOTE_INDEXES['phone']}'"

        digits = "".join(c for c in str(value) if c.isdigit())
        clean_10 = (digits[-10:] if len(digits) >= 10 else digits).replace("'", "''")
        if clean_10 and clean_10 != safe_val:
            where_clause = f"({field} = '{safe_val}' OR {field} = '{clean_10}')"
        else:
            where_clause = f"{field} = '{safe_val}'"

    elif field == "aadharNumber" and _idx_ready("aadhar"):
        chunks = get_aadhar_chunks(value)
        if len(chunks) == 1:
            dataset_target = f"'{HF_INDEX_BASE}/idx_aadhar.{chunks[0]}.parquet'"
        elif len(chunks) < 5:
            paths = [f"'{HF_INDEX_BASE}/idx_aadhar.{c}.parquet'" for c in chunks]
            dataset_target = f"[{', '.join(paths)}]"
        else:
            dataset_target = f"'{REMOTE_INDEXES['aadhar']}'"

        digits = "".join(c for c in str(value) if c.isdigit())
        clean_12 = (digits[-12:] if len(digits) >= 12 else digits).replace("'", "''")
        if clean_12 and clean_12 != safe_val:
            where_clause = f"({field} = '{safe_val}' OR {field} = '{clean_12}')"
        else:
            where_clause = f"{field} = '{safe_val}'"
    else:
        return {"field": field, "value": value, "mode": mode, "count": 0, "results": []}

    # 🚀 Inlined literal value enables DuckDB's optimizer to statically prune row groups at compile-time (<1.0s vs 34s)
    sql = f"SELECT {cols_str} FROM read_parquet({dataset_target}) WHERE {where_clause} LIMIT {limit * DUPLICATE_CAP + 20}"

    con = _get_conn()
    cursor = con.cursor()
    rows = cursor.execute(sql).fetchall()
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
    except Exception:
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

    elif search_type == "username":
        # 🚀 Correlate username against 104M Vault (e.g. john.doe@... -> mobile -> ICMR profile)
        con = _get_conn()
        cur = con.cursor()
        v = str(q).replace("'", "''").lower()
        sql = f"SELECT mobile, email, name, circle FROM my_db.ind_emails WHERE email LIKE '{v}@%' OR email LIKE '{v}.%@%' LIMIT 3"
        try:
            rows = cur.execute(sql).fetchall()
            cols = [d[0] for d in cur.description]
            db_results = [dict(zip(cols, r)) for r in rows]
        except Exception:
            db_results = []

        if db_results and db_results[0].get("mobile"):
            phone = str(db_results[0]["mobile"]).strip()
            icmr_data = _run_field_search("phoneNumber", phone, "exact", limit)
            if icmr_data.get("results"):
                for r in icmr_data["results"]:
                    r["Email"] = db_results[0].get("email")
                    r["source"] = "104M Email Vault + ICMR Record"
                icmr_data["count"] = len(icmr_data["results"])
                _set_cached_result(cache_key, icmr_data)
                return icmr_data

        mapped = []
        for r in db_results:
            mapped.append({
                "name": r.get("name"),
                "phoneNumber": r.get("mobile"),
                "state": r.get("circle"),
                "Email": r.get("email"),
                "source": "104M Email Vault"
            })
        main_data = {"field": "username", "value": q, "mode": "exact", "count": len(mapped), "results": mapped}
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
        if is_invalid_val(val):
            continue
            
        if field == "address":
            val = clean_address(val)
            if not val:
                continue
                
        safe_val = html.escape(str(val).strip())
        emoji = FIELD_EMOJIS.get(field, "🔹")
        label = FIELD_LABELS.get(field, field.capitalize())
        # Avoid duplicate rows with same label (e.g. name vs Name, phoneNumber vs Number)
        norm_label = label.strip().lower()
        if norm_label in seen_labels:
            continue
        seen_labels.add(norm_label)
        lines.append(f"{emoji} <b>{label}:</b> {safe_val}")
    
    cn = row.get("connected_numbers", [])
    valid_nums = []
    for c in cn:
        v = c.get("value")
        if not is_invalid_val(v):
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if len(digits) >= 10:
                valid_nums.append(digits[-10:])
    if valid_nums:
        unique_nums = list(dict.fromkeys(valid_nums))
        num_str = ", ".join(f"<code>{html.escape(n)}</code>" for n in unique_nums)
        lines.append(f"🔗 <b>Connected Numbers:</b> {num_str}")
        
    return "\n".join(lines)


def run_deep_phone_search(phone: str, limit: int = 10) -> dict:
    """Executes multi-hop OSINT pivots: primary profile -> Aadhaar reverse SIMs -> alt contact -> family members."""
    digits = "".join(c for c in str(phone) if c.isdigit())
    clean_phone = digits[-10:] if len(digits) >= 10 else digits
    cache_key = f"deep:phone:{clean_phone}"
    cached = _get_cached_result(cache_key)
    if cached is not None:
        return cached

    # Hop 0: Target Profile
    main_res = run_sync_search("phone", clean_phone, limit=limit)
    records = main_res.get("results", [])
    if not records:
        empty_res = {"count": 0, "results": [], "deep_data": None}
        return empty_res

    target = records[0]
    aadhar = target.get("aadharNumber") if not is_invalid_val(target.get("aadharNumber")) else None
    father = target.get("fathersName") if not is_invalid_val(target.get("fathersName")) else None
    target_addr = str(target.get("address") or "").strip()

    all_target_addresses = [target_addr] if target_addr and not is_invalid_val(target_addr) else []
    all_alt_numbers = set()

    if target.get("otherNumber") and not is_invalid_val(target.get("otherNumber")):
        o = "".join(c for c in str(target.get("otherNumber")) if c.isdigit())[-10:]
        if o and o != clean_phone:
            all_alt_numbers.add((o, "Primary Profile Application"))

    futures = {}
    if aadhar:
        futures["aadhar_sims"] = pool.submit(run_sync_search, "aadhar", str(aadhar).strip(), 10)

    # Resolve linked sims first to harvest all secondary addresses & alternate numbers
    linked_sims = []
    seen_sims = {clean_phone}
    if "aadhar_sims" in futures:
        try:
            sim_res = futures["aadhar_sims"].result(timeout=15.0)
            for r in (sim_res.get("results") or []):
                p = "".join(c for c in str(r.get("phoneNumber") or "") if c.isdigit())[-10:]
                if p and p not in seen_sims and not is_invalid_val(p):
                    seen_sims.add(p)
                    linked_sims.append(r)
                r_addr = str(r.get("address") or "").strip()
                if r_addr and not is_invalid_val(r_addr) and r_addr not in all_target_addresses:
                    all_target_addresses.append(r_addr)
                if r.get("otherNumber") and not is_invalid_val(r.get("otherNumber")):
                    ro = "".join(c for c in str(r.get("otherNumber")) if c.isdigit())[-10:]
                    if ro and ro != clean_phone and ro != p:
                        all_alt_numbers.add((ro, f"Secondary SIM ({p}) Application"))
        except Exception as e:
            print(f"Aadhaar SIMs lookup error: {e}")

    # Parallel Hop: Family Lineage & Household Address Matching
    achunks = get_aadhar_chunks(str(aadhar)) if aadhar else [0]
    achunk = achunks[0] if achunks else 0
    alt_phone_set = {an for an, _ in all_alt_numbers}

    parallel_futures = {}

    # Parallel A: Family Lineage Search (Shared Father, Mother/Spouse W/O, or Target Alternate Number)
    if aadhar and father and len(father) > 4:
        def _find_family():
            try:
                con = _get_conn()
                cur = con.cursor()
                safe_father = str(father).replace("'", "''")
                sql = f"SELECT name, fathersName, phoneNumber, aadharNumber, otherNumber, address FROM read_parquet('{HF_INDEX_BASE}/idx_aadhar.{achunk}.parquet') WHERE fathersName = '{safe_father}' LIMIT 40"
                rows = cur.execute(sql).fetchall()
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in rows]
            except Exception as e:
                print(f"Family lookup error: {e}")
                return []
        parallel_futures["family"] = pool.submit(_find_family)

    # Parallel B: Household Co-habitants Search by Address / Pincode
    target_pincodes = [extract_pincode(addr) for addr in all_target_addresses if extract_pincode(addr)]
    primary_pin = target_pincodes[0] if target_pincodes else ""

    if primary_pin:
        def _find_household():
            try:
                con = _get_conn()
                cur = con.cursor()
                sql = f"SELECT name, fathersName, phoneNumber, aadharNumber, otherNumber, address FROM read_parquet('{HF_INDEX_BASE}/idx_aadhar.{achunk}.parquet') WHERE address LIKE '%{primary_pin}%' LIMIT 40"
                rows = cur.execute(sql).fetchall()
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in rows]
            except Exception as e:
                print(f"Household lookup error: {e}")
                return []
        parallel_futures["household"] = pool.submit(_find_household)

    # Parallel C: Alternate Contacts Lookup
    alt_lookups = {}
    for alt_n, orig in list(all_alt_numbers)[:4]:
        alt_lookups[alt_n] = (pool.submit(run_sync_search, "phone", alt_n, 1), orig)

    # Await parallel results
    p_results = {}
    for k, f in parallel_futures.items():
        try:
            p_results[k] = f.result(timeout=28.0)
        except Exception as e:
            print(f"Deep pivot {k} error or timeout: {e}")
            p_results[k] = []

    # 1. Process Household Co-habitants
    household_members = []
    seen_house = set()
    raw_house = p_results.get("household") or []

    for r in raw_house:
        cp = "".join(c for c in str(r.get("phoneNumber") or "") if c.isdigit())[-10:]
        ca = "".join(c for c in str(r.get("aadharNumber") or "") if c.isdigit())[-12:]
        if cp in seen_sims or (aadhar and ca and ca == str(aadhar)[-12:]):
            continue
        k = ca if ca else (cp if cp else (r.get("name") or "").strip().lower())
        if k in seen_house:
            continue

        r_addr = str(r.get("address") or "").strip()
        best_affinity = None
        for known_addr in all_target_addresses:
            aff = match_address_affinity(known_addr, r_addr)
            if not best_affinity or aff["score"] > best_affinity["score"]:
                best_affinity = aff

        # Only retain verified high-confidence address co-habitants (score >= 0.75 and matched)
        if best_affinity and best_affinity.get("matched") and best_affinity.get("score", 0) >= 0.75:
            seen_house.add(k)
            r["match_reason"] = best_affinity.get("reason", f"Same Address ({primary_pin})")
            household_members.append(r)

    # 2. Process Family Members with Strict Verification & Alternate Linking
    family_members = []
    seen_fam = set()
    raw_fam = p_results.get("family") or []

    target_name = (target.get("name") or "").strip()
    target_words = [w.lower() for w in target_name.split() if len(w) > 2]

    for r in raw_fam:
        cp = "".join(c for c in str(r.get("phoneNumber") or "") if c.isdigit())[-10:]
        ca = "".join(c for c in str(r.get("aadharNumber") or "") if c.isdigit())[-12:]
        if cp in seen_sims or (aadhar and ca and ca == str(aadhar)[-12:]):
            continue
        k = ca if ca else (cp if cp else (r.get("name") or "").strip().lower())
        if k in seen_fam:
            continue

        r_addr = str(r.get("address") or "").strip()
        r_name = (r.get("name") or "").strip()
        r_father = (r.get("fathersName") or "").strip()
        r_other = "".join(c for c in str(r.get("otherNumber") or "") if c.isdigit())[-10:]

        is_verified = False
        reasons = []

        # Check KYC Alternate Nominee linkage
        if cp in alt_phone_set or r_other == clean_phone or (r_other and r_other in alt_phone_set):
            is_verified = True
            reasons.append("KYC Mutual Nominee Match")

        # Check Mother / Spouse relation via address (W/O Father)
        if father and f"w/o {father.lower()}" in r_addr.lower():
            is_verified = True
            reasons.append(f"Mother / Spouse (W/O {father})")
        elif father and r_father.lower() == father.lower():
            # Check Sibling Lineage
            r_words = [w.lower() for w in r_name.split() if len(w) > 2]
            common_words = set(target_words).intersection(set(r_words))

            # Aadhaar proximity check
            aadhaar_proximity = False
            if aadhar and ca and len(str(aadhar)) == 12 and len(ca) == 12:
                if str(aadhar)[:4] == ca[:4]:
                    aadhaar_proximity = True

            # Locality affinity check
            loc_matched = False
            for known_addr in all_target_addresses:
                aff = match_address_affinity(known_addr, r_addr)
                if aff.get("matched") and aff.get("score", 0) >= 0.35:
                    loc_matched = True
                    reasons.append(aff["reason"])
                    break

            # Residing location
            c_addr = clean_address(r_addr)
            addr_loc = c_addr.split(',')[-1].strip() if c_addr else ""

            if bool(common_words) or loc_matched or aadhaar_proximity:
                is_verified = True
                reasons.append(f"Verified Sibling Lineage (Father: {father})")
                if addr_loc and not loc_matched:
                    reasons.append(f"Residing: {addr_loc}")

        if is_verified:
            seen_fam.add(k)
            r["match_reason"] = " | ".join(reasons)
            # Give higher priority to mother/spouse or direct surname matches
            priority = 0
            if "Mother / Spouse" in r["match_reason"]:
                priority = 10
            elif "KYC Mutual Nominee" in r["match_reason"]:
                priority = 8
            elif any(w in r_name.lower() for w in target_words):
                priority = 5
            r["_priority"] = priority
            family_members.append(r)

    family_members.sort(key=lambda x: x.get("_priority", 0), reverse=True)
    family_members = family_members[:8]

    # 3. Process Alternate Contacts with Reverse Intelligence
    alt_contacts = []
    for alt_n, (lookup_future, origin) in alt_lookups.items():
        c_item = {
            "phoneNumber": alt_n,
            "name": "Discovered Alternate Contact",
            "origin": origin,
            "source": "telecom_registry"
        }
        try:
            alt_res = lookup_future.result(timeout=5.0)
            if alt_res.get("results"):
                c_item = dict(alt_res["results"][0])
                c_item["origin"] = origin
        except Exception:
            pass

        # Check relation or geographic affinity with target
        c_addr = clean_address(c_item.get("address"))
        c_father = c_item.get("fathersName", "")
        c_context = []

        if father and c_father and c_father.lower() == father.lower():
            c_context.append(f"Family Nominee (Father: {father})")

        for known_addr in all_target_addresses:
            for loc_kw in ["yamuna nagar", "yamunanagar", "ghaziabad", "delhi", "haryana", "noida"]:
                if loc_kw in known_addr.lower() and loc_kw in c_addr.lower():
                    c_context.append(f"Co-located in {loc_kw.title()}")
                    break
            if c_context:
                break

        c_item["relation_context"] = " | ".join(c_context) if c_context else "KYC Nominated Application Contact"
        alt_contacts.append(c_item)

    deep_data = {
        "phone": clean_phone,
        "target": target,
        "all_records": records,
        "linked_sims": linked_sims,
        "household_members": household_members,
        "family_members": family_members,
        "alt_contacts": alt_contacts,
        "email": target.get("Email")
    }

    resp = {
        "count": len(records),
        "results": records,
        "deep_data": deep_data
    }
    _set_cached_result(cache_key, resp)
    return resp


def format_deep_phone_result(deep_data: dict, duration: float = 0.0, email_osint: dict = None) -> str:
    """Formats the deep intelligence dossier as rich, structured Telegram HTML."""
    target = deep_data.get("target") or {}
    phone = deep_data.get("phone", "")
    safe_phone = html.escape(str(phone))

    lines = [
        "🔬 <b>DEEP INTELLIGENCE DOSSIER [BETA]</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>Investigated Target:</b> <code>{safe_phone}</code>",
        f"⏱️ <b>Investigation Time:</b> {duration:.2f}s | <b>Credits Charged:</b> 3 Credits\n"
    ]

    # 1. Primary Identity
    lines.append("👤 <b>PRIMARY IDENTITY & CARRIER</b>")
    if target.get("name") and not is_invalid_val(target.get("name")):
        lines.append(f"├ 👤 <b>Full Name:</b> {html.escape(str(target['name']).strip())}")
    if target.get("fathersName") and not is_invalid_val(target.get("fathersName")):
        lines.append(f"├ 👨‍👦 <b>Father's Name:</b> {html.escape(str(target['fathersName']).strip())}")
    if target.get("aadharNumber") and not is_invalid_val(target.get("aadharNumber")):
        lines.append(f"├ 🪪 <b>Aadhaar Number:</b> <code>{html.escape(str(target['aadharNumber']).strip())}</code>")
    lines.append(f"├ 📱 <b>Primary Mobile:</b> <code>{safe_phone}</code>")
    if target.get("Carrier") and not is_invalid_val(target.get("Carrier")):
        lines.append(f"├ 📡 <b>Telecom Operator:</b> {html.escape(str(target['Carrier']).strip())}")
    if target.get("address") and not is_invalid_val(target.get("address")):
        clean_addr = clean_address(target['address'])
        if clean_addr:
            lines.append(f"├ 🏠 <b>Registered Address:</b> {html.escape(clean_addr)}")
    if target.get("Email") and not is_invalid_val(target.get("Email")):
        lines.append(f"└ 📧 <b>Email Address:</b> <code>{html.escape(str(target['Email']).strip())}</code>")
    else:
        if lines[-1].startswith("├ "):
            lines[-1] = "└ " + lines[-1][2:]
    lines.append("")

    # 2. Linked SIM Cards (Same Aadhaar)
    linked_sims = deep_data.get("linked_sims", [])
    lines.append("📱 <b>LINKED SIM CARDS (Same Aadhaar Identity)</b>")
    if linked_sims:
        lines.append(f"<i>Discovered {len(linked_sims)} additional registered mobile number(s):</i>")
        for idx, sim in enumerate(linked_sims):
            prefix = "└ " if idx == len(linked_sims) - 1 else "├ "
            sim_no = sim.get("phoneNumber") or ""
            sim_name = sim.get("name") or target.get("name") or "Citizen"
            sim_addr = clean_address(sim.get("address"))
            addr_snippet = f" | 🏠 {sim_addr[:40]}..." if sim_addr else ""
            lines.append(f"{prefix}📱 <code>{html.escape(str(sim_no))}</code> (Registered to: <b>{html.escape(str(sim_name))}</b>{html.escape(addr_snippet)})")
    else:
        lines.append("<i>No additional SIM cards registered under this Aadhaar.</i>")
    lines.append("")

    # 3. Household & Co-habitants (Same Address)
    households = deep_data.get("household_members", [])
    if households:
        lines.append("🏠 <b>HOUSEHOLD & CO-HABITANTS (Address Correlation)</b>")
        lines.append(f"<i>Identified {len(households)} co-habitant(s) sharing registered premises/locality:</i>")
        for idx, hm in enumerate(households):
            prefix = "└ " if idx == len(households) - 1 else "├ "
            h_name = hm.get("name", "Resident")
            h_ph = hm.get("phoneNumber", "")
            h_ad = hm.get("aadharNumber", "")
            h_reason = hm.get("match_reason", "Registered at Same Premises")
            h_line = f"{prefix}👤 <b>{html.escape(str(h_name))}</b>"
            if not is_invalid_val(h_ph):
                h_line += f" — 📱 <code>{html.escape(str(h_ph))}</code>"
            if h_reason:
                h_line += f" <i>(Matches: {html.escape(str(h_reason))})</i>"
            if not is_invalid_val(h_ad):
                h_line += f" | 🪪 <code>{html.escape(str(h_ad))}</code>"
            lines.append(h_line)
        lines.append("")

    # 4. Family & Lineage Linkages
    family = deep_data.get("family_members", [])
    lines.append("👨‍👩‍👧‍👦 <b>FAMILY & LINEAGE LINKAGES (Verified Parental & Sibling Match)</b>")
    if family:
        lines.append(f"<i>Identified {len(family)} verified family member(s) via parental lineage:</i>")
        for idx, fam in enumerate(family):
            prefix = "└ " if idx == len(family) - 1 else "├ "
            fam_name = fam.get("name", "Relative")
            fam_ph = fam.get("phoneNumber", "")
            fam_ad = fam.get("aadharNumber", "")

            match_reason = fam.get("match_reason")
            if not match_reason:
                target_f = (target.get("fathersName") or "").strip()
                match_reason = f"Father: {target_f}" if target_f and not is_invalid_val(target_f) else "Parental Lineage"

            fam_line = f"{prefix}👤 <b>{html.escape(str(fam_name))}</b>"
            if not is_invalid_val(fam_ph):
                fam_line += f" — 📱 <code>{html.escape(str(fam_ph))}</code>"
            if match_reason:
                fam_line += f" <i>(Matches: {html.escape(str(match_reason))})</i>"
            if not is_invalid_val(fam_ad):
                fam_line += f" | 🪪 <code>{html.escape(str(fam_ad))}</code>"
            lines.append(fam_line)
    else:
        lines.append("<i>No direct family members verified at this location in registry chunk.</i>")
    lines.append("")

    # 5. Emergency & Alternate Contacts
    alt_contacts = deep_data.get("alt_contacts", [])
    if alt_contacts:
        lines.append("📞 <b>EMERGENCY & KYC NOMINATED CONTACTS</b>")
        for idx, ac in enumerate(alt_contacts):
            prefix = "└ " if idx == len(alt_contacts) - 1 else "├ "
            ac_ph = ac.get("phoneNumber", "")
            ac_name = ac.get("name", "Nominated Contact")
            ac_orig = ac.get("origin", "Secondary Application Record")
            ac_ctx = ac.get("relation_context", "")
            ac_ad = ac.get("aadharNumber")
            ac_addr = clean_address(ac.get("address"))

            ac_line = f"{prefix}📞 <code>{html.escape(str(ac_ph))}</code> (Registered to: <b>{html.escape(str(ac_name))}</b>"
            if ac_ad and not is_invalid_val(ac_ad):
                ac_line += f" | 🪪 <code>{html.escape(str(ac_ad))}</code>"
            if ac_addr:
                ac_line += f" | 🏠 {html.escape(str(ac_addr[:45]))}..."
            ac_line += f" | <i>{html.escape(str(ac_orig))}</i>"
            if ac_ctx:
                ac_line += f" — <b>{html.escape(str(ac_ctx))}</b>"
            ac_line += ")"
            lines.append(ac_line)
        lines.append("")

    # 6. Gravatar / Breach Scan if present
    if email_osint:
        breach_count = email_osint.get("breach_count", 0)
        breaches = email_osint.get("breaches", [])
        gravatar = email_osint.get("gravatar")
        if gravatar or breach_count > 0:
            lines.append("🌐 <b>DIGITAL FOOTPRINT & BREACH INTELLIGENCE</b>")
            if gravatar and gravatar.get("display_name"):
                lines.append(f"├ 👤 <b>Public Online Name:</b> {html.escape(gravatar['display_name'])}")
            if breach_count > 0:
                top_b = ", ".join(f"<code>{html.escape(b)}</code>" for b in breaches[:8])
                lines.append(f"└ 🛡️ <b>Leaked in {breach_count} Breach(es):</b> {top_b}")
    # 7. Beta Disclaimer
    lines.append("⚠️ <b>DISCLAIMER (BETA FEATURE):</b>")
    lines.append("<i>This deep intelligence dossier is generated by algorithmic multi-hop correlation and is currently in <b>BETA phase</b>. Because records are aggregated from public registries and algorithms are still undergoing active optimization, we are NOT responsible for any inaccuracies, mismatched linkages, or wrong results. Use strictly for informational/OSINT reference.</i>")

    return "\n".join(lines)
