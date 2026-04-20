# Download LLM evidence-justification datasets for ClaimReview claims in French and German

import argparse
import os
import urllib.request

OUTPUT_DIR = "data"

FILES = {
    "fr": {
        "file_id": "1E-5cj5PyiAPqhHJDW2AmCG9JBAORGD5s",
        "filename": "FR_claimreview_LLMdata.csv",
    },
    "de": {
        "file_id": "12gdHt67jv8yoarixufONSKZOs25JGCq6",
        "filename": "DE_claimreview_LLMdata.csv",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=list(FILES.keys()), default=None)
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    targets = {args.lang: FILES[args.lang]} if args.lang else FILES

    for lang, info in targets.items():
        url = f"https://drive.google.com/uc?id={info['file_id']}&export=download"
        output_path = os.path.join(OUTPUT_DIR, info["filename"])
        print(f"Download: {info['filename']}...")
        urllib.request.urlretrieve(url, output_path)
        print(f"  Save to: {output_path}")


if __name__ == "__main__":
    main()
