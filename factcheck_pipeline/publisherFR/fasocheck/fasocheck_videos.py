
import re
import requests
from bs4 import BeautifulSoup
from html import unescape
from typing import List, Dict
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode, parse_qsl, unquote

Y_EMBED  = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})", re.I)
Y_WATCH  = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{6,})", re.I)

DM_EMBED = re.compile(r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)", re.I)
DM_WATCH = re.compile(r"https?://(?:www\.)?dailymotion\.com/video/([A-Za-z0-9]+)", re.I)

FB_PLUGIN = re.compile(r"https?://(?:www\.)?facebook\.com/plugins/video\.php\?[^\"'>]+", re.I)
FB_CANON  = re.compile(r"https?://(?:www\.)?facebook\.com/[^\"'>]+/videos/\d+", re.I)

ULT_IFRAME = re.compile(r"(?:https?:)?//(?:www\.)?ultimedia\.com/deliver/generic/iframe/[^\"'\s]+", re.I)

RAW_VIDEO = re.compile(r"https?://[^\"'>]+\.(?:mp4|m3u8)(?:\?[^\"'>]*)?", re.I)

def abs_http(u: str) -> str:
    return ("https:" + u) if u and u.startswith("//") else (u or "")

def strip_qs_frag(u: str) -> str:
    sp = urlsplit(u)
    return urlunsplit((sp.scheme, sp.netloc, sp.path, "", ""))

def add_query(u: str, extra: dict) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs.update(extra or {})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def dedupe_keep_shortest_prefix(urls):
    urls = sorted(set(urls), key=lambda x: (len(x), x))
    out = []
    for u in urls:
        if any(u.startswith(k) for k in out):
            continue
        out = [k for k in out if not k.startswith(u)]
        out.append(u)
    return out

def canon_youtube(u: str) -> str | None:
    u = abs_http(u)
    m = Y_EMBED.search(u) or Y_WATCH.search(u)
    if not m:
        return None
    vid = m.group(1)
    return add_query(f"https://www.youtube.com/embed/{vid}", {"autoplay": "1"})

def canon_dailymotion(u: str) -> str | None:
    u = abs_http(u)
    m = DM_EMBED.search(u) or DM_WATCH.search(u)
    return f"https://www.dailymotion.com/embed/video/{m.group(1)}" if m else None

def canon_facebook(u: str) -> str | None:
    u = abs_http(u)
    m = FB_CANON.search(u)
    if m:
        return strip_qs_frag(m.group(0))
    if not FB_PLUGIN.search(u):
        return None
    href = parse_qs(urlsplit(u).query).get("href", [None])[0]
    if href:
        href = unquote(href)
        m2 = FB_CANON.search(href)
        return strip_qs_frag(m2.group(0) if m2 else href)
    return strip_qs_frag(u)

def canon_ultimedia(u: str) -> str | None:
    u = abs_http(u)
    m = ULT_IFRAME.search(u)
    return strip_qs_frag(m.group(0)) if m else None

def extract_from_html(html: str) -> Dict[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    buckets = {
        "youtube": [],
        "dailymotion": [],
        "facebook": [],
        "ultimedia": [],
        "raw_video": [],
    }

    for ifr in soup.find_all("iframe"):
        src = abs_http((ifr.get("src") or "").strip())
        if not src:
            continue

        cu = (canon_youtube(src) or
              canon_dailymotion(src) or
              canon_facebook(src) or
              canon_ultimedia(src))
        if cu:
            if "youtube.com/embed/" in cu: buckets["youtube"].append(cu)
            elif "dailymotion.com/embed/" in cu: buckets["dailymotion"].append(cu)
            elif "facebook.com" in cu: buckets["facebook"].append(cu)
            elif "ultimedia.com" in cu: buckets["ultimedia"].append(cu)
        else:
            if RAW_VIDEO.search(src):
                buckets["raw_video"].append(strip_qs_frag(src))

    for vid in soup.find_all("video"):
        src = abs_http((vid.get("src") or "").strip())
        if src and RAW_VIDEO.search(src):
            buckets["raw_video"].append(strip_qs_frag(src))
        for s in vid.find_all("source"):
            ssrc = abs_http((s.get("src") or "").strip())
            if ssrc and RAW_VIDEO.search(ssrc):
                buckets["raw_video"].append(strip_qs_frag(ssrc))
        for a in vid.find_all("a", href=True):
            href = abs_http(a["href"].strip())
            if href and RAW_VIDEO.search(href):
                buckets["raw_video"].append(strip_qs_frag(href))

    text = unescape(soup.get_text(" ", strip=False) or "")
    for m in RAW_VIDEO.finditer(text):
        buckets["raw_video"].append(strip_qs_frag(abs_http(m.group(0))))

    for k, v in buckets.items():
        buckets[k] = dedupe_keep_shortest_prefix(v)

    return {k: v for k, v in buckets.items() if v}

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def extract_links(review_url: str) -> List[str]:
    try:
        html = fetch_html(review_url)
        buckets = extract_from_html(html)
        all_links = []
        for _, links in buckets.items():
            all_links.extend(links)
        return sorted(set(all_links))
    except Exception:
        return []

def handle(review_url: str,headless: bool = True) -> List[str]:
    return extract_links(review_url)
