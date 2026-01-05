# factcheck_pipeline

A multilingual, multimodal data collection pipeline for constructing fact-checking datasets from ClaimReview feeds and publisher websites. The pipeline aggregates verified claims via the [Google ClaimReview API](https://developers.google.com/search/docs/appearance/structured-data/factcheck), scrapes full debunking articles from fact-checking organizations' websites using publisher-specific extraction adapters, downloads associated visual media (images, captions, and videos), and normalizes heterogeneous verdict labels into a unified label set (True, False, Partially-True, Other).

## Usage

```bash
python -m factcheck_pipeline.pipeline \
  --api_key "YOUR_API_KEY" \
  --publisherFR "PUBLISHER_NAME" \
  --publisher_site_filter "PUBLISHER_SITE" \
  --out_jsonl "data/fr/output.jsonl" \
  --assets_dir "data/fr/output" \
  --language_code fr
```

### Arguments

| Argument | Description |
|----------|-------------|
| `--api_key` | Google Fact Check Tools API key |
| `--publisherFR` / `--publisherDE` | Publisher module name |
| `--publisher_site_filter` | ClaimReview publisher site filter (e.g., `lemonde.fr`) |
| `--out_jsonl` | Output JSONL file path |
| `--assets_dir` | Directory for downloaded images and videos |
| `--language_code` | Language code: `fr` or `de` |
| `--max_items` | Maximum number of claims to process (optional) |
| `--skip_images` | Skip image downloading (optional) |
| `--skip_videos` | Skip video downloading (optional) |

## Dataset Overview

| Dataset | Claims | Period | Languages |
|---------|--------|--------|-----------|
| French  | 5,170  | 2014–2025 | FR |
| German  | 3,555  | 2017–2023 | DE |

Each record contains: reviewer organization, claim statement, claim date, normalized verdict label, review title, review URL, LLM-extracted evidence and generated justifications (Gemini-2.5 Pro, Llama3.3-70B, Qwen2.5-72B).

## Download

**Download all datasets:**

```bash
python download_LLMdata.py
```

**Download a specific language:**

```bash
python download_LLMdata.py --lang fr    # French only
python download_LLMdata.py --lang de    # German only
```

Downloaded files will be saved to the `data/` directory.

