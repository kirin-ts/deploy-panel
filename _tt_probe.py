# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, re, json
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
kw = urllib.parse.quote("牧神记")
u = "https://so.toutiao.com/search?dvpf=pc&source=input&keyword=%s" % kw
req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "identity",
                                         "Referer": "https://so.toutiao.com/"})
body = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
print("页长:", len(body))
# 常见视频链接模式
for pat in [r'https?://www\.douyin\.com/[^"\'\s<>]+', r'https?://[^"\'\s<>]*(?:video|/v/)[^"\'\s<>]*',
            r'https?://www\.toutiao\.com[^"\'\s<>]+']:
    ms = re.findall(pat, body)
    print(pat[:30], "->", len(ms))
    for m in ms[:4]:
        print("   ", m[:90])
# 是否内嵌 JSON
for key in ["__INITIAL_STATE__", "__NEXT_DATA__", "window._INIT_", "videoData", "itemList"]:
    i = body.find(key)
    if i >= 0:
        print("含", key, "at", i)
        print(body[i:i+200].replace("\n", " "))
