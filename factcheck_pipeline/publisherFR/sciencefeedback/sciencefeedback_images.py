
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; scifeed-img-scraper/1.7)"}
IMG_EXT  = (".jpg", ".jpeg", ".png", ".webp" , ".avif")


def absurl(base, u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("data:"):
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(base, u)

def pick_largest_from_srcset(srcset, base):
    best_u, best_w = None, -1
    for part in str(srcset).split(","):
        p = part.strip().split()
        if not p:
            continue
        u = absurl(base, p[0])
        w = 0
        if len(p) > 1:
            d = p[1].lower()
            if d.endswith("w"):
                try:
                    w = int(d[:-1])
                except:
                    w = 0
            elif d.endswith("x"):
                try:
                    w = int(float(d[:-1]) * 1000)
                except:
                    w = 0
        if u and w > best_w:
            best_u, best_w = u, w
    return best_u

def clean_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def safe_slug(s, length=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s)
    s = s.strip("._")
    return s[-length:] if len(s) > length else (s or "article")

def download_image(url, dest_dir, prefix):
    if not url:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, stream=True)
        r.raise_for_status()
        ext = get_ext(url)
        if ext not in IMG_EXT:
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if "jpeg" in ct: ext = ".jpg"
            elif "png" in ct: ext = ".png"
            elif "webp" in ct: ext = ".webp"
            else: ext = ".jpg"
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        fpath = os.path.join(dest_dir, f"{h}{ext}")
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk: f.write(chunk)
        return fpath
    except Exception as e:
        return None


def is_main_container(tag: Tag) -> bool:
    if not isinstance(tag, Tag) or tag.name != "div":
        return False
    classes = tag.get("class", [])
    return "wp-block-bsaweb-blocks-grid-item" in classes and "col-8@md" in classes

def find_container(soup: BeautifulSoup) -> Tag | None:
    return soup.find(is_main_container)

def find_verification_block(container: Tag) -> Tag | None:

    h2 = container.find("h2", id="verification")
    if not h2:
        return None
    cur = h2
    while cur and isinstance(cur, Tag):
        if cur.name == "div":
            cls = cur.get("class", [])
            if "wp-block-group" in cls and "alignwide" in cls:
                return cur
        cur = cur.parent
    return h2

def find_references_heading(container: Tag) -> Tag | None:
    return container.find("h4", id="references")

def best_img_in_figure(fig: Tag, base: str):
    for pic in fig.find_all("picture"):
        for src in pic.find_all("source"):
            ss = src.get("srcset") or src.get("data-srcset")
            if ss:
                u = pick_largest_from_srcset(ss, base)
                if u:
                    return u
        im = pic.find("img")
        if im:
            u = im.get("src") or im.get("data-src") or im.get("data-original")
            if not u:
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
            if u:
                u = absurl(base, u)
                if u:
                    return u
    im = fig.find("img")
    if im:
        u = im.get("src") or im.get("data-src") or im.get("data-original")
        if not u:
            ss = im.get("srcset") or im.get("data-srcset")
            if ss:
                u = pick_largest_from_srcset(ss, base)
        if u:
            u = absurl(base, u)
            if u:
                return u
    return None

def is_inside_footer(node: Tag) -> bool:
    return bool(node.find_parent("footer"))

def prune_footers(root: Tag) -> None:

    for ft in root.find_all("footer"):
        ft.decompose()
    for extra in root.select("div.wp-block-group[style*='margin-block-start']"):
        txt = clean_text(extra.get_text(" "))
        if any(key in txt for key in ("Tags:", "Published on:", "Éditeur", "Editor:")):
            extra.decompose()


def extract_images_with_captions_scifeed_between_verification_and_references(article_url: str):

    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    base = r.url
    container = find_container(soup)
    if not container:
        return []

    prune_footers(container)

    start_marker = find_verification_block(container)
    if not start_marker:
        return []

    stop_marker = find_references_heading(container)

    figures, started = [], False
    for el in container.descendants:
        if not isinstance(el, Tag):
            continue
        if not started:
            if el is start_marker:
                started = True
            continue
        if stop_marker is not None and el is stop_marker:
            break
        if el.name == "figure":
            if is_inside_footer(el):
                continue
            figures.append(el)

    out, seen = [], set()
    for fig in figures:
        u = best_img_in_figure(fig, base)
        if not u:
            continue
        ext = get_ext(u)
        if ext and ext not in IMG_EXT:
            continue
        if u in seen:
            continue
        seen.add(u)
        cap = ""
        fc = fig.find("figcaption")
        if fc:
            cap = clean_text(fc.get_text(" "))
        out.append({"image_url": u, "caption": cap})

    return out


def enrich_dataframe_with_images_list(article_url,
                                      out_dir,
                                      download=True):

    images = []

    if article_url:
        items = extract_images_with_captions_scifeed_between_verification_and_references(article_url)
        if download and items:
            prefix = safe_slug(article_url)
            for it in items:
                path = download_image(it["image_url"], out_dir, f"{prefix}_img")
                image_name = os.path.basename(path) if path else ""
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", "") or "",
                    "path": image_name
                })
        else:
            for it in (items or []):
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", "") or "",
                    "path": ""
                })

    return images

def handle(review_url: str, location_info: str):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
    )
    out_df = pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
