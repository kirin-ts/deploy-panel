# -*- coding: utf-8 -*-
import urllib.request, re, io
url = 'https://www.bing.com/videos/search?q=%E7%89%A7%E7%A5%9E%E8%AE%B0&setlang=zh-hans'
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept-Encoding': 'identity', 'Accept-Language': 'zh-CN,zh;q=0.9'})
html = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore')
print('len', len(html))
# 找视频条目：<a href="..." ...>标题</a> 或 json 数据
links = re.findall(r'href="(https?://[^"]+)"', html)
seen = set(); cnt = 0
for u in links:
    lu = u.lower()
    if any(k in lu for k in ['video', 'play', 'bilibili', 'youku', 'v.qq', 'iqiyi', 'mgtv',
                             'sohu', 'haokan', 'ixigua', '1905', 'cctv', 'douyin', 'kuaishou',
                             'acfun', 'pearvideo', '163.com', 'youtube', 'vimeo']):
        key = u.split('?')[0]
        if key in seen: continue
        seen.add(key); cnt += 1
        print(u[:110])
        if cnt >= 15: break
print('matched', cnt)
titles = re.findall(r'title="([^"]{6,80})"', html)
print('titles sample:', titles[:10])
