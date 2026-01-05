import argparse
import os
import urllib.request
 
OUTPUT_DIR = "data"
 
FILES = {
    "fr": {
        "sheet_id": "1woG35jXBZwxP4UoQJrfpPGocU5WUlx1VRsPOFn3i-y8",
        "filename": "french_claimreview_LLMdata.csv",
    },
    "de": {
        "sheet_id": "1r-F8PsNZyYDpd034ZDd65TrdJnG7-5JRYhHPVqhxBd8",
        "filename": "german_claimreview_LLMdata.csv",
    },
}
 
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=list(FILES.keys()), default=None)
    args = parser.parse_args()
 
    os.makedirs(OUTPUT_DIR, exist_ok=True)
 
    targets = {args.lang: FILES[args.lang]} if args.lang else FILES
 
    for info in targets.values():
        url = f"https://docs.google.com/spreadsheets/d/{info['sheet_id']}/export?format=csv"
        output_path = os.path.join(OUTPUT_DIR, info["filename"])
        urllib.request.urlretrieve(url, output_path)
 
if __name__ == "__main__":
    main()
