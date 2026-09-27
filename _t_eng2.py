# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, re
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def fetch(u, hdrs, timeout=15):
    req = urllib.request.Request(u, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout).read()

VIDEO_DOMS = ("bilibili.com", "douyin.com", "ixigua.com", "youku.com", "iqiyi.com",
              "mgtv.com", "tv.sohu.com", "v.qq.com", "weibo.com", "kuaishou.com",
              "acfun.cn", "163.com", "icourse163.org", "mooc", "study.163.com",
              "open.163.com", "youtube.com", "vimeo.com", "pearvideo.com",
              "haokan.baidu.com", "v.baidu.com", "yidianzixun", "toutiao.com",
              "kankan.com", "miguvideo.com", "cctv.com", "cntv.cn", "qiyi",
              "letv.com", "pptv.com", "fun.tv", "58kan", "cnmooc")

def parse_bing(html):
    items = []
    for m in re.finditer(r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        body_t = re.sub(r"<[^>]+>", " ", m.group(3))
        body_t = re.sub(r"\s+", " ", body_t).strip()[:160]
        if url and title:
            items.append({"title": title[:120], "url": url, "summary": body_t})
    return items

def parse_baidu(html):
    items = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http") and title:
            items.append({"title": title[:120], "url": url, "summary": ""})
    return items

def parse_sogou(html):
    items = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http") and title:
            items.append({"title": title[:120], "url": url, "summary": ""})
    return items

kw = urllib.parse.quote("三体 电视剧")

# 1) Bing 网页
try:
    raw = fetch("https://cn.bing.com/search?q=" + kw + "&count=15", {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    its = parse_bing(raw.decode("utf-8", "ignore"))
    print("Bing:", len(its))
    for it in its[:8]:
        dom = urllib.parse.urlparse(it["url"]).netloc
        hit = any(v in it["url"] for v in VIDEO_DOMS)
        print("  ", ("[VID]" if hit else "[web]"), it["title"][:40], "|", dom)
except Exception as e:
    print("Bing ERR:", e)

# 2) 百度网页
try:
    raw = fetch("https://www.baidu.com/s?wd=" + kw + "&rn=15", {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    its = parse_baidu(raw.decode("utf-8", "ignore"))
    print("Baidu:", len(its))
    for it in its[:8]:
        dom = urllib.parse.urlparse(it["url"]).netloc
        hit = any(v in it["url"] for v in VIDEO_DOMS)
        print("  ", ("[VID]" if hit else "[web]"), it["title"][:40], "|", dom)
except Exception as e:
    print("Baidu ERR:", e)

# 3) Sogou 网页
try:
    raw = fetch("https://www.sogou.com/web?query=" + kw, {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    its = parse_sogou(raw.decode("utf-8", "ignore"))
    print("Sogou:", len(its))
    for it in its[:8]:
        dom = urllib.parse.urlparse(it["url"]).netloc
        hit = any(v in it["url"] for v in VIDEO_DOMS)
        print("  ", ("[VID]" if hit else "[web]"), it["title"][:40], "|", dom)
except Exception as e:
    print("Sogou ERR:", e)
