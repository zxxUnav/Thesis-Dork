#!/usr/bin/env python3
"""
Reads input_pii.txt (one PII per line) and domains.txt (one domain per line).
Outputs normalized rows and example site-scoped dorks.
No CSV input support; simple and short.
"""

# import required libraries
from __future__ import annotations # buat memastikan kompatibilitas tipe data untuk Python yang old ver 
import argparse, csv, re # Untuk membuat CLI yang UI friendly , csv dan re untuk regex
from pathlib import Path # cara untuk mengelola path file dan direktori
from typing import List, Tuple  # Untuk memberikan petunjuk tipe data (type hinting) biar kode lebih jelas

# Regex -> fastest way buat mencocokkan pola teks.
# Definisikan beberapa pola untuk mendeteksi jenis data PII.
PATTERNS = {
    "email": re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"),
    "nik": re.compile(r"^\d{16}$"),
    "phone": re.compile(r"^(?:\+62|62|0)8[1-9][0-9]{6,9}$"),
    "date": re.compile(r"^(?:\d{2}[-/]\d{2}[-/](?:19|20)\d{2})$"),
}

def detect_type(s: str) -> str:
    v = s.strip() # Menghapus spasi / karakter kosong di awal dan akhir string
    # quick name|dob detection

    if "|" in v and re.search(r"\d{4}", v): # Quick detection untuk format khusus "Nama|TanggalLahir"
        return "name_dob"
    
    # Loop melalui setiap pola regex yang udah dibuat di atas
    for k, p in PATTERNS.items():
        if p.match(v):
            return k  # Jika string cocok dengan salah satu pola
                      # Kembalikan nama polanya (misal: "email", "nik")
    
    # Kalo input data tidak cocok dengan regex di atas, kita coba deteksi umum
    if v.isdigit():      # Jika string hanya berisi angka
        return "numeric"
    
    if any(c.isalpha() for c in v) and any(c.isdigit() for c in v): # Jika mengandung huruf DAN angka
        return "alphanumeric"

    return "unknown" # Jika semua deteksi gagal, tandai sebagai "unknown"

def read_lines(path: Path) -> List[str]:
    if not path.exists(): # Cek dulu apakah filenya ada , klo gada, return list kosong
        return []
    
    # Buka file dengan encoding utf-8 (standar umum)
    with path.open(encoding="utf-8") as f:  
        # Ini  "list comprehension", cara cepat untuk membuat list.
        # Artinya: untuk setiap baris (ln) di file, ambil baris itu, hapus spasi,
        # dan masukkan ke list HANYA JIKA baris itu tidak kosong dan tidak diawali '#'
        
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]

def gen_site_dorks(domain: str, value: str, detected: str) -> List[str]:
    # Dork dasar untuk membatasi pencarian hanya pada satu situs web
    base = f"site:{domain}"

    # Logic untuk membuat dork yang lebih spesifik berdasarkan tipe data

    if detected == "email":                                             # Untuk email, cari email persis atau email yang ada di dalam teks halaman
        return [f'{base} "{value}"', f'{base} intext:"{value}"']

    if detected == "phone":                                             # Untuk no. telp, cari juga di dalam file spreadsheet (xls)    
        return [f'{base} "{value}"', f'{base} intext:"{value}"', f'{base} filetype:xls intext:"{value}"']
    
    if detected in ("nik","numeric"):                                   # Untuk NIK atau angka, cari juga di dalam file spreadsheet (xls)
        return [f'{base} "{value}"', f'{base} intext:"{value}"', f'{base} filetype:xls intext:"{value}"']

    if detected == "name_dob":                                           # Untuk format name|dob, pisahkan dulu
        name, dob = (value.split("|",1)+[""])[:2]
        return [f'{base} "{name}" "{dob}"', f'{base} intext:"{name}" filetype:pdf']
    # Cari kombinasi nama dan tanggal lahir, atau nama di dalam file PDF (sering untuk CV atau dokumen resmi)
    return [f'{base} "{value}"', f'{base} intext:"{value}"']

def main():
    """Fungsi utama yang akan dieksekusi saat script dijalankan."""
    # Membuat parser untuk argumen baris perintah
    ap = argparse.ArgumentParser(description="Domain-scoped loader (txt-only).")
    ap.add_argument("-i","--input", default="input_pii.txt", help="PII input file (.txt), one per line.")
    ap.add_argument("-d","--domains-file", default="domains.txt", help="Domains list (.txt), one domain per line.")
    ap.add_argument("--dry-run", action="store_true", help="Do not execute queries; just print/save templates.")
    ap.add_argument("--output", "-o", default="", help="Optional CSV output path to save results.")
    args = ap.parse_args() # Memproses argumen yang diberikan pengguna

    # Membaca file input PII dan domain menggunakan fungsi read_lines
    inputs = read_lines(Path(args.input))
    domains = read_lines(Path(args.domains_file))

    # Validasi: Pastikan kita punya data untuk diproses
    if not inputs:
        print(f"No PII inputs found in {args.input}.")
        return
    if not domains:
        print(f"No domains found in {args.domains_file}. Provide at least one domain.")
        return
    
    # Siapkan list kosong untuk menampung semua hasil
    rows: List[Tuple[str,str,str,str]] = []  # domain, value, detected, dorks_joined

    # Loop 1st utama: iterasi melalui setiap data PII
    for value in inputs:
        detected = detect_type(value) #detect tipe PII

        # Loop 2 loop melalui setiap domain untuk setiap data PII skrg
        for domain in domains:
            dorks = gen_site_dorks(domain, value, detected) # Buat dork-nya
            # Tambahkan hasilnya ke list 'rows'
            # " || ".join(dorks) menggabungkan semua dork menjadi satu string dipisahkan oleh " || "
            rows.append((domain, value, detected, " || ".join(dorks)))

            # Cetak informasi ke layar agar pengguna tahu proses berjalan
            print(f"READ INPUT: {value} (detected_type={detected}) @ {domain}")
            for i, q in enumerate(dorks, 1):
                print(f"  {i}) {q}")
        print()  # spacer per input

    # Kalo pengguna memberikan argumen --output, simpan hasilnya ke file CSV
    if args.output:
        outp = Path(args.output)
        with outp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["domain","value","detected_type","dorks"])
            for r in rows:
                w.writerow(r)
        print(f"Saved {len(rows)} rows to {args.output}")

    if args.dry_run:
        print("DRY-RUN: no queries executed.")

if __name__ == "__main__":
    main()
