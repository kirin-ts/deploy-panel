# -*- coding: utf-8 -*-
"""重注入模块块到 server.py"""
import os, py_compile
BASE = r"C:\Users\s\Doubao\chats\2026-09-26\new-chat-2\deploy-panel"
SRV = os.path.join(BASE, "server.py")
MOD = os.path.join(BASE, "_app_module.py.txt")
srv = open(SRV, encoding="utf-8").read()
mod = open(MOD, encoding="utf-8").read()
start = srv.find("# ---------- 应用搭建器（可视化搭前后端） ----------")
end = srv.find("# ---------- HTTP 服务")
assert start > 0 and end > start, "模块块锚点缺失"
srv = srv[:start] + mod + "\n" + srv[end:]
open(SRV, "w", encoding="utf-8", newline="").write(srv)
py_compile.compile(SRV, doraise=True)
print("server.py 已重注入，语法 OK，长度:", len(srv))
