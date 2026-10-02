"""
Downloads NHTSA's public Standing General Order (SGO) crash incident
report CSVs — real, open, government safety-event data — for use with
the triage agent. No account, no key, no scraping: these are official
public data files.

Source: https://www.nhtsa.gov/laws-regulations/standing-general-order-crash-reporting
"""
import os
import sys
import urllib.request

BASE = "https://static.nhtsa.gov/odi/ffdd/sgo-2021-01"
FILES = {
    "sgo_ads.csv": f"{BASE}/SGO-2021-01_Incident_Reports_ADS.csv",
    "sgo_adas.csv": f"{BASE}/SGO-2021-01_Incident_Reports_ADAS.csv",
    "sgo_other.csv": f"{BASE}/SGO-2021-01_Incident_Reports_OTHER.csv",
}

OUT_DIR = os.path.join(os.path.dirname(__file__), "data")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for filename, url in FILES.items():
        dest = os.path.join(OUT_DIR, filename)
        print(f"Downloading {url} -> {dest}")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            continue
        size_kb = os.path.getsize(dest) / 1024
        print(f"  done ({size_kb:.0f} KB)")

    print("\nDone. Use data/sgo_ads.csv (Automated Driving Systems — the")
    print("richest of the three feeds) to start.")


if __name__ == "__main__":
    main()
