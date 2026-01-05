
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from factcheck_pipeline.claimreview_api import fetch_claimreview_claims, flatten_claims


def url_hash(u: str) -> str:
    return hashlib.md5(u.encode("utf-8")).hexdigest()[:12]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_jsonl(path: str, record: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def maybe_remove_empty_dir(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except Exception:
        pass


def load_image_info_csv(folder: str) -> List[Dict[str, str]]:
    csv_path = os.path.join(folder, "image_info.csv")
    if not os.path.exists(csv_path):
        return []

    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}

    out: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        out.append(
            {
                "image_url": str(row[cols.get("image_url", "image_url")]) if cols.get("image_url") else "",
                "caption": str(row[cols.get("caption", "caption")]) if cols.get("caption") else "",
                "path": str(row[cols.get("path", "path")]) if cols.get("path") else "",
            }
        )
    return out


def sanitize_publisher_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("https://", "").replace("http://", "")
    s = s.replace("www.", "")
    s = s.split("/")[0]
    if "." in s:
        s = s.rsplit(".", 1)[0]
    return s.replace("-", "_")


def _safe_import(module_path: str):
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        if (e.name or "") == module_path:
            return None
        raise


def _get_callable(mod, attr: str) -> Optional[Callable[..., Any]]:
    if mod is None:
        return None
    fn = getattr(mod, attr, None)
    return fn if callable(fn) else None


@dataclass
class PublisherModules:
    publisher_id: str
    publisher_site_filter: str
    article_fetch: Callable[[str], Dict[str, Any]]
    images_handle: Optional[Callable[..., Any]] = None
    videos_handle: Optional[Callable[..., Any]] = None
    label_normalize: Optional[Callable[[str], str]] = None


def load_publisher_modules(
    *,
    publisher_pkg: str,
    publisher_key: str,
    publisher_site_filter: str,
) -> PublisherModules:

    pub = sanitize_publisher_key(publisher_key)
    base_pkg = f"factcheck_pipeline.{publisher_pkg}.{pub}"

    article_mod = importlib.import_module(f"{base_pkg}.{pub}_article")
    article_fetch = _get_callable(article_mod, "fetch_and_extract")
    if article_fetch is None:
        raise ImportError(f"{base_pkg}.{pub}_article must define fetch_and_extract(review_url)")

    img_mod = _safe_import(f"{base_pkg}.{pub}_images")
    images_handle = _get_callable(img_mod, "handle")

    vid_mod = _safe_import(f"{base_pkg}.{pub}_videos")
    videos_handle = _get_callable(vid_mod, "handle")

    norm_mod = _safe_import(f"{base_pkg}.label_normalizer")
    label_normalize = _get_callable(norm_mod, "normalize_label")

    return PublisherModules(
        publisher_id=pub,
        publisher_site_filter=publisher_site_filter,
        article_fetch=article_fetch,
        images_handle=images_handle,
        videos_handle=videos_handle,
        label_normalize=label_normalize,
    )


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--api_key", required=True)

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--publisherDE", help="Publisher key inside factcheck_pipeline.publisherDE (e.g., dpa, correctiv).")
    g.add_argument("--publisherFR", help="Publisher key inside factcheck_pipeline.publisherFR (e.g., afp_factcheck).")

    p.add_argument(
        "--publisher_site_filter",
        required=True,
        help="ClaimReview reviewPublisherSiteFilter (e.g., volksverpetzer.de).",
    )

    p.add_argument("--out_jsonl", required=True, help="Output JSONL path")
    p.add_argument(
        "--assets_dir",
        required=True,
        help="Base folder for downloaded media. Per-claim subfolders are created only if needed.",
    )

    p.add_argument("--language_code", default="de")
    p.add_argument("--page_size", type=int, default=200)
    p.add_argument("--max_items", type=int, default=None)

    p.add_argument("--skip_images", action="store_true")
    p.add_argument("--skip_videos", action="store_true")

    return p.parse_args()


def main():
    args = parse_args()

    if args.publisherDE:
        publisher_pkg = "publisherDE"
        publisher_key = args.publisherDE
    else:
        publisher_pkg = "publisherFR"
        publisher_key = args.publisherFR

    pubmods = load_publisher_modules(
        publisher_pkg=publisher_pkg,
        publisher_key=publisher_key,
        publisher_site_filter=args.publisher_site_filter,
    )

    ensure_dir(os.path.dirname(args.out_jsonl) or ".")
    ensure_dir(args.assets_dir)

    raw_claims = fetch_claimreview_claims(
        api_key=args.api_key,
        review_publisher_site_filter=pubmods.publisher_site_filter,
        page_size=args.page_size,
        language_code=args.language_code,
    )
    items = flatten_claims(raw_claims, review_publisher_site_filter=args.publisher_site_filter)


    seen = set()
    dedup_items = []
    for it in items:
        if not it.review_url:
            continue
        if it.review_url in seen:
            continue
        seen.add(it.review_url)
        dedup_items.append(it)

    if args.max_items is not None:
        dedup_items = dedup_items[: args.max_items]

    n = 0
    for it in dedup_items:
        review_url = it.review_url
        article_id = url_hash(review_url)

        article_asset_dir = os.path.join(args.assets_dir, f"{pubmods.publisher_id}_{article_id}")
        assets_dir_created = False

        #article_content: Dict[str, Any] = {}
        article_error: Optional[str] = None
        try:
            article_content = pubmods.article_fetch(review_url) or {}
        except Exception as e:
            article_error = str(e)
            article_content = {}

        images: List[Dict[str, str]] = []
        images_error: Optional[str] = None
        if not args.skip_images and pubmods.images_handle is not None:
            try:
                ensure_dir(article_asset_dir)
                assets_dir_created = True

                pubmods.images_handle(review_url, location_info=article_asset_dir)
                images = load_image_info_csv(article_asset_dir)
            except Exception as e:
                images_error = str(e)
                images = []

        video_urls: List[str] = []
        videos_error: Optional[str] = None
        if not args.skip_videos and pubmods.videos_handle is not None:
            try:
                if not assets_dir_created:
                    ensure_dir(article_asset_dir)
                    assets_dir_created = True

                try:
                    video_urls = pubmods.videos_handle(review_url) or []
                except TypeError:
                    video_urls = pubmods.videos_handle(review_url, location_info=article_asset_dir) or []
            except Exception as e:
                videos_error = str(e)
                video_urls = []

        has_media = bool(images) or bool(video_urls)

        if assets_dir_created and not has_media:
            maybe_remove_empty_dir(article_asset_dir)
            assets_dir_created = False

        label_norm = it.review_label_raw
        if pubmods.label_normalize is not None:
            try:
                label_norm = pubmods.label_normalize(it.review_label_raw)
            except Exception:
                label_norm = it.review_label_raw

        media_obj: Dict[str, Any] = {"images": images, "videos": video_urls}
        if has_media and assets_dir_created:
            media_obj["assets_dir"] = article_asset_dir

        errors_obj: Dict[str, str] = {}
        if article_error:
            errors_obj["article"] = article_error
        if images_error:
            errors_obj["images"] = images_error
        if videos_error:
            errors_obj["videos"] = videos_error

        record: Dict[str, Any] = {
            "publisher_id": pubmods.publisher_id,
            "language": args.language_code,
            "claim": it.claim_text,
            "claimant": it.claimant,
            "claim_date": it.claim_date,
            "label_raw": it.review_label_raw,
            "label_normalized": label_norm,
            "claimreview": {
                "review_url": it.review_url,
                "review_title": it.review_title,
                "review_date": it.review_date,
                "publisher_name": it.publisher_name,
                "publisher_site": it.publisher_site,
            },
            "debunk_article": {
                "url": article_content.get("url", review_url),
                "title": article_content.get("title", ""),
                "text": article_content.get("content", ""),
            },
            "media": media_obj,
        }

        if errors_obj:
            record["errors"] = errors_obj

        write_jsonl(args.out_jsonl, record)
        n += 1

    print(f"Saved {n} records to {args.out_jsonl}")


if __name__ == "__main__":
    main()
