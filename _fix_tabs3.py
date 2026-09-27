# -*- coding: utf-8 -*-
import io, os, re, subprocess
BASE = r"C:\Users\s\Doubao\chats\2026-09-26\new-chat-2\deploy-panel"
q = os.path.join(BASE, "app.html")
h = io.open(q, encoding="utf-8").read()

# 视频 panel 后 3 个 </div> 删 1（保留 ua-card + mt-panel-video 闭合）
old1 = '''          <div id="ua-vresults" style="margin-top:8px;display:flex;flex-direction:column;gap:6px"></div>
        </div>

        </div>
        </div>
        <div id="mt-panel-music" class="hidden">'''
new1 = '''          <div id="ua-vresults" style="margin-top:8px;display:flex;flex-direction:column;gap:6px"></div>
        </div>
        </div>
        <div id="mt-panel-music" class="hidden">'''
assert old1 in h, "视频尾部"
h = h.replace(old1, new1, 1)

# 看音乐段现状
i = h.find('id="mt-panel-music"')
j = h.find('id="mt-panel-book"')
print("音乐段:\n" + h[i:j])
