# -*- coding: utf-8 -*-
import urllib.request, re
url = 'https://www.bing.com/videos/search?q=%E7%89%A7%E7%A5%9E%E8%AE%B0&setlang=zh-hans'
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept-Encoding': 'identity', 'Accept-Language': 'zh-CN,zh;q=0.9'})
html = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore')
# 视频卡结构：<a href="视频链接" ...><div class="v-title">标题</div>... 或 title 属性
# 先按卡切分
cards = re.split(r'<div class="v-card"', html)
print('cards:', len(cards))
out = []
for c in cards[1:6]:
    m = re.search(r'href="(https?://[^"]+)"', c)
    t = re.search(r'v-title[^>]*>([^<]+)', c)
    dur = re.search(r'duration[^>]*>([^<]+)', c)
    out.append((t.group(1).strip() if t else '', m.group(1) if m else '', dur.group(1).strip() if dur else ''))
for x in out: print(x)
# 备选：alt 属性（封面）
alts = re.findall(r'alt="([^"]{8,80})"', html)
print('alt sample:', alts[:8])
