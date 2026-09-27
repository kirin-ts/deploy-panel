# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, re, json
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def fetch(u, hdrs, timeout=15):
    req = urllib.request.Request(u, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout).read()

kw = urllib.parse.quote("三体 电视剧")

# 1) Bing videos（视频垂直）
try:
    raw = fetch("https://cn.bing.com/videos/search?q=" + kw, {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    html = raw.decode("utf-8", "ignore")
    # 抓取视频卡片：title + url + 时长
    items = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*title="([^"]{4,120})"', html)
    seen = set()
    out = []
    for u2, t in items:
        if u2 in seen or "bing.com" in u2 or "microsoft" in u2 or "go.micro" in u2:
            continue
        seen.add(u2)
        out.append((t[:60], u2[:90]))
    print("=== Bing videos:", len(out))
    for t, u2 in out[:6]:
        print("  -", t, "|", u2)
except Exception as e:
    print("=== Bing videos ERR:", e)

# 2) 搜狗视频
try:
    raw = fetch("https://v.sogou.com/v?query=" + kw, {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://v.sogou.com/"})
    html = raw.decode("utf-8", "ignore")
    print("=== Sogou videos len:", len(html))
    items = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{4,120}?)</a>', html, re.S)
    seen = set()
    out = []
    for u2, t in items:
        t2 = re.sub(r"<[^>]+>", "", t).strip()
        if not t2 or u2 in seen or "sogou.com" in u2 or "microsoft" in u2:
            continue
        seen.add(u2)
        out.append((t2[:60], u2[:90]))
    print("  Sogou video links:", len(out))
    for t, u2 in out[:6]:
        print("  -", t, "|", u2)
except Exception as e:
    print("=== Sogou videos ERR:", e)

# 3) Yandex videos
try:
    raw = fetch("https://yandex.com/video/search?text=" + kw, {"User-Agent": UA, "Accept-Language": "en,zh-CN;q=0.8"})
    html = raw.decode("utf-8", "ignore")
    print("=== Yandex videos len:", len(html))
    items = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{4,120}?)</a>', html, re.S)
    seen = set()
    out = []
    for u2, t in items:
        t2 = re.sub(r"<[^>]+>", "", t).strip()
        if not t2 or u2 in seen or "yandex" in u2 or "yabs" in u2:
            continue
        seen.add(u2)
        out.append((t2[:60], u2[:90]))
    print("  Yandex video links:", len(out))
    for t, u2 in out[:6]:
        print("  -", t, "|", u2)
except Exception as e:
    print("=== Yandex videos ERR:", e)
