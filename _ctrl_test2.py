# -*- coding: utf-8 -*-
import urllib.request, json
from urllib.parse import urlparse
base = 'http://127.0.0.1:8787'

def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def search(q):
    j = post('/api/media/websearch', {'q': q})
    doms = {}
    for it in j.get('items', []):
        d = urlparse(it['url']).netloc.replace('www.', '')
        doms[d] = doms.get(d, 0) + 1
    return doms, j.get('items', [])

# 1) 基准（过滤开）
d1, items1 = search('牧神记')
print('1基准:', {k: v for k, v in sorted(d1.items(), key=lambda x: -x[1])[:6]})
# 2) 屏蔽抖音（把 douyin.com 加进 black_dom）
r = post('/api/media/filters', {'list': 'black_dom', 'op': 'add', 'value': 'douyin.com'})
print('2添加douyin黑名单 ok=%s n=%s' % (r.get('ok'), len(r.get('filters', {}).get('black_dom', []))))
d2, items2 = search('牧神记')
print('2屏蔽后:', {k: v for k, v in sorted(d2.items(), key=lambda x: -x[1])[:6]})
print('  抖音还在吗:', any('douyin' in d for d in d2), '总条数:', len(items2))
# 3) 删除黑名单恢复
r = post('/api/media/filters', {'list': 'black_dom', 'op': 'remove', 'value': 'douyin.com'})
d3, items3 = search('牧神记')
print('3恢复后抖音回来吗:', any('douyin' in d for d in d3))
# 4) 开放指定网站：从 video_doms 移除 bilibili → B站应消失
r = post('/api/media/filters', {'list': 'video_doms', 'op': 'remove', 'value': 'bilibili.com'})
print('4移除bilibili白名单 ok=%s' % r.get('ok'))
d4, items4 = search('牧神记')
print('4白名单移除后B站还在吗:', any('bilibili' in d for d in d4))
# 5) 恢复 bilibili
r = post('/api/media/filters', {'list': 'video_doms', 'op': 'add', 'value': 'bilibili.com'})
d5, items5 = search('牧神记')
print('5恢复白名单后B站回来吗:', any('bilibili' in d for d in d5))
