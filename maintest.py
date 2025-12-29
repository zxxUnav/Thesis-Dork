# main.py
"""Entry point CLI untuk loader dorking.
Menjalankan workflow: baca input -> deteksi tipe -> generate dorks -> print/save.
Import fungsi utama dari loaderev.py supaya modul tetap modular.
"""
from pathlib import Path
import os
import argparse
import csv
from typing import List, Tuple

# import fungsi dari loaderev.py — pastikan loaderev.py ada di folder yang sama
from loaderev import read_lines, detect_type, gen_site_dorks

from dork_executor import (
    build_driver,
    google_search,
    setup_logger,
    save_block_screenshot
)

# fungsi tambahan untuk validasi domain
import re
DOMAIN_RE = re.compile(r"^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}$")

from cse_executor import (
    load_cse_env_or_exit,
    setup_logger as setup_logger_cse,
    cse_search_paged,
    is_domain_scoped,
    classify_cse_error,
    log_query_summary,
)


def validate_domain(domain: str) -> bool:
    """Return True jika domain tampak valid menurut regex sederhana."""
    d = domain.strip().lower()
    return bool(DOMAIN_RE.match(d))

def main():
    ap = argparse.ArgumentParser(description="Domain-scoped loader (txt-only).")
    ap.add_argument("--engine",default="",choices=["selenium", "cse"],help="Pilih executor: selenium (browser) atau cse (Google Custom Search JSON API).")
    ap.add_argument("--retries", type=int, default=3, help="Retry untuk CSE.")
    ap.add_argument("--timeout", type=int, default=20, help="HTTP timeout untuk CSE (detik).")

    ap.add_argument("-i", "--input", default="input_pii.txt",
                    help="File input PII (.txt), satu baris per data.")
    ap.add_argument("-d", "--domains-file", default="domains.txt",
                    help="File daftar domain (.txt), satu domain per baris.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mode simulasi (tidak menjalankan query, hanya tampilkan).")
    ap.add_argument("--output", "-o", default="",
                    help="Path file CSV opsional untuk menyimpan hasil.")
    ap.add_argument("--verbose", action="store_true",
                    help="Cetak info tambahan seperti absolute paths dan hitungan.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Proses maksimal N input PII (0 = semua).")
    ap.add_argument("--filter", dest="type_filter", default="",
                    help="Hanya process tipe tertentu (email|phone|nik|unknown|name_dob). Kosong=semua.")
    ap.add_argument("--execute", action="store_true",
                help="Jalankan dork ke Google via Selenium.")
    ap.add_argument("--browser", default="chrome", choices=["chrome", "firefox"],
                help="Browser untuk Selenium.")
    ap.add_argument("--headless", action="store_true",
                help="Jalankan browser tanpa GUI.")
    ap.add_argument("--max-results", type=int, default=5,
                help="Ambil maksimal N hasil per query.")
    ap.add_argument("--exec-limit", type=int, default=0,
                help="Batasi jumlah dork yang dieksekusi (0 = semua).")
    ap.add_argument("--wait", type=int, default=15,
                help="Timeout/wait Selenium (detik).")
    ap.add_argument("--results", default="results.csv",
                help="CSV output hasil eksekusi Selenium.")
    ap.add_argument("--log", default="executor.log",
                help="Path log executor Selenium.")
    ap.add_argument("--sleep-min", type=float, default=1.2,
                help="Delay minimum antar query (detik).")
    ap.add_argument("--sleep-max", type=float, default=2.8,
                help="Delay maksimum antar query (detik).")

    args = ap.parse_args()

    engine = args.engine.strip().lower()
    if not engine and getattr(args, "execute", False):
        engine = "selenium"

    input_path = Path(args.input)
    domains_path = Path(args.domains_file)

    if args.verbose:
        print("[VERBOSE] Current working dir:", os.getcwd())
        print("[VERBOSE] Input path :", input_path.resolve())
        print("[VERBOSE] Domains path:", domains_path.resolve())

    # gunakan fungsi read_lines dari loaderev.py
    inputs = read_lines(input_path)
    domains = read_lines(domains_path)

    if not inputs:
        print(f"[!] Tidak ada data PII di {args.input}.")
        return
    if not domains:
        print(f"[!] Tidak ada domain di {args.domains_file}. Harap tambahkan setidaknya satu domain.")
        return

    # validate domains
    valid_domains = []
    invalid_domains = []
    for d in domains:
        if validate_domain(d):
            valid_domains.append(d)
        else:
            invalid_domains.append(d)
    if invalid_domains:
        print("[!] Peringatan: beberapa domain tampak tidak valid dan akan tetap diproses, tapi cek kembali:")
        for d in invalid_domains:
            print("   -", d)

    to_process = inputs if args.limit <= 0 else inputs[:args.limit]
    if args.verbose:
        print(f"[VERBOSE] Total PII read: {len(inputs)}; to process: {len(to_process)}")
        print(f"[VERBOSE] Total domains read: {len(domains)}; valid: {len(valid_domains)}")

    rows = []
    for value in to_process:
        detected = detect_type(value)

        if args.type_filter:
            allowed = [x.strip() for x in args.type_filter.split(",") if x.strip()]
            if detected not in allowed:
                if args.verbose:
                    print(f"[SKIP] {value} (detected={detected}) not in filter {allowed}")
                continue

        for domain in domains:
            dorks = gen_site_dorks(domain, value, detected)

            # ✅ INI YANG PENTING: flatten
            for q in dorks:
                rows.append({
                    "domain": domain,
                    "value": value,
                    "detected_type": detected,
                    "dork": q
                })
            
            print(f"READ INPUT: {value} (detected_type={detected}) @ {domain}")
            for i, q in enumerate(dorks, 1):
                print(f"  {i}) {q}")
        print()

    if args.output:
        outp = Path(args.output)
        with outp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["domain", "value", "detected_type", "dorks"])
            for r in rows:
                w.writerow([r["domain"], r["value"], r["detected_type"], r["dork"]])
        print(f"[OK] Disimpan: {len(rows)} baris ke file {args.output}")

    if args.dry_run:
        print("DRY-RUN MODE: tidak ada query yang dijalankan ke Google.")
        return
    
    print("[DEBUG] rows count:", len(rows))
    print("[DEBUG] sample row:", rows[0] if rows else None)

    # =========================
    # ENGINE CSE (Jalur C)
    # =========================
    if args.engine == "cse":
        print("[EXEC] Starting CSE (Google Custom Search JSON API) executor...")

        # wajib .env
        env = load_cse_env_or_exit()
        setup_logger_cse(args.log)

        api_key = env["GOOGLE_API_KEY"]
        cse_id = env["GOOGLE_CSE_ID"]

        with open(args.results, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["domain","value","detected_type","dork","rank","title","url","snippet_or_error"])

            work = rows if args.exec_limit <= 0 else rows[:args.exec_limit]
            for row in work:
                domain = row["domain"]
                value = row["value"]
                detected_type = row["detected_type"]
                dork = row["dork"]

                try:
                    results = cse_search_paged(
                        query=dork,
                        api_key=api_key,
                        cse_id=cse_id,
                        total_results=args.max_results,   # boleh > 10, paging otomatis
                        timeout=args.timeout,
                        retries=args.retries,
                        sleep_min=args.sleep_min,
                        sleep_max=args.sleep_max,
                    )

                    for r in results:
                        url = r["url"]
                        # guard domain-scoped
                        if not is_domain_scoped(url, domain):
                            continue

                        writer.writerow([
                            domain, value, detected_type, dork,
                            r["rank"], r["title"], url, r["snippet"]
                        ])

                except Exception as e:
                    code = classify_cse_error(str(e))
                    writer.writerow([
                        domain, value, detected_type, dork,
                        -1, "", "", f"{code}: {e}"
                    ])
                    # STOP kalau quota habis (biar gak bakar request lain)
                    if code == "ERR_QUOTA_EXCEEDED":
                        print("[!] Quota CSE harian habis. Stop supaya tidak buang request.")
                        return

        return


    if args.execute:
        print("[EXEC] Starting Selenium executor...")
        setup_logger(args.log)
        driver = build_driver(
            browser=args.browser,
            headless=args.headless,
            wait=args.wait
        )

    with open(args.results, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["domain","value","detected_type","dork","rank","title","url","snippet_or_error"])

        for row in rows:  # <-- rows, bukan dorks
            query = row["dork"]
            try:
                results = google_search(driver, query, max_results=args.max_results, wait=args.wait)
                for r in results:
                    writer.writerow([
                        row["domain"], row["value"], row["detected_type"], query,
                        r["rank"], r["title"], r["url"], r["snippet"]
                    ])
            except Exception as e:
                shot = save_block_screenshot(driver)
                writer.writerow([
                    row["domain"], row["value"], row["detected_type"], query,
                    -1, "", "", f"ERROR: {e} | screenshot={shot}"
                ])
                break

    driver.quit()

# pastikan main() dipanggil ketika file ini dieksekusi langsung
if __name__ == "__main__":
    main()

