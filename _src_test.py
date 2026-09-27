# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, json, re, io, sys
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
kw = urllib.parse.quote("牧神记")

# 1) 百度带 cookie
ck = "BAIDUID=2E7F9C4D1B3A5C6E7F9C4D1B3A5C6E7:FG=1; BIDUPSID=2E7F9C4D1B3A5C6E7F9C4D1B3A5C6E7; PSTM=1700000000"
u = "https://www.baidu.com/s?wd=%s&rn=15" % kw
try:
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Cookie": ck,
                                             "Accept-Language": "zh-CN,zh;q=0.9",
                                             "Accept-Encoding": "identity"})
    r = urllib.request.urlopen(req, timeout=15)
    body = r.read().decode("utf-8", "ignore")
    m = re.findall(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    print("百度cookie: status=%s 命中=%d 页长=%d" % (r.status, len(m), len(body)))
    for x in m[:2]:
        print("  ", re.sub(r"<[^>]+>", "", x[1])[:40], x[0][:60])
except Exception as e:
    print("百度cookie失败:", repr(e)[:120])

# 2) 头条搜索
try:
    u2 = "https://so.toutiao.com/search?dvpf=pc&source=input&keyword=%s" % kw
    req2 = urllib.request.Request(u2, headers={"User-Agent": UA,
                                               "Accept": "application/json, text/plain, */*",
                                               "Accept-Encoding": "identity",
                                               "Referer": "https://so.toutiao.com/"})
    r2 = urllib.request.urlopen(req2, timeout=15)
    raw = r2.read()
    body2 = raw.decode("utf-8", "ignore")
    print("头条: status=%s 长度=%d" % (r2.status, len(body2)))
    print("  头部:", body2[:300].replace("\n", " "))
    try:
        j = json.loads(body2)
        data = j.get("data", [])
        print("  JSON data条数:", len(data))
        for d in data[:5]:
            t = d.get("title") or d.get("display_title")
            url = d.get("display_url") or d.get("url") or d.get("share_url")
            v = d.get("is_video") or d.get("video_id")
            print("   ", (re.sub(r"<[^>]+>", "", t or ""))[:40], "|", (url or "")[:60], "|video:", v)
    except Exception as e:
        print("  非JSON:", repr(e)[:80])
except Exception as e:
    print("头条失败:", repr(e)[:120])
