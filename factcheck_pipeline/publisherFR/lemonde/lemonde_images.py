
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lemonde-img-scraper/1.1)"}
IMG_EXT  = (".jpg", ".jpeg", ".png", ".webp", ".avif")

ARTICLE_SEL = "article.article__content.old__article-content-single"


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

def clean_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def pick_largest_from_srcset(srcset, base):
    best_u, best_w, last_u = None, -1, None
    for part in str(srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        u = absurl(base, bits[0])
        last_u = u or last_u
        w = -1
        if len(bits) > 1 and bits[1].endswith("w"):
            try: w = int(bits[1][:-1])
            except: w = -1
        if u and w > best_w:
            best_u, best_w = u, w
    return best_u or last_u

def safe_slug(s, length=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s)
    s = s.strip("._")
    return s[-length:] if len(s) > length else (s or "article")

def prefer_larger(u1, u2):
    def score(u):
        m = re.search(r'(\d{3,4})w(?:\D|$)', u)
        if m: return int(m.group(1))
        m = re.search(r'/fit-in/(\d{3,4})x', u)
        if m: return int(m.group(1))
        nums = [int(n) for n in re.findall(r'/(\d{3,4})(?:/|$)', urlsplit(u).path)]
        return max(nums) if nums else 0
    return u1 if score(u1) >= score(u2) else u2


def extract_images_with_captions(article_url):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[fetch error] {article_url}: {e}")
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except:
        soup = BeautifulSoup(r.text, "html.parser")

    base = r.url
    art = soup.select_one(ARTICLE_SEL) or soup.select_one("article.article__content") or soup

    out = []
    figures = list(art.select("figure"))

    for fig in figures:
        caption, credit = "", ""
        fc = fig.find("figcaption", class_="article__legend") or fig.find("figcaption")
        if fc:
            credit_el = fc.select_one(".article__credit")
            credit = clean_text(credit_el.get_text(" ")) if credit_el else ""
            caption = clean_text(fc.get_text(" "))

        best_url = None

        for pic in fig.find_all("picture"):
            for src in pic.find_all("source"):
                u = pick_largest_from_srcset(src.get("srcset") or src.get("data-srcset"), base)
                if u:
                    best_url = u if not best_url else prefer_larger(best_url, u)
            im = pic.find("img")
            if im:
                u = None
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
                if not u:
                    u = absurl(base, im.get("src") or im.get("data-src") or im.get("data-original"))
                if u:
                    best_url = u if not best_url else prefer_larger(best_url, u)

        if not best_url:
            im = fig.find("img")
            if im:
                u = None
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
                if not u:
                    u = absurl(base, im.get("src") or im.get("data-src") or im.get("data-original"))
                if u:
                    best_url = u

        if not best_url:
            for ns in fig.find_all("noscript"):
                try:
                    tmp = BeautifulSoup(ns.decode_contents(), "html.parser")
                    im = tmp.find("img")
                    if im:
                        u = absurl(base, im.get("src"))
                        if u:
                            best_url = u
                            break
                except:
                    pass

        if best_url and (not get_ext(best_url) or get_ext(best_url) in IMG_EXT):
            out.append({"image_url": best_url, "caption": caption})

    seen, uniq = set(), []
    for it in out:
        u = it["image_url"]
        if u in seen:
            continue
        seen.add(u)
        uniq.append(it)
    return uniq


def download_image(url, dest_dir, prefix):
    if not url:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, stream=True)
        r.raise_for_status()
        ext = get_ext(url)
        if not ext or ext not in IMG_EXT:
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if "jpeg" in ct: ext = ".jpg"
            elif "png" in ct: ext = ".png"
            elif "webp" in ct: ext = ".webp"
            elif "avif" in ct: ext = ".avif"
            else: ext = ".jpg"
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        fpath = os.path.join(dest_dir, f"{h}{ext}")
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)
        return fpath
    except Exception as e:
        print(f"[download error] {url}: {e}")
        return None

def enrich_dataframe_with_images_list(article_url,
                                      out_dir,
                                      download=True,
                                      store_json=True):

    images = []

    if article_url:
        items = extract_images_with_captions(article_url)
        if download and items:
            prefix = safe_slug(article_url)
            for it in items:
                path = download_image(it["image_url"], out_dir, f"{prefix}_img")
                image_name = os.path.basename(path)
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", ""),
                    "path": image_name
                })
        else:
            images = items
    return images


def handle(review_url: str, location_info: str):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
        store_json=True,
    )
    out_df =pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
