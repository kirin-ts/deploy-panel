# -*- coding: utf-8 -*-
"""诊断：搜索每个环节的返回/剔除统计，回答"到底什么在阻止搜索" """
import sys, io, os, urllib.parse
DP = r"C:\Users\s\Doubao\chats\2026-09-26\new-chat-2\deploy-panel"
sys.path.insert(0, DP)
import server

def diagnose(q, nofilter):
    # 引擎计数
    est = {}
    def wrap(name, fn):
        def w(*a):
            r = fn(*a); est[name] = len(r); return r
        return w
    server._web_engine_bing_videos = wrap('bing_videos', server._web_engine_bing_videos)
    server._web_engine_bing = wrap('bing_web', server._web_engine_bing)
    server._web_engine_baidu = wrap('baidu', server._web_engine_baidu)
    server._web_engine_sogou = wrap('sogou', server._web_engine_sogou)
    server._web_engine_360 = wrap('360', server._web_engine_360)
    server._web_engine_site = wrap('free_sites', server._web_engine_site)

    raw = []
    for eng in (server._web_engine_bing_videos, server._web_engine_bing,
                server._web_engine_baidu, server._web_engine_sogou, server._web_engine_360):
        try: raw += eng(q)
        except Exception as e: est.setdefault('ERR_'+eng.__name__, str(e)[:40])
    for site in server._WEB_FREE_SITES:
        try: raw += server._web_engine_site(q, site)
        except Exception as e: est.setdefault('ERR_site_'+site, str(e)[:40])

    cut = {"black_dom": 0, "pirate": 0, "black_title": 0, "paid": 0, "junk": 0,
           "not_video": 0, "dup": 0, "quota10": 0, "cap30": 0}
    seen, items = set(), []
    for it in raw:
        url = it["url"]; low = url.lower()
        if not nofilter:
            if any(b in low for b in server._WEB_BLACK_DOM): cut["black_dom"] += 1; continue
            if any(d in low for d in server._WEB_PIRATE_DOMS): cut["pirate"] += 1; continue
            if any(k in it["title"] for k in server._WEB_BLACK_TITLE): cut["black_title"] += 1; continue
        key = url.split("&")[0]
        if key in seen: cut["dup"] += 1; continue
        seen.add(key)
        dom = (urllib.parse.urlparse(url).netloc or "").replace("www.", "")
        if not nofilter:
            if any(d in low for d in server._WEB_PAID_DOMS): cut["paid"] += 1; continue
            if any(k in it["title"] for k in server._WEB_JUNK_TITLE): cut["junk"] += 1; continue
            if not server._is_video_url(low): cut["not_video"] += 1; continue
        dom_cnt = {}
        for _x in items: dom_cnt[_x["domain"]] = dom_cnt.get(_x["domain"], 0) + 1
        if dom_cnt.get(dom, 0) >= 10: cut["quota10"] += 1; continue
        items.append({"title": it["title"][:40], "url": url, "domain": dom[:30],
                      "level": 2 if server._is_video_url(low) else 1})
        if len(items) >= 30: cut["cap30"] = 1; break

    print("===== %s | nofilter=%s =====" % (q, nofilter))
    print("引擎原始返回:", est)
    print("融合后剔除明细:", cut)
    print("最终输出: %d 条" % len(items))
    doms = {}
    for it in items: doms[it["domain"]] = doms.get(it["domain"], 0) + 1
    print("域名分布:", {k: v for k, v in sorted(doms.items(), key=lambda x: -x[1])[:8]})
    return items

# 用代表性词诊断两种状态
diagnose("牧神记", nofilter=True)   # 过滤开（用户看到的限制状态）
print()
diagnose("牧神记", nofilter=False)  # 调试模式对照
