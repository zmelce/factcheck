import os
import io
import re
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup
from PIL import Image

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
}

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")


def fetch_html(url: str, timeout: int = 30) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text

def pick_from_srcset(srcset: str) -> str | None:
    best = None
    best_w = -1
    for part in str(srcset).split(","):
        toks = part.strip().split()
        if not toks:
            continue
        cand = toks[0]
        w = 0
        if len(toks) > 1:
            m = re.search(r"(\d+)w", toks[1])
            if m:
                w = int(m.group(1))
            elif toks[1].endswith("x"):
                try:
                    w = int(float(toks[1][:-1]) * 1000)
                except Exception:
                    w = 0
        if w >= best_w:
            best_w = w
            best = cand
    return best

def absurl(base_url: str, u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(base_url, u)

def get_img_url(img, base_url: str) -> str | None:
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        val = img.get(attr)
        if val and val.strip():
            return absurl(base_url, val.strip())
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        chosen = pick_from_srcset(srcset)
        if chosen:
            return absurl(base_url, chosen)
    return None

def safe_filename_from_url(url: str) -> str:
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    last = clean.split("/")[-1] or "image"
    last = re.sub(r"[^\w.\-]+", "_", last)
    return last or "image"

def ensure_extension(filename: str, content_type: str | None) -> str:
    root, ext = os.path.splitext(filename)
    if ext:
        return filename
    if content_type:
        guess = mimetypes.guess_extension((content_type.split(";")[0] or "").strip().lower())
        if guess:
            return root + guess
    return root + ".jpg"

def download_image(url: str, timeout: int = 60) -> tuple[bytes, str | None]:
    r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
    r.raise_for_status()
    content = r.content
    ctype = r.headers.get("Content-Type", None)
    return content, ctype

def image_info_from_bytes(content: bytes) -> tuple[int | None, int | None, str | None]:
    try:
        with Image.open(io.BytesIO(content)) as im:
            w, h = im.size
            return w, h, im.format
    except Exception:
        return None, None, None

def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def clean_text(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def choose_container(soup: BeautifulSoup):
    for sel in (
        'div.entry-content[itemprop="text"]',
        "article",
        "main",
        "div.entry-content",
        "div.post-content",
        "div.article__body",
    ):
        node = soup.select_one(sel)
        if node:
            return node
    return soup

def extract_images_with_captions(page_url: str, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    container = choose_container(soup)
    results, seen = [], set()

    for fig in container.find_all("figure"):
        img = fig.find("img")
        if not img:
            continue
        u = get_img_url(img, page_url)
        if not u or u in seen:
            continue
        path_lower = urlparse(u).path.lower()
        if any(path_lower.endswith(ext) for ext in IMG_EXT) or True:
            seen.add(u)
            cap_el = fig.find("figcaption")
            cap = clean_text(cap_el.get_text(" ", strip=True)) if cap_el else ""
            results.append({"image_url": u, "caption": cap})

    for img in container.find_all("img"):
        if img.find_parent("figure"):
            continue
        u = get_img_url(img, page_url)
        if not u or u in seen:
            continue
        path_lower = urlparse(u).path.lower()
        if any(path_lower.endswith(ext) for ext in IMG_EXT) or True:
            seen.add(u)
            results.append({"image_url": u, "caption": ""})

    return results

def enrich_records_with_download(records: list[dict], out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    enriched = []

    for rec in records:
        url = rec["image_url"]
        caption = rec.get("caption", "")
        try:
            content, ctype = download_image(url)
        except Exception as e:
            continue

        base = safe_filename_from_url(url)
        base = ensure_extension(base, ctype)
        w, h, fmt = image_info_from_bytes(content)

        digest = sha256_hex(content)[:12]
        root, ext = os.path.splitext(base)
        fname = f"{root}_{digest}{ext}" if (out_dir / base).exists() else base

        dest = out_dir / fname
        dest.write_bytes(content)

        enriched.append({
            "image_url": url,
            "caption": caption,
            "path": fname,
        })

    return enriched


def enrich_dataframe_with_images_list(
    article_url: str,
    out_dir: str,
    download: bool = True,
    store_json: bool = True,
    timeout: int = 30
):
    out_path = Path(out_dir)
    images = []

    if article_url:
        html = fetch_html(article_url, timeout=timeout)
        items = extract_images_with_captions(article_url, html)
        if download and items:
            images = enrich_records_with_download(items, out_path)
        else:
            images = [{"image_url": it["image_url"], "caption": it.get("caption", ""), "path": None}
                      for it in items]

    return images

def handle(review_url: str, location_info: str, headless: bool = True):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
        store_json=True,
    )
    out_df = pd.DataFrame(out_list)
    if not out_df.empty:
        os.makedirs(location_info, exist_ok=True)
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
