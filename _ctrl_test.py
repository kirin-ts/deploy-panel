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
    return doms

# 1) 开启过滤
print('开过滤:', post('/api/media/nofilter', {'on': True}))
# 2) 基准搜索
d1 = search('牧神记')
print('基准域名分布:', {k: v for k, v in sorted(d1.items(), key=lambda x: -x[1])[:6]})
# 3) 添加 baike.baidu.com 到黑名单
r = post('/api/media/filters', {'list': 'black_dom', 'op': 'add', 'value': 'baike.baidu.com'})
print('添加黑名单:', r.get('ok'), len(r.get('filters', {}).get('black_dom', [])))
d2 = search('牧神记')
print('屏蔽后域名分布:', {k: v for k, v in sorted(d2.items(), key=lambda x: -x[1])[:6]})
has_bk = any('baike' in d for d in d2)
print('百科是否还在结果中:', has_bk)
# 4) 删除黑名单
r2 = post('/api/media/filters', {'list': 'black_dom', 'op': 'remove', 'value': 'baike.baidu.com'})
print('删除黑名单:', r2.get('ok'))
d3 = search('牧神记')
has_bk3 = any('baike' in d for d in d3)
print('恢复后百科是否回来:', has_bk3)
