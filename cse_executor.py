# cse_executor.py
from __future__ import annotations

import os
import time
import random
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


# -------- URL hygiene (normalize + domain-check + dedup) --------

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "yclid", "mc_cid", "mc_eid"
}

def normalize_url(raw: str) -> str:
    """Buang fragment #..., rapikan tracking params umum, dan normalisasi scheme+host."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    try:
        u = urlparse(raw)
        # drop fragment
        fragment = ""
        # clean tracking params
        q = [(k, v) for (k, v) in parse_qsl(u.query, keep_blank_values=True) if k not in TRACKING_KEYS]
        query = urlencode(q, doseq=True)
        # normalize
        scheme = (u.scheme or "").lower()
        netloc = (u.netloc or "").lower()
        return urlunparse((scheme, netloc, u.path, u.params, query, fragment))
    except Exception:
        return raw


def is_domain_scoped(url: str, domain: str) -> bool:
    """Pastikan hasil benar-benar masih di domain target (host==domain atau subdomainnya)."""
    try:
        host = (urlparse(url).netloc or "").lower()
        d = (domain or "").strip().lower()
        return host == d or host.endswith("." + d)
    except Exception:
        return False


def dedup_results(items: List[Dict[str, str]], key: str = "url") -> List[Dict[str, str]]:
    """Dedup item berdasarkan key (default url). Sekalian normalize URL."""
    seen = set()
    out = []
    for it in items:
        val = it.get(key, "")
        if key == "url":
            val = normalize_url(val)
            it["url"] = val
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(it)
    return out


# -------- Env loading & logging --------

def load_cse_env_or_exit() -> Dict[str, str]:
    """
    Wajib:
    - baca .env: GOOGLE_API_KEY, GOOGLE_CSE_ID
    - kalau tidak ada, pesan jelas dan exit
    """
    if load_dotenv is not None:
        load_dotenv()

    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    cse_id = (os.getenv("GOOGLE_CSE_ID") or "").strip()

    if not api_key or not cse_id:
        print(
            "[!] Engine 'cse' membutuhkan env berikut di file .env:\n"
            "    GOOGLE_API_KEY=...\n"
            "    GOOGLE_CSE_ID=...\n"
            "    (Tanpa itu, program tidak bisa menjalankan request CSE.)\n"
            "    Exiting."
        )
        raise SystemExit(2)

    return {"GOOGLE_API_KEY": api_key, "GOOGLE_CSE_ID": cse_id}


def setup_logger(log_path: str):
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logging.info("Logger initialized (CSE)")


# -------- Error classifier (buat kolom snippet_or_error rapi) --------

def classify_cse_error(message: str) -> str:
    m = (message or "").lower()

    if "missing env" in m or "membutuhkan env" in m or "required" in m:
        return "ERR_MISSING_ENV"
    if "quota" in m or "daily limit" in m or "limit exceeded" in m:
        return "ERR_QUOTA_EXCEEDED"
    if "api key not valid" in m or "invalid key" in m or "bad api key" in m:
        return "ERR_INVALID_KEY"
    if "http 429" in m or "too many requests" in m or "rate limit" in m:
        return "ERR_RATE_LIMIT"
    if "timeout" in m:
        return "ERR_TIMEOUT"

    # 403 bisa invalid key / quota / access denied
    if "http 403" in m or "forbidden" in m:
        return "ERR_FORBIDDEN"

    return "ERR_UNKNOWN"


# -------- Rate limit & retry policy --------

def rate_limit_sleep(sleep_min: float, sleep_max: float, jitter: bool = True):
    if sleep_max < sleep_min:
        sleep_max = sleep_min
    if jitter:
        time.sleep(random.uniform(sleep_min, sleep_max))
    else:
        time.sleep(sleep_min)


def should_retry(status_code: Optional[int], err_text: str) -> bool:
    """Retry hanya untuk kondisi yang biasanya transient."""
    if status_code in (429, 500, 502, 503, 504):
        return True
    et = (err_text or "").lower()
    if "timeout" in et or "temporarily" in et or "connection" in et:
        return True
    return False


def log_query_summary(domain: str, detected_type: str, dork: str, status: str, got_n: int, elapsed_ms: int):
    logging.info(
        f"CSE_SUMMARY | domain={domain} | type={detected_type} | status={status} | got={got_n} | ms={elapsed_ms} | dork={dork}"
    )


# -------- Core CSE call (support paging) --------

def _cse_http_call(
    session: requests.Session,
    query: str,
    api_key: str,
    cse_id: str,
    start: int,
    num: int,
    timeout: int
) -> Tuple[int, Dict]:
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": cse_id, "q": query, "start": start, "num": num}
    resp = session.get(url, params=params, timeout=timeout)
    status = resp.status_code
    try:
        data = resp.json()
    except Exception:
        data = {"_raw": resp.text[:500]}
    return status, data


def cse_search_paged(
    query: str,
    api_key: str,
    cse_id: str,
    total_results: int = 10,
    timeout: int = 20,
    retries: int = 3,
    backoff_base: float = 1.6,
    sleep_min: float = 1.0,
    sleep_max: float = 2.0,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, str]]:
    """
    Ambil SERP via CSE JSON API, support total_results > 10 (paging start=1,11,21,...).
    Return list item: {rank,title,url,snippet}
    """
    sess = session or requests.Session()
    total_results = max(1, int(total_results))

    results: List[Dict[str, str]] = []
    start = 1
    rank = 1

    # paging: CSE per page max 10
    while len(results) < total_results:
        remaining = total_results - len(results)
        num = min(10, remaining)

        last_err = None
        t0 = time.time()

        for attempt in range(1, retries + 1):
            try:
                rate_limit_sleep(sleep_min, sleep_max, jitter=True)

                status, data = _cse_http_call(
                    session=sess,
                    query=query,
                    api_key=api_key,
                    cse_id=cse_id,
                    start=start,
                    num=num,
                    timeout=timeout,
                )

                if status != 200:
                    # ambil message error kalau ada
                    msg = ""
                    if isinstance(data, dict):
                        msg = (data.get("error", {}) or {}).get("message", "") or str(data)
                    else:
                        msg = str(data)

                    err = f"HTTP {status}: {msg}"
                    if should_retry(status, err) and attempt < retries:
                        sleep_s = (backoff_base ** attempt) + random.uniform(0.0, 0.7)
                        logging.warning(f"CSE retry {attempt}/{retries} in {sleep_s:.2f}s | {err} | q={query}")
                        time.sleep(sleep_s)
                        continue

                    raise RuntimeError(err)

                items = (data.get("items") or []) if isinstance(data, dict) else []
                for it in items:
                    title = (it.get("title") or "").strip()
                    link = (it.get("link") or "").strip()
                    snippet = (it.get("snippet") or "").strip()
                    results.append({"rank": rank, "title": title, "url": link, "snippet": snippet})
                    rank += 1

                # kalau items kosong, stop paging biar gak loop kosong terus
                if not items:
                    break

                elapsed_ms = int((time.time() - t0) * 1000)
                logging.info(f"CSE page ok | start={start} | got={len(items)} | ms={elapsed_ms} | q={query}")
                break

            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = str(e)
                if attempt >= retries:
                    raise RuntimeError(f"Timeout/ConnError: {last_err}")
                sleep_s = (backoff_base ** attempt) + random.uniform(0.0, 0.7)
                logging.warning(f"CSE net retry {attempt}/{retries} in {sleep_s:.2f}s | err={last_err} | q={query}")
                time.sleep(sleep_s)

            except Exception as e:
                last_err = str(e)
                # kalau tidak perlu retry / sudah habis
                raise RuntimeError(last_err)

        # next page
        start += 10

        # kalau sudah dapat total, stop
        if len(results) >= total_results:
            break

        # safety: jika start terlalu besar (CSE punya limit hasil), stop
        if start > 91:  # start max biasanya 91 untuk 100 results
            break

    # post-process: normalize + dedup
    results = dedup_results(results, key="url")
    return results
