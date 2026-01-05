# factcheck_pipeline

```bash
python -m factcheck_pipeline.pipeline \
  --api_key "YOUR_API_KEY" \
  --publisherFR (OR publisherDE) "PUBLISHER_NAME" \
  --publisher_site_filter "PUBLISHER_SITE" \
  --out_jsonl "data/fr/OUTPUT.jsonl" \
  --assets_dir "data/fr/OUTPUT" \
  --language_code fr (OR de) \
  --max_items \
  --skip_images \ (optional)
  --skip_videos (optional)
