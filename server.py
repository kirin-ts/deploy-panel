# -*- coding: utf-8 -*-
"""
DeployPanel · GitHub 项目一键部署面板（本地服务）
# Copyright (C) 2026 kirin-ts
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version. See LICENSE for details.
核心为 Python 3.8+ 标准库实现（零第三方硬依赖）；通用视频下载可选用 yt-dlp（pip install yt-dlp，GPL-3.0 兼容）。
功能：GitHub 搜索 / 许可证识别 / GPL 律师审核 / 下载解压 / 环境自动检测 / 一键部署 / 环境打包带走 / 离线模式 / 通用视频下载
启动：python server.py  →  浏览器打开 http://127.0.0.1:8787
"""
import json, os, re, io, sys, time, zipfile, shutil, subprocess, threading, sqlite3, urllib.request, urllib.parse
try:
    import yt_dlp as _yt_dlp
    YTDLP_AVAILABLE = True
except Exception:
    _yt_dlp = None
    YTDLP_AVAILABLE = False
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
WORKSPACES = os.path.join(BASE, "workspaces")
KB = os.path.join(BASE, "gpl_kb")
LOGS = os.path.join(BASE, "logs")
PORT = 8787
UA = {"User-Agent": "DeployPanel/1.0 (local tool)"}
os.makedirs(DATA, exist_ok=True); os.makedirs(WORKSPACES, exist_ok=True); os.makedirs(LOGS, exist_ok=True)

# ---------- 工具 ----------
def log(msg):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg)
    print(line, flush=True)
    try:
        with open(os.path.join(LOGS, "server.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def http_get(url, timeout=15, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()

def config():
    return load_json(os.path.join(DATA, "config.json"), {})

# ---------- 离线知识库 ----------
LICENSES = load_json(os.path.join(KB, "licenses.json"), {}).get("licenses", [])
COMPLIANCE = load_json(os.path.join(KB, "compliance_rules.json"), {})
RESTRICTIONS = load_json(os.path.join(KB, "restriction_keywords.json"), {})

def find_license(license_id):
    license_id = (license_id or "").strip()
    for lic in LICENSES:
        if lic["id"].lower() == license_id.lower() or license_id.lower() in [a.lower() for a in lic.get("aliases", [])]:
            return lic
    if license_id in ("NOASSERTION", "OTHER", "SEE LICENSE IN", "View license"):
        return find_license("UNKNOWN")
    return None

# ---------- GitHub 搜索（联网 + 离线缓存） ----------
CACHE_FILE = os.path.join(DATA, "search_cache.json")

def github_search(query, page=1):
    cache = load_json(CACHE_FILE, {"items": [], "ts": 0})
    headers = {"Accept": "application/vnd.github+json"}
    try:
        url = "https://api.github.com/search/repositories?q=" + urllib.parse.quote(query) + "&per_page=20&page=" + str(page)
        status, body = http_get(url, timeout=20, headers=headers)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            items = [{
                "full_name": it["full_name"], "description": it.get("description") or "",
                "stars": it.get("stargazers_count", 0), "forks": it.get("forks_count", 0),
                "language": it.get("language") or "", "license": (it.get("license") or {}).get("spdx_id") or "",
                "owner": it.get("owner", {}).get("login", ""), "html_url": it.get("html_url", ""),
                "updated_at": it.get("updated_at", ""), "default_branch": it.get("default_branch", "master"),
            } for it in data.get("items", [])]
            cache = {"items": items, "query": query, "ts": int(time.time()), "source": "github"}
            save_json(CACHE_FILE, cache)
            return {"ok": True, "source": "github", "items": items, "total": data.get("total_count", 0)}
        return {"ok": False, "error": "GitHub API 返回 " + str(status), "items": []}
    except Exception as e:
        return {"ok": False, "error": "联网失败：" + str(e), "items": []}

def github_search_offline(query):
    cache = load_json(CACHE_FILE, {"items": []})
    q = query.lower().strip()
    items = cache.get("items", [])
    if q and cache.get("query", "").lower() != q:
        items = [it for it in items if q in it["full_name"].lower() or q in (it.get("description") or "").lower()]
    return {"ok": True, "source": "offline-cache", "items": items, "total": len(items), "offline": True}

# ---------- 仓库详情 + 许可证/作者限制识别 ----------
LICENSE_CACHE_DIR = os.path.join(DATA, "license_cache")
os.makedirs(LICENSE_CACHE_DIR, exist_ok=True)

def fetch_raw(owner, repo, path, branch="master"):
    headers = {}
    for br in [branch, "main", "master"]:
        for p in [path, path + ".md", path + ".txt"]:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{p}"
            try:
                status, body = http_get(url, timeout=12, headers=headers)
                if status == 200:
                    return body.decode("utf-8", "replace")
            except Exception:
                pass
    return None

def fetch_repo_meta(full_name):
    owner, repo = full_name.split("/", 1)
    headers = {"Accept": "application/vnd.github+json"}
    try:
        status, body = http_get(f"https://api.github.com/repos/{owner}/{repo}", timeout=15, headers=headers)
        if status == 200:
            it = json.loads(body.decode("utf-8", "replace"))
            return {
                "full_name": it["full_name"], "description": it.get("description") or "",
                "stars": it.get("stargazers_count", 0), "license": (it.get("license") or {}).get("spdx_id") or "",
                "language": it.get("language") or "", "default_branch": it.get("default_branch", "master"),
                "owner": it.get("owner", {}).get("login", ""), "html_url": it.get("html_url", ""),
                "created_at": it.get("created_at", ""), "updated_at": it.get("updated_at", ""),
                "archived": it.get("archived", False), "forks": it.get("forks_count", 0),
            }
    except Exception as e:
        return {"full_name": full_name, "error": str(e)}
    return {"full_name": full_name}

def identify_restrictions(text):
    hits = []
    if not text:
        return hits
    low = text.lower()
    for group in RESTRICTIONS.get("restriction_keywords", []):
        for kw in group["kw"]:
            if kw.lower() in low:
                hits.append({"keyword": kw, "zh": group["zh"], "level": group["level"], "hint": group["hint"]})
                break
    return hits

def license_from_text(text):
    """离线识别 LICENSE 文本 → SPDX 标识（不联网）"""
    low = (text or "").lower()
    probes = [
        ("agpl-3.0", ["gnu affero general public license"]),
        ("lgpl-3.0", ["gnu lesser general public license"]),
        ("gpl-3.0", ["gnu general public license version 3", "gnu general public license v3", "gpl version 3"]),
        ("gpl-2.0", ["gnu general public license version 2", "gnu general public license v2", "gpl version 2", "gnu general public license, version 2"]),
        ("apache-2.0", ["apache license", "version 2.0, january 2004"]),
        ("mit", ["permission is hereby granted, free of charge"]),
        ("bsd-3-clause", ["redistribution and use in source and binary forms"]),
        ("mpl-2.0", ["mozilla public license version 2.0"]),
        ("cc0-1.0", ["creative commons legal code cc0"]),
    ]
    for sid, keys in probes:
        if any(k in low for k in keys):
            return sid
    if "all rights reserved" in low and "permission" not in low:
        return "UNLICENSED"
    return "UNKNOWN"

def repo_analysis(full_name):
    """整合：联网拉取元数据+LICENSE+README → 许可证识别 + 作者限制识别 + 中文解读"""
    meta = fetch_repo_meta(full_name)
    owner, repo = full_name.split("/", 1)
    branch = meta.get("default_branch", "master")
    license_text = None
    cache_path = os.path.join(LICENSE_CACHE_DIR, full_name.replace("/", "__") + ".txt")
    if os.path.exists(cache_path):
        license_text = open(cache_path, "r", encoding="utf-8", errors="replace").read()
    if license_text is None:
        license_text = fetch_raw(owner, repo, "LICENSE", branch)
        if license_text:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(license_text)
    readme = fetch_raw(owner, repo, "README", branch)
    readme = (readme or "")[:60000]
    # 许可证判定：优先 GitHub 元数据 spdx，其次离线文本识别
    spdx = meta.get("license", "") or ""
    license_id = spdx if find_license(spdx) else (license_from_text(license_text) if license_text else "UNKNOWN")
    lic = find_license(license_id) or find_license("UNKNOWN")
    restrictions = identify_restrictions(readme)
    return {
        "meta": meta,
        "license": {"id": license_id, "zh_name": lic["zh_name"], "type": lic["type"],
                    "open_source": lic["open_source"], "can_modify": lic["can_modify"],
                    "can_commercial": lic["can_commercial"], "can_close": lic["can_close"],
                    "must_provide_source": lic["must_provide_source"],
                    "obligations": lic["obligations"], "risk_note": lic["risk_note"], "notes": lic["notes"]},
        "restrictions": restrictions,
        "readme_snippet": readme[:1200],
        "online": bool(license_text or meta.get("stars") is not None),
    }

# ---------- GPL 律师审核引擎 ----------
def run_audit(full_name):
    analysis = repo_analysis(full_name)
    lic = analysis["license"]
    restrictions = analysis["restrictions"]
    verdict = "low"
    checks = []
    # 基础：未授权/未识别 → 禁止部署
    if lic["id"] in ("UNLICENSED", "UNKNOWN", "Proprietary"):
        verdict = "high"
        checks.append({"item": "许可证状态：" + lic["zh_name"], "rule": "未获授权前禁止部署", "level": "must"})
    # GPL 家族 → 完整合规清单（默认场景：部署运行）
    scenario_key = "internal_use"
    scenario = None
    for s in COMPLIANCE.get("scenarios", []):
        if s["id"] == scenario_key:
            scenario = s
    if scenario:
        for c in scenario["checks"]:
            checks.append(c)
        sv = scenario["verdict"]
        if sv == "high" and verdict != "critical":
            verdict = "high"
    # 作者限制 → 命中红色直接禁止
    for r in restrictions:
        if r["level"] == "red":
            verdict = "high"
            checks.append({"item": "作者限制：" + r["zh"], "rule": r["hint"], "level": "must"})
        else:
            checks.append({"item": "作者提示：" + r["zh"], "rule": r["hint"], "level": "warn"})
    # 汇总
    level_meta = COMPLIANCE.get("levels", {}).get(verdict, {"zh": "🟢 低风险", "deploy_allowed": True})
    report = {
        "repo": full_name,
        "license": lic,
        "restrictions": restrictions,
        "verdict": verdict,
        "verdict_zh": level_meta["zh"],
        "deploy_allowed": level_meta["deploy_allowed"],
        "checks": checks,
        "analysis": analysis,
        "ts": int(time.time()),
        "auditor": "GPL 律师（离线知识库 v1.0）",
    }
    # 生成中文审核报告落盘
    report_dir = os.path.join(DATA, "audits")
    os.makedirs(report_dir, exist_ok=True)
    md = build_audit_md(report)
    report_path = os.path.join(report_dir, full_name.replace("/", "__") + ".md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)
    report["report_path"] = report_path
    return report

def build_audit_md(r):
    lic = r["license"]
    lines = []
    lines.append("# GPL 合规审核报告（离线律师引擎）")
    lines.append("")
    lines.append("**仓库**：" + r["repo"])
    lines.append("**结论**：" + r["verdict_zh"])
    lines.append("**许可证**：" + lic["zh_name"] + "（" + lic["id"] + "）")
    lines.append("**审核时间**：" + time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    lines.append("## 一、许可证解读（中文）")
    lines.append("")
    lines.append("- 是否开源：" + ("是" if lic["open_source"] else "否"))
    lines.append("- 是否允许修改：" + ("是" if lic["can_modify"] else "否"))
    lines.append("- 是否允许商用：" + ("是" if lic["can_commercial"] else "否"))
    lines.append("- 是否允许闭源分发：" + ("是" if lic["can_close"] else "否"))
    lines.append("- 是否必须提供源码：" + ("是" if lic["must_provide_source"] else "否"))
    lines.append("- 风险提示：" + lic.get("risk_note", ""))
    lines.append("")
    lines.append("**须履行的义务**：")
    for ob in lic.get("obligations", []):
        lines.append("- " + ob)
    lines.append("")
    lines.append("## 二、作者使用限制（README 自动识别）")
    lines.append("")
    if r["restrictions"]:
        for x in r["restrictions"]:
            lines.append("- 【" + x["level"] + "】" + x["zh"] + "：" + x["hint"])
    else:
        lines.append("- 未在 README 中识别到明显限制声明（不代表无限制，请以 LICENSE 与官方文档为准）")
    lines.append("")
    lines.append("## 三、合规检查清单")
    lines.append("")
    for c in r["checks"]:
        lines.append("- [" + ("x" if c["level"] == "must" else " ") + "] " + c["item"] + " ｜ 依据：" + c["rule"])
    lines.append("")
    lines.append("## 四、结论与部署建议")
    lines.append("")
    lines.append("部署许可：" + ("✅ 允许部署" if r["deploy_allowed"] else "🚫 禁止部署") + "（" + r["verdict_zh"] + "）")
    if not r["deploy_allowed"]:
        lines.append("建议：停止部署。如需使用，请先联系作者获取书面授权，或更换同类开源项目。")
    lines.append("")
    lines.append("---")
    lines.append("本报告由本地离线 GPL 律师引擎生成，基于内置知识库（gpl-legal-counsel）；不构成正式法律意见，重大决策请咨询执业律师。")
    return "\n".join(lines)

# ---------- 下载 & 安全解压 ----------
def download_repo(full_name):
    owner, repo = full_name.split("/", 1)
    safe = full_name.replace("/", "__").replace("\\", "_").replace(":", "_")
    dest = os.path.join(WORKSPACES, safe)
    if os.path.isdir(dest) and os.listdir(dest):
        return {"ok": True, "path": dest, "cached": True}
    os.makedirs(dest, exist_ok=True)
    zip_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/main"
    tmp = os.path.join(WORKSPACES, safe + ".zip")
    headers = {"User-Agent": UA["User-Agent"]}
    ok = False
    for br in ["main", "master"]:
        url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{br}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f)
            if os.path.getsize(tmp) > 1000:
                ok = True
                break
        except Exception as e:
            log("download branch " + br + " fail: " + str(e))
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": "仓库下载失败（可能不存在、网络不可达或超时）"}
    # 安全解压：防路径穿越
    with zipfile.ZipFile(tmp) as z:
        root = None
        for name in z.namelist():
            parts = name.replace("\\", "/").split("/")
            if not root:
                root = parts[0] if parts else None
        for name in z.namelist():
            norm = name.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/") or re.match(r"^[a-zA-Z]:", norm):
                continue
            target = os.path.join(dest, norm)
            if not os.path.abspath(target).startswith(os.path.abspath(dest)):
                continue
            if norm.endswith("/"):
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    # 若解压出单层目录（GitHub zip 惯例：外层套 <repo>-<branch>），上移一层
    inner = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
    if len(inner) == 1 and os.listdir(dest).count(inner[0]) == 1:
        sub = os.path.join(dest, inner[0])
        for item in os.listdir(sub):
            shutil.move(os.path.join(sub, item), os.path.join(dest, item))
        shutil.rmtree(sub, ignore_errors=True)
    os.remove(tmp)
    return {"ok": True, "path": dest, "cached": False}

# ---------- 环境自动检测 ----------
def detect_env(project_path):
    found = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build", "__pycache__")]
        depth = root[len(project_path):].count(os.sep)
        if depth > 2:
            continue
        for f in files:
            found.append(os.path.join(root, f))
    names = [f.lower().replace("\\", "/") for f in found]
    env = {"type": "static", "zh": "静态站点", "runtime": None, "deps": [], "start_hint": "python -m http.server 8080", "icon": "🌐", "files": []}
    if any(n.endswith("package.json") for n in names):
        env = {"type": "node", "zh": "Node.js", "runtime": "Node.js (需安装)", "start_hint": "npm install && npm start", "icon": "🟢"}
        for f in found:
            if f.lower().endswith("package.json"):
                try:
                    pj = json.load(open(f, "r", encoding="utf-8"))
                    scripts = pj.get("scripts", {})
                    env["start_hint"] = ("npm run " + (scripts.get("start", "dev") if "start" in scripts else list(scripts.keys())[0])) if scripts else "npm start"
                    env["deps"] = list(pj.get("dependencies", {}).keys())[:12]
                except Exception:
                    pass
    elif any(n.endswith(("requirements.txt", "pyproject.toml", "setup.py")) for n in names):
        env = {"type": "python", "zh": "Python", "runtime": "Python (需安装)", "start_hint": "pip install -r requirements.txt && python main.py", "icon": "🐍"}
        for f in found:
            if f.lower().endswith("requirements.txt"):
                env["deps"] = [l.strip().split("==")[0] for l in open(f, "r", encoding="utf-8", errors="replace").read().splitlines() if l.strip() and not l.startswith("#")][:15]
    elif any(n.endswith("composer.json") for n in names):
        env = {"type": "php", "zh": "PHP", "runtime": "PHP (需安装)", "start_hint": "composer install && php -S localhost:8080", "icon": "🐘"}
    elif any(n.endswith("pom.xml") or n.endswith("build.gradle") for n in names):
        env = {"type": "java", "zh": "Java", "runtime": "Java (需安装)", "start_hint": "mvn spring-boot:run 或 mvn package && java -jar target/*.jar", "icon": "☕"}
    elif any(n.endswith("go.mod") for n in names):
        env = {"type": "go", "zh": "Go", "runtime": "Go (需安装)", "start_hint": "go run .", "icon": "🐹"}
    elif any(n.endswith("dockerfile") or n.endswith("docker-compose.yml") or n.endswith("docker-compose.yaml") for n in names):
        env = {"type": "docker", "zh": "Docker", "runtime": "Docker (需安装)", "start_hint": "docker compose up -d", "icon": "🐳"}
    elif any(n.endswith(".csproj") for n in names):
        env = {"type": "dotnet", "zh": ".NET", "runtime": ".NET SDK (需安装)", "start_hint": "dotnet run", "icon": "🔷"}
    env["files"] = [os.path.relpath(f, project_path) for f in found if f.lower().endswith(("package.json", "requirements.txt", "pyproject.toml", "setup.py", "composer.json", "pom.xml", "build.gradle", "go.mod", "dockerfile", "docker-compose.yml", "docker-compose.yaml", ".csproj", "index.html", "main.py", "app.py", "server.py", "manage.py"))][:20]
    return env

# ---------- 部署 ----------
def write_script(project_path, env):
    if env["type"] == "node":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\necho [DeployPanel] 安装依赖...\r\ncall npm install\r\necho [DeployPanel] 启动服务...\r\ncall npm start\r\npause\r\n"
    elif env["type"] == "python":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\necho [DeployPanel] 安装依赖...\r\npip install -r requirements.txt\r\necho [DeployPanel] 启动服务...\r\npython main.py\r\npause\r\n"
    elif env["type"] == "php":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\nphp -S 0.0.0.0:8080\r\npause\r\n"
    elif env["type"] == "java":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\nmvn spring-boot:run\r\npause\r\n"
    elif env["type"] == "go":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\ngo run .\r\npause\r\n"
    elif env["type"] == "docker":
        script = "@echo off\r\ncd /d \"%~dp0\"\r\ndocker compose up -d\r\npause\r\n"
    else:
        script = "@echo off\r\ncd /d \"%~dp0\"\r\necho [DeployPanel] 静态站点已就绪，访问 http://localhost:8080\r\npython -m http.server 8080\r\npause\r\n"
    with open(os.path.join(project_path, "start_这里一键启动.bat"), "w", encoding="gbk", errors="ignore") as f:
        f.write(script)
    return os.path.join(project_path, "start_这里一键启动.bat")

def deploy_project(full_name, project_path, env):
    script_path = write_script(project_path, env)
    deploy_cfg = {
        "repo": full_name, "path": project_path, "env": env, "script": script_path,
        "ts": int(time.time()), "status": "ready",
    }
    records = load_json(os.path.join(DATA, "deployments.json"), {"items": []})
    records.setdefault("items", []).insert(0, deploy_cfg)
    save_json(os.path.join(DATA, "deployments.json"), records)
    return deploy_cfg

# ---------- 打包带走 ----------
def export_package(full_name, project_path, env, audit):
    safe = full_name.replace("/", "__").replace("\\", "_")
    out_dir = os.path.join(DATA, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out_zip = os.path.join(out_dir, safe + "_环境包.zip")
    if os.path.exists(out_zip):
        os.remove(out_zip)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__")]
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, project_path)
                z.write(full, os.path.join("项目代码", rel))
        # 部署配置
        cfg = {"仓库": full_name, "环境类型": env["zh"], "启动命令": env["start_hint"],
               "依赖": env.get("deps", []), "说明": "解压后双击「start_这里一键启动.bat」即可运行；需预装对应运行时。"}
        z.writestr("部署配置 deploy-config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
        # 审核报告
        if audit:
            z.write(audit.get("report_path", ""), "GPL审核报告.md") if os.path.exists(audit.get("report_path", "")) else z.writestr("GPL审核报告.md", build_audit_md(audit))
        z.writestr("使用说明-中文.md", (
            "# 部署环境包（由 DeployPanel 导出）\n\n"
            "1. 解压本包到目标电脑\n2. 确保目标电脑已安装所需运行时（见 deploy-config.json 的环境类型）\n"
            "3. 双击「start_这里一键启动.bat」启动\n4. 浏览器访问 http://localhost:8080\n\n"
            "⚠️ 使用前请先阅读「GPL审核报告.md」，确认你的用途符合项目许可证与作者要求。\n"))
    return out_zip

def in_workspaces(path):
    safe = os.path.abspath(WORKSPACES)
    real = os.path.abspath(path)
    return real.startswith(safe + os.sep) and real != safe

def api_open_folder(params):
    path = params.get("path", "")
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许打开 DeployPanel 工作区内的目录"}
    try:
        os.startfile(path)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": "打开失败：" + str(e)}

def api_delete_project(params):
    path = params.get("path", "")
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许删除 DeployPanel 工作区内的项目"}
    try:
        shutil.rmtree(os.path.abspath(path))
        records = load_json(os.path.join(DATA, "deployments.json"), {"items": []})
        real = os.path.abspath(path)
        records["items"] = [r for r in records.get("items", []) if os.path.abspath(r.get("path", "")) != real]
        save_json(os.path.join(DATA, "deployments.json"), records)
        return {"ok": True, "path": path, "deleted": True}
    except Exception as e:
        return {"ok": False, "error": "删除失败：" + str(e)}

def api_suggest(params):
    q = params.get("q", "").strip().lower()
    if not q:
        return {"ok": True, "items": [], "online": False}
    # 通道 1：本地缓存（离线可用）
    cache = load_json(CACHE_FILE, {"items": []})
    local = [it for it in cache.get("items", []) if q in it["full_name"].lower() or q in (it.get("description") or "").lower()]
    # 通道 2：GitHub 实时联想
    online = []
    headers = {"Accept": "application/vnd.github+json"}
    try:
        url = "https://api.github.com/search/repositories?q=" + urllib.parse.quote(q) + "&per_page=8&sort=stars"
        status, body = http_get(url, timeout=6, headers=headers)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            online = [{"full_name": it["full_name"], "language": it.get("language") or "",
                       "stars": it.get("stargazers_count", 0), "license": (it.get("license") or {}).get("spdx_id") or "",
                       "description": (it.get("description") or "")[:80]} for it in data.get("items", [])]
    except Exception:
        pass
    # 合并去重：在线优先，缓存补缺
    seen, merged = set(), []
    for it in online + local:
        if it["full_name"] not in seen:
            seen.add(it["full_name"])
            merged.append(it)
    return {"ok": True, "items": merged[:10], "online": bool(online), "offline_fallback": bool(local)}

# ---------- 服务启停控制 ----------
DEPLOY_FILE = os.path.join(DATA, "deployments.json")

def build_start_cmd(env, project_path):
    t = env.get("type", "static")
    if t == "node":
        # 有 npm start 用 npm start，否则 npm run dev
        return "npm start"
    if t == "python":
        for name in ("main.py", "app.py", "server.py", "manage.py", "run.py", "wsgi.py"):
            if os.path.isfile(os.path.join(project_path, name)):
                return "python " + name
        if os.path.isfile(os.path.join(project_path, "requirements.txt")):
            return "pip install -r requirements.txt && python main.py"
        return "python main.py"
    if t == "php":
        return "php -S 0.0.0.0:8080"
    if t == "java":
        return "mvn spring-boot:run"
    if t == "go":
        return "go run ."
    if t == "docker":
        return "docker compose up -d"
    return "python -m http.server 8080"

def api_service(params):
    path = params.get("path", "")
    action = params.get("action", "status")
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许管理 DeployPanel 工作区内的项目"}
    records = load_json(DEPLOY_FILE, {"items": []})
    real = os.path.abspath(path)
    rec = next((r for r in records.get("items", []) if os.path.abspath(r.get("path", "")) == real), None)
    if rec is None:
        rec = {"path": path, "repo": "unknown", "env": detect_env(path), "pid": None, "status": "stopped"}
    pid = rec.get("pid")
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except Exception:
            alive = False
    env = rec.get("env", detect_env(path))
    if action == "status":
        rec["status"] = "running" if alive else "stopped"
        return {"ok": True, "status": rec["status"], "pid": pid, "env": env}
    if action == "start":
        if alive:
            return {"ok": True, "status": "running", "pid": pid, "note": "已在运行"}
        cmd = build_start_cmd(env, real)
        if env.get("type") == "docker":
            try:
                subprocess.run(cmd, cwd=real, shell=True, timeout=120)
                rec["status"] = "running"; rec["pid"] = None; rec["cmd"] = cmd
                save_records(records, rec, real)
                return {"ok": True, "status": "running", "note": "Docker 已拉起（docker compose up -d）"}
            except Exception as e:
                return {"ok": False, "error": "Docker 启动失败：" + str(e)}
        try:
            proc = subprocess.Popen(cmd, cwd=real, shell=True,
                                    stdout=open(os.path.join(LOGS, "service_" + os.path.basename(real) + ".log"), "ab"),
                                    stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rec["pid"] = proc.pid; rec["status"] = "running"; rec["cmd"] = cmd; rec["started_at"] = int(time.time())
            save_records(records, rec, real)
            return {"ok": True, "status": "running", "pid": proc.pid, "cmd": cmd}
        except Exception as e:
            return {"ok": False, "error": "启动失败：" + str(e)}
    if action == "stop":
        if env.get("type") == "docker":
            try:
                subprocess.run("docker compose down", cwd=real, shell=True, timeout=60)
            except Exception:
                pass
        if pid and alive:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=15)
            except Exception:
                pass
        rec["status"] = "stopped"; rec["pid"] = None
        save_records(records, rec, real)
        return {"ok": True, "status": "stopped"}
    return {"ok": False, "error": "未知操作"}

def save_records(records, rec, real):
    items = [r for r in records.get("items", []) if os.path.abspath(r.get("path", "")) != real]
    items.insert(0, rec)
    records["items"] = items
    save_json(DEPLOY_FILE, records)

# ---------- 数据库可视化编辑（SQLite，零依赖离线） ----------
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def resolve_db_file(path, db):
    real = os.path.abspath(path)
    dbfile = os.path.abspath(os.path.join(real, db)) if not os.path.isabs(db) else os.path.abspath(db)
    if not dbfile.startswith(real + os.sep):
        raise ValueError("数据库必须位于项目目录内")
    if not os.path.isfile(dbfile):
        raise ValueError("数据库文件不存在")
    return dbfile

def check_ident(name):
    if not IDENT_RE.match(name):
        raise ValueError("非法标识符")

def api_db_scan(params):
    path = params.get("path", "")
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    dbs = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build", "__pycache__", "venv", "env")]
        for f in files:
            if f.lower().endswith((".db", ".sqlite", ".sqlite3", ".db3")):
                full = os.path.join(root, f)
                dbs.append({"file": os.path.relpath(full, path).replace("\\", "/"), "path": full,
                            "size": os.path.getsize(full), "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))})
    return {"ok": True, "databases": dbs, "count": len(dbs)}

def api_db_tables(params):
    path = params.get("path", "")
    db = params.get("db", "")
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        con.close()
        return {"ok": True, "tables": tables}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def api_db_table(params):
    path, db, table = params.get("path", ""), params.get("db", ""), params.get("table", "")
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        check_ident(table)
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute('PRAGMA table_info("' + table + '")')
        cols = [{"name": r[1], "type": r[2]} for r in cur.fetchall()]
        cur.execute('SELECT COUNT(*) FROM "' + table + '"')
        total = cur.fetchone()[0]
        cur.execute('SELECT rowid AS __rid, * FROM "' + table + '" ORDER BY rowid DESC LIMIT 200')
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
        return {"ok": True, "cols": cols, "rows": rows, "total": total, "shown": len(rows)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _collect_row(con, table, data, cols):
    check_ident(table)
    valid_cols = [c["name"] for c in cols]
    for k in data:
        check_ident(k)
    clean = {k: (data[k] if data[k] not in ("", None) else None) for k in data if k in valid_cols}
    return clean

def _parse_data(params):
    data = params.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    return data if isinstance(data, dict) else {}

def api_db_insert(params):
    path, db, table = params.get("path", ""), params.get("db", ""), params.get("table", "")
    data = _parse_data(params)
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute('PRAGMA table_info("' + table + '")')
        cols = [{"name": r[1]} for r in cur.fetchall()]
        clean = _collect_row(con, table, data, cols)
        if not clean:
            return {"ok": False, "error": "没有可插入的列数据"}
        q = 'INSERT INTO "' + table + '" (' + ",".join('"' + k + '"' for k in clean) + ") VALUES (" + ",".join("?" for _ in clean) + ")"
        cur.execute(q, list(clean.values()))
        con.commit()
        rid = cur.lastrowid
        con.close()
        return {"ok": True, "rowid": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def api_db_update(params):
    path, db, table = params.get("path", ""), params.get("db", ""), params.get("table", "")
    data = _parse_data(params)
    try:
        rowid = int(params.get("rowid", ""))
    except Exception:
        return {"ok": False, "error": "rowid 无效"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute('PRAGMA table_info("' + table + '")')
        cols = [{"name": r[1]} for r in cur.fetchall()]
        clean = _collect_row(con, table, data, cols)
        if not clean:
            return {"ok": False, "error": "没有可更新的列数据"}
        sets = ",".join('"' + k + '"=?' for k in clean)
        cur.execute('UPDATE "' + table + '" SET ' + sets + " WHERE rowid=?", list(clean.values()) + [rowid])
        con.commit()
        con.close()
        return {"ok": True, "affected": cur.rowcount}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def api_db_delete(params):
    path, db, table = params.get("path", ""), params.get("db", ""), params.get("table", "")
    try:
        rowid = int(params.get("rowid", ""))
    except Exception:
        return {"ok": False, "error": "rowid 无效"}
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        check_ident(table)
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute('DELETE FROM "' + table + '" WHERE rowid=?', [rowid])
        con.commit()
        con.close()
        return {"ok": True, "affected": cur.rowcount}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def api_db_add_column(params):
    path, db, table = params.get("path", ""), params.get("db", ""), params.get("table", "")
    column, ctype = params.get("column", ""), params.get("type", "TEXT")
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        check_ident(table); check_ident(column)
        if not IDENT_RE.match(ctype or ""):
            ctype = "TEXT"
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute('ALTER TABLE "' + table + '" ADD COLUMN "' + column + '" ' + ctype)
        con.commit()
        con.close()
        return {"ok": True, "column": column, "type": ctype}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def api_db_create_table(params):
    path, db = params.get("path", ""), params.get("db", "")
    table, coldef = params.get("table", ""), params.get("cols", "")
    if not in_workspaces(path):
        return {"ok": False, "error": "只允许访问工作区内的项目"}
    try:
        check_ident(table)
        coldef = coldef.strip() or "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT"
        dbfile = resolve_db_file(path, db)
        con = sqlite3.connect(dbfile)
        cur = con.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS "' + table + '" (' + coldef + ")")
        con.commit()
        con.close()
        return {"ok": True, "table": table}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- 积木工作室（低代码搭积木） ----------
BLOCKS_BUILTIN = load_json(os.path.join(DATA, "blocks_builtin.json"), {}).get("blocks", [])
CUSTOM_BLOCKS_FILE = os.path.join(DATA, "blocks_custom.json")
FLOWS_DIR = os.path.join(DATA, "flows")
os.makedirs(FLOWS_DIR, exist_ok=True)

def load_custom_blocks():
    return load_json(CUSTOM_BLOCKS_FILE, {}).get("blocks", [])

def save_custom_blocks(blocks):
    save_json(CUSTOM_BLOCKS_FILE, {"blocks": blocks})

def all_blocks():
    merged = {}
    for b in BLOCKS_BUILTIN:
        merged[b["id"]] = dict(b)
    for b in load_custom_blocks():
        merged[b["id"]] = dict(b)   # 自定义可覆盖内置
    return merged

def api_blocks(params):
    return {"ok": True, "blocks": list(all_blocks().values()),
            "builtin": [b["id"] for b in BLOCKS_BUILTIN]}

def api_blocks_save(params):
    try:
        data = json.loads(params.get("data", "{}"))
    except Exception:
        return {"ok": False, "error": "积木定义格式错误"}
    bid = str(data.get("id", "")).strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", bid):
        return {"ok": False, "error": "积木 id 需为字母/数字/下划线"}
    if not data.get("name") or not data.get("template"):
        return {"ok": False, "error": "积木需要名称与代码模板"}
    custom = [b for b in load_custom_blocks() if b.get("id") != bid]
    block = {"id": bid, "name": str(data["name"]), "category": str(data.get("category") or "自定义"),
             "desc": str(data.get("desc") or ""), "color": str(data.get("color") or "#5b6472"),
             "params": data.get("params") or [], "template": str(data["template"]),
             "kind": str(data.get("kind") or ""), "editable": True}
    custom.insert(0, block)
    save_custom_blocks(custom)
    return {"ok": True, "block": block}

def api_blocks_delete(params):
    bid = params.get("id", "")
    custom = [b for b in load_custom_blocks() if b.get("id") != bid]
    save_custom_blocks(custom)
    return {"ok": True, "deleted": True}

def _render_template(tpl, params):
    def repl(m):
        key = m.group(1)
        v = params.get(key, "")
        if v is None:
            v = ""
        return repr(str(v)) if m.group(2) else str(v)
    return re.sub(r"\{(\w+)(!r)?\}", repl, tpl)

def build_code(flow, blocks_index):
    """画布图 → Python 代码行（带缩进）"""
    uid2 = {b["uid"]: b for b in flow.get("blocks", [])}
    out = {}
    for e in flow.get("edges", []):
        out.setdefault(e["from"], {})[e.get("port", "next")] = e["to"]
    lines = []
    def chain(uid, indent, visited):
        while uid and uid not in visited:
            visited.add(uid)
            b = uid2.get(uid)
            if not b:
                break
            bdef = blocks_index.get(b.get("blockId"))
            if not bdef:
                uid = out.get(uid, {}).get("next")
                continue
            kind = bdef.get("kind", "")
            if kind == "if":
                cond = str(b.get("params", {}).get("cond", ""))
                lines.append(indent + "if " + cond + ":")
                t = out.get(uid, {}).get("true")
                if t:
                    chain(t, indent + "    ", visited)
                else:
                    lines.append(indent + "    pass")
                lines.append(indent + "else:")
                f = out.get(uid, {}).get("false")
                if f:
                    chain(f, indent + "    ", visited)
                else:
                    lines.append(indent + "    pass")
            elif kind == "for":
                var = str(b.get("params", {}).get("var", "item"))
                items = str(b.get("params", {}).get("items", "range(3)"))
                lines.append(indent + "for " + var + " in " + items + ":")
                body = out.get(uid, {}).get("body")
                if body:
                    chain(body, indent + "    ", visited)
                else:
                    lines.append(indent + "    pass")
            elif kind == "while":
                cond = str(b.get("params", {}).get("cond", "True"))
                lines.append(indent + "while " + cond + ":")
                body = out.get(uid, {}).get("body")
                if body:
                    chain(body, indent + "    ", visited)
                else:
                    lines.append(indent + "    pass")
            else:
                tpl = _render_template(bdef.get("template", ""), b.get("params", {}))
                if tpl.strip():
                    for ln in tpl.split("\n"):
                        lines.append(indent + ln)
            uid = out.get(uid, {}).get("next")
    entry = flow.get("entry")
    if not entry and uid2:
        entry = next((b["uid"] for b in flow["blocks"] if b.get("blockId") == "b_start"), None) or flow["blocks"][0]["uid"]
    if entry:
        chain(entry, "", set())
    return lines

def flow_source(flow):
    """生成可直接运行的 Python 源码"""
    idx = all_blocks()
    body = build_code(flow, idx)
    indent = "\n".join(("    " + l) if l.strip() else l for l in body)
    return "def _dp_main():\n" + indent + "\n\n_dp_main()\n"

def api_flow_run(params):
    try:
        flow = json.loads(params.get("data", "{}"))
    except Exception:
        return {"ok": False, "error": "画布数据格式错误"}
    if not flow.get("blocks"):
        return {"ok": False, "error": "画布为空，请先拖入积木"}
    src = flow_source(flow)
    try:
        proc = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=30, cwd=WORKSPACES)
        return {"ok": True, "exit": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:], "source": src}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "运行超时（30 秒）", "source": src}
    except Exception as e:
        return {"ok": False, "error": "运行失败：" + str(e), "source": src}

def api_flow_export(params):
    try:
        flow = json.loads(params.get("data", "{}"))
    except Exception:
        return {"ok": False, "error": "画布数据格式错误"}
    src = flow_source(flow)
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", str(flow.get("name") or "未命名")) or "未命名"
    out_dir = os.path.join(DATA, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, safe + ".py")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# 由 DeployPanel 积木工作室生成\n# -*- coding: utf-8 -*-\n" + src)
    return {"ok": True, "file": out, "name": os.path.basename(out)}

def api_flow_save(params):
    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "请填写工程名"}
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    try:
        flow = json.loads(params.get("data", "{}"))
    except Exception:
        return {"ok": False, "error": "画布数据格式错误"}
    flow["name"] = name
    save_json(os.path.join(FLOWS_DIR, safe + ".json"), flow)
    return {"ok": True, "name": safe}

def api_flow_load(params):
    name = params.get("name", "")
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    p = os.path.join(FLOWS_DIR, safe + ".json")
    if not os.path.exists(p):
        return {"ok": False, "error": "工程不存在"}
    return {"ok": True, "flow": load_json(p, {})}

def api_flow_list(params):
    names = []
    if os.path.isdir(FLOWS_DIR):
        for f in os.listdir(FLOWS_DIR):
            if f.endswith(".json"):
                names.append(f[:-5])
    names.sort()
    return {"ok": True, "flows": names}

def api_flow_delete(params):
    name = params.get("name", "")
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    p = os.path.join(FLOWS_DIR, safe + ".json")
    if os.path.exists(p):
        os.remove(p)
        return {"ok": True, "deleted": True}
    return {"ok": False, "error": "工程不存在"}

# ---------- 应用搭建器（可视化搭前后端） ----------
import html as _html
COMPONENTS_BUILTIN = load_json(os.path.join(DATA, "components_builtin.json"), {}).get("components", [])
COMPONENTS_CUSTOM_FILE = os.path.join(DATA, "components_custom.json")
APPS_DIR = os.path.join(DATA, "apps")
APPS_RUNTIME = os.path.join(DATA, "apps_runtime.json")
APPS_WORK = os.path.join(WORKSPACES, "apps")
os.makedirs(APPS_DIR, exist_ok=True)
os.makedirs(APPS_WORK, exist_ok=True)
UPLOADS = os.path.join(DATA, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

def load_custom_components():
    return load_json(COMPONENTS_CUSTOM_FILE, {}).get("components", [])

def all_components():
    merged = {}
    for c in COMPONENTS_BUILTIN:
        merged[c["id"]] = dict(c)
    for c in load_custom_components():
        merged[c["id"]] = dict(c)
    return merged

def api_app_components(params):
    return {"ok": True, "components": list(all_components().values())}

def api_app_comp_save(params):
    try:
        data = json.loads(params.get("data", "{}"))
    except Exception:
        return {"ok": False, "error": "组件 JSON 解析失败"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "组件数据格式错误"}
    cid = str(data.get("id", "")).strip()
    if not cid or not re.match(r"^[\w\u4e00-\u9fa5-]+$", cid):
        return {"ok": False, "error": "组件 id 不能为空且只能含中文/字母/数字/下划线/短横线"}
    if not str(data.get("name", "")).strip():
        return {"ok": False, "error": "组件名称必填"}
    props = data.get("props", [])
    if not isinstance(props, list):
        return {"ok": False, "error": "属性定义需为 JSON 数组"}
    for pp in props:
        if not isinstance(pp, dict) or not str(pp.get("key", "")).strip():
            return {"ok": False, "error": "属性 key 必填（[{\"key\":\"text\",...}]）"}
    comp = {
        "id": cid,
        "name": str(data.get("name", "")).strip(),
        "category": str(data.get("category", "自定义")).strip() or "自定义",
        "desc": str(data.get("desc", "")).strip(),
        "color": str(data.get("color", "#5b6472")).strip() or "#5b6472",
        "props": props,
        "html": str(data.get("html", "")),
        "css": str(data.get("css", "")),
    }
    cur = load_custom_components()
    cur = [c for c in cur if c.get("id") != cid]
    cur.append(comp)
    save_json(COMPONENTS_CUSTOM_FILE, {"components": cur})
    return {"ok": True}

def api_app_comp_delete(params):
    cid = str(params.get("id", "")).strip()
    if not cid:
        return {"ok": False, "error": "缺组件 id"}
    cur = load_custom_components()
    n = len(cur)
    cur = [c for c in cur if c.get("id") != cid]
    if len(cur) == n:
        return {"ok": False, "error": "该组件未被自定义过，无需删除"}
    save_json(COMPONENTS_CUSTOM_FILE, {"components": cur})
    return {"ok": True}

def render_comp_html(cdef, props, close=True):
    tpl = cdef.get("html", "")
    defs = {p2.get("key"): p2.get("default", "") for p2 in cdef.get("props", [])}
    imgs = {p2.get("key"): p2 for p2 in cdef.get("props", []) if p2.get("type") == "img"}
    def repl(m):
        key = m.group(1)
        v = props.get(key, defs.get(key, ""))
        if v is None:
            v = ""
        v = str(v)
        if key in imgs:
            if not v:
                return ""
            if imgs[key].get("as") == "img-tag":
                return '<img class="dp-nav-logo" src="%s" alt="logo">' % _html.escape(v, quote=True)
            return _html.escape(v, quote=True)
        return _html.escape(v, quote=True)
    inner = re.sub(r"\{(\w+)\}", repl, tpl)
    st = {}
    for k in ("align", "width", "border", "radius", "margin"):
        st[k] = str(props.get(k, defs.get(k, "")))
    wcls = "dp-wrap"
    for k, prefix in (("align", "dp-a-"), ("width", "dp-w-"), ("border", "dp-b-"), ("radius", "dp-r-"), ("margin", "dp-m-")):
        if st[k]:
            wcls += " " + prefix + st[k]
    return '<div class="%s">%s%s' % (wcls, inner, "</div>" if close else "")



def api_app_save(params):
    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "请填写应用名"}
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    try:
        comps = json.loads(params.get("data", "[]"))
    except Exception:
        return {"ok": False, "error": "页面数据格式错误"}
    save_json(os.path.join(APPS_DIR, safe + ".json"), {"name": name, "components": comps})
    return {"ok": True, "name": safe}

def api_app_load(params):
    name = params.get("name", "")
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    p2 = os.path.join(APPS_DIR, safe + ".json")
    if not os.path.exists(p2):
        return {"ok": False, "error": "应用不存在"}
    return {"ok": True, "app": load_json(p2, {})}

def api_app_list(params):
    names = []
    if os.path.isdir(APPS_DIR):
        for f in os.listdir(APPS_DIR):
            if f.endswith(".json"):
                names.append(f[:-5])
    names.sort()
    return {"ok": True, "apps": names}

def api_app_delete(params):
    name = params.get("name", "")
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    p2 = os.path.join(APPS_DIR, safe + ".json")
    if os.path.exists(p2):
        os.remove(p2)
        return {"ok": True, "deleted": True}
    return {"ok": False, "error": "应用不存在"}

def _collect_schema(comps, comp_index):
    """遍历组件树，收集表结构 {table: [cols]}；返回 (tables, forms, lists, buttons)"""
    tables = {}
    forms = []
    lists = []
    buttons = []
    def walk(items, form_ctx):
        for it in items:
            cid = it.get("compId")
            if cid == "c_form":
                tbl = str(it.get("props", {}).get("table", "users"))
                new_ctx = {"table": tbl}
                tables.setdefault(tbl, [])
                walk(it.get("children", []), new_ctx)
                forms.append({"table": tbl, "props": it.get("props", {})})
            elif cid in ("c_input", "c_textarea"):
                f = str(it.get("props", {}).get("field", "")).strip()
                if f and re.match(r"^[A-Za-z_]\w*$", f):
                    cur = tables.setdefault((form_ctx or {}).get("table", "users"), [])
                    if f not in cur:
                        cur.append(f)
            elif cid == "c_list":
                tbl = str(it.get("props", {}).get("table", "users"))
                tables.setdefault(tbl, [])
                lists.append(it)
            elif cid == "c_button" and str(it.get("props", {}).get("action", "")) == "提交到数据表":
                tbl = (form_ctx or {}).get("table") or str(it.get("props", {}).get("action_data", "users"))
                tables.setdefault(tbl, [])
                buttons.append({"props": it.get("props", {}), "table": tbl})
            else:
                walk(it.get("children", []), form_ctx)
    walk(comps, None)
    for tbl in list(tables.keys()):
        if not tables[tbl]:
            tables[tbl] = ["name", "message"]
    return tables, forms, lists, buttons

APP_PY_TEMPLATE = """# -*- coding: utf-8 -*-
# 由 DeployPanel 应用搭建器生成 · {appname}
import http.server, sqlite3, json, os, re, urllib.parse
BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data.db")
PORT = {port}
TABLES = {tables!r}
def db():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; return con
def init():
    con = db()
    for tbl, cols in TABLES.items():
        if not re.match(r"^[A-Za-z_]\\w*$", tbl):
            continue
        cols2 = [c for c in cols if re.match(r"^[A-Za-z_]\\w*$", c)]
        colsql = ", ".join("`%s` TEXT" % c for c in cols2)
        con.execute("CREATE TABLE IF NOT EXISTS `%s` (id INTEGER PRIMARY KEY AUTOINCREMENT, %s, created_at TEXT DEFAULT (datetime('now','localtime')))" % (tbl, colsql))
    con.commit(); con.close()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def _page(self):
        p2 = os.path.join(BASE, "templates", "index.html")
        with open(p2, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def _serve_asset(self, p):
        fn = os.path.basename(p)
        fp = os.path.join(BASE, "assets", fn)
        if not os.path.exists(fp):
            self._json({"ok": False, "error": "not found"})
            return
        with open(fp, "rb") as f:
            body = f.read()
        ext = os.path.splitext(fn)[1].lower()
        ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        pr = urllib.parse.urlparse(self.path)
        if pr.path.startswith("/api/data/"):
            tbl = pr.path[len("/api/data/"):]
            if not re.match(r"^[A-Za-z_]\\w*$", tbl) or tbl not in TABLES:
                self._json({"ok": False, "error": "表不存在"}); return
            try:
                con = db()
                rows = con.execute("SELECT * FROM `%s` ORDER BY id DESC LIMIT 200" % tbl).fetchall()
                con.close()
                self._json({"ok": True, "rows": [dict(r) for r in rows]})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        if pr.path.startswith("/assets/"):
            self._serve_asset(pr.path)
            return
        else:
            self._page()
    def do_POST(self):
        pr = urllib.parse.urlparse(self.path)
        if pr.path == "/api/_shutdown":
            _SHUTDOWN[0] = True
            self._json({"ok": True})
            return
        if pr.path.startswith("/api/data/"):
            tbl = pr.path[len("/api/data/"):]
            if not re.match(r"^[A-Za-z_]\\w*$", tbl) or tbl not in TABLES:
                self._json({"ok": False, "error": "表不存在"}); return
            try:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
                cols = [k for k in body.keys() if re.match(r"^[A-Za-z_]\\w*$", k) and k in TABLES[tbl]]
                con = db()
                if cols:
                    q = "INSERT INTO `%s` (%s) VALUES (%s)" % (tbl, ",".join("`%s`" % c for c in cols), ",".join("?" * len(cols)))
                    con.execute(q, [body[c] for c in cols])
                con.commit(); con.close()
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        else:
            self._page()
_SHUTDOWN = [False]
if __name__ == "__main__":
    init()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("app running on http://127.0.0.1:%d" % PORT)
    import threading, time as _t
    def _watch():
        while True:
            _t.sleep(0.5)
            if _SHUTDOWN[0]:
                try:
                    srv.shutdown()
                except Exception:
                    pass
                return
    threading.Thread(target=_watch, daemon=True).start()
    srv.serve_forever()
    print("app stopped")
"""

def api_app_generate(params):
    name = str(params.get("name", "")).strip()
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    p2 = os.path.join(APPS_DIR, safe + ".json")
    if not os.path.exists(p2):
        return {"ok": False, "error": "应用不存在，请先保存"}
    app = load_json(p2, {})
    comps = app.get("components", [])
    cidx = all_components()
    tables, forms, lists, buttons = _collect_schema(comps, cidx)

    port = 8801
    while port < 8899:
        if not _port_in_use(port):
            break
        port += 1

    out_dir = os.path.join(APPS_WORK, safe)
    # img 类型属性：uploads/ 引用复制进应用 assets 并改写为 assets/
    import shutil as _sh
    def _asset_rewrite(items):
        for it in items:
            c = cidx.get(it.get("compId"))
            if c:
                for pp in c.get("props", []):
                    if pp.get("type") == "img":
                        k = pp.get("key")
                        v = str(it.get("props", {}).get(k, ""))
                        if v.startswith("uploads/"):
                            src = os.path.join(UPLOADS, os.path.basename(v))
                            if os.path.exists(src):
                                adir = os.path.join(out_dir, "assets")
                                os.makedirs(adir, exist_ok=True)
                                dst = os.path.join(adir, os.path.basename(v))
                                if not os.path.exists(dst):
                                    _sh.copy2(src, dst)
                                it.setdefault("props", {})[k] = "assets/" + os.path.basename(v)
            _asset_rewrite(it.get("children", []))
    _asset_rewrite(comps)

    css_parts, html_parts = [], []
    def walk(items):
        for it in items:
            c = cidx.get(it.get("compId"))
            if not c:
                continue
            props = it.get("props", {})
            css = c.get("css", "")
            if css and css not in css_parts:
                css_parts.append(css)
            if it.get("compId") == "c_form":
                html_parts.append(render_comp_html(c, props, close=False) + "\n")
                walk(it.get("children", []))
                html_parts.append("</form></div>\n")
            else:
                html_parts.append(render_comp_html(c, props) + "\n")
    walk(comps)
    GEN_STYLE_CSS = ".dp-wrap{box-sizing:border-box}.dp-a-左{text-align:left}.dp-a-居中{text-align:center}.dp-a-右{text-align:right}.dp-w-全宽{width:100%}.dp-w-默认{width:auto}.dp-w-半宽{width:50%}.dp-b-无{border:none}.dp-b-细{border:1px solid #d0d5dd}.dp-b-明显{border:2px solid #2f6fed}.dp-r-小{border-radius:4px}.dp-r-中{border-radius:10px}.dp-r-大{border-radius:16px}.dp-m-小{margin:4px 0}.dp-m-中{margin:12px 0}.dp-m-大{margin:20px 0}"
    page_css = GEN_STYLE_CSS + "\n" + "\n".join(css_parts)
    page_html = "".join(html_parts)

    page_js = r'''function toast(msg){var t=document.createElement('div');t.style.cssText='position:fixed;left:50%;bottom:30px;transform:translateX(-50%);background:#222;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;z-index:99';t.textContent=msg;document.body.appendChild(t);setTimeout(function(){t.remove()},2200);}
function _h(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
document.querySelectorAll('.dp-list').forEach(function(el){
  var tbl=el.getAttribute('data-table'), flds=(el.getAttribute('data-fields')||'').split(',').map(function(s){return s.trim()}).filter(Boolean);
  fetch('/api/data/'+tbl).then(function(r){return r.json()}).then(function(j){
    if(!j.ok){el.innerHTML='<div style="color:#d64545">加载失败:'+_h(j.error)+'</div>';return;}
    if(!j.rows.length){el.innerHTML='<div style="color:#999">暂无数据</div>';return;}
    el.innerHTML=j.rows.map(function(r){
      var cells=flds.length?flds.map(function(f){return '<div style="margin:2px 0">'+_h(String(r[f]||''))+'</div>'}).join(''):_h(JSON.stringify(r));
      return '<div class="dp-row">'+cells+'</div>';
    }).join('');
  }).catch(function(){el.innerHTML='<div style="color:#d64545">请求失败</div>';});
});
document.querySelectorAll('.dp-form').forEach(function(f){
  f.addEventListener('submit',function(ev){
    ev.preventDefault();
    var tbl=f.getAttribute('data-table')||'users';
    var data={};
    f.querySelectorAll('.dp-input').forEach(function(inp){data[inp.getAttribute('data-field')||'']=inp.value;});
    fetch('/api/data/'+tbl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(j){
      if(j.ok){toast('提交成功');f.reset();}
      else{toast('提交失败:'+_h(j.error));}
    }).catch(function(){toast('网络错误');});
  });
});
document.querySelectorAll('.dp-btn').forEach(function(b){
  b.addEventListener('click',function(){
    var act=b.getAttribute('data-action')||'', arg=b.getAttribute('data-arg')||'';
    if(act==='提交到数据表'){var f=b.closest('.dp-form');if(f){f.requestSubmit?f.requestSubmit():f.submit();}else{toast('按钮不在表单内，无法提交');}}
    else if(act==='提示消息'){toast(arg);}
    else if(act==='打开链接'){window.open(arg,'_blank');}
  });
});
'''
    index_html = ('<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  '<title>%s</title><style>body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:640px;margin:0 auto;background:#f5f6f8;color:#222;padding-bottom:40px}%s</style></head>'
                  '<body>%s<script>%s</script></body></html>') % (_html.escape(app.get("name", "我的应用")), page_css, page_html, page_js)

    os.makedirs(os.path.join(out_dir, "templates"), exist_ok=True)
    with open(os.path.join(out_dir, "templates", "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    # 路由说明文档（随应用打包，可拷走）
    routes_md = ["# %s 路由说明\n" % app.get("name", "我的应用")]
    routes_md.append("## 后端接口\n\n| 方法 | 路由 | 用途 |\n|---|---|---|")
    routes_md.append("| POST | /api/data/{表} | 写入一条数据（表单 / 提交按钮触发） |")
    routes_md.append("| GET | /api/data/{表} | 读取该表全部数据（数据列表触发） |")
    routes_md.append("| POST | /api/_shutdown | 优雅停止本应用服务 |")
    if os.path.exists(os.path.join(out_dir, "assets")):
        routes_md.append("| GET | /assets/{文件} | 静态资源（图片，白名单防穿越） |")
    if tables:
        routes_md.append("\n## 数据表\n")
        for tbl in sorted(tables.keys()):
            routes_md.append("- `%s`：字段 %s" % (tbl, ", ".join(tables[tbl])))
    routes_md.append("\n## 前端触发\n")
    for f in forms:
        routes_md.append("- 表单容器 → POST /api/data/%s" % f.get("table"))
    for b in buttons:
        p2 = b.get("props", {})
        routes_md.append("- 按钮「%s」→ POST /api/data/%s" % (p2.get("text", "提交"), b.get("table", "users")))
    for l in lists:
        p2 = l.get("props", {})
        routes_md.append("- 数据列表「%s」→ GET /api/data/%s" % (p2.get("table", "列表"), p2.get("table", "users")))
    with open(os.path.join(out_dir, "templates", "ROUTES.md"), "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(routes_md))

    app_py = APP_PY_TEMPLATE.replace("{appname}", app.get("name", "我的应用")).replace("{port}", str(port)).replace("{tables!r}", repr(tables))
    with open(os.path.join(out_dir, "app.py"), "w", encoding="utf-8", newline="") as f:
        f.write(app_py)
    return {"ok": True, "path": out_dir, "port": port, "tables": tables,
            "index": os.path.join(out_dir, "templates", "index.html")}

def _port_in_use(port):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()

def api_app_run(params):
    name = str(params.get("name", "")).strip()
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    rt = load_json(APPS_RUNTIME, {})
    if safe in rt:
        if _proc_alive(rt[safe].get("pid")):
            return {"ok": True, "already": True, "info": rt[safe]}
        rt.pop(safe, None)
    gen = api_app_generate(params)
    if not gen.get("ok"):
        return gen
    out_dir = gen["path"]; port = gen["port"]
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=out_dir,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                            stdout=open(os.path.join(LOGS, "app_%s.log" % safe), "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    import time as _t
    # 等待端口就绪（最多 8 秒），避免前端打开页面时连接被拒
    _dead = _t.time() + 8
    _ready = False
    while _t.time() < _dead:
        try:
            _s = socket.create_connection(("127.0.0.1", port), timeout=0.4)
            _s.close()
            _ready = True
            break
        except OSError:
            _t.sleep(0.25)
    if not _ready:
        if not _proc_alive(proc.pid):
            return {"ok": False, "error": "应用启动失败（可查看 logs/app_%s.log）" % safe}
    else:
        _t.sleep(0.3)
    rt[safe] = {"pid": proc.pid, "port": port, "path": out_dir, "ts": int(_t.time())}
    save_json(APPS_RUNTIME, rt)
    log("app run: %s pid=%s port=%s" % (safe, proc.pid, port))
    return {"ok": True, "info": rt[safe]}

def api_app_stop(params):
    name = str(params.get("name", "")).strip()
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    rt = load_json(APPS_RUNTIME, {})
    info = rt.get(safe)
    pid = (info or {}).get("pid")
    port = (info or {}).get("port")
    log("app stop: %s pid=%s port=%s" % (safe, pid, port))
    if pid and _proc_alive(pid):
        # 1) 先请求优雅关闭（仅本机）
        if port:
            try:
                import urllib.request as _ur
                req = _ur.Request("http://127.0.0.1:%d/api/_shutdown" % port, method="POST")
                _ur.urlopen(req, timeout=3).read()
                import time as _t
                _t.sleep(0.8)
            except Exception:
                pass
        # 2) 兜底强制结束（不带 /T，只杀目标进程）
        if _proc_alive(pid):
            r = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)
            log("app stop kill: %s" % ((r.stdout or "").strip() or (r.stderr or "").strip()))
    rt.pop(safe, None)
    save_json(APPS_RUNTIME, rt)
    return {"ok": True, "stopped": True}

def api_app_status(params):
    name = str(params.get("name", "")).strip()
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    rt = load_json(APPS_RUNTIME, {})
    info = rt.get(safe)
    if info and _proc_alive(info.get("pid")):
        return {"ok": True, "running": True, "info": info}
    if info:
        rt.pop(safe, None); save_json(APPS_RUNTIME, rt)
    return {"ok": True, "running": False, "info": None}

def _proc_alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False

def api_app_dbview(params):
    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "缺应用名"}
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    dbp = os.path.join(APPS_WORK, safe, "data.db")
    if not os.path.exists(dbp):
        return {"ok": False, "error": "应用尚未生成（请先「生成前后端」）"}
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    tabs = []
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for r in rows:
            t = r["name"]
            if not re.match(r"^[\w\u4e00-\u9fa5-]+$", t):
                continue
            cnt = con.execute("SELECT COUNT(*) c FROM `%s`" % t).fetchone()["c"]
            cols = [c2["name"] for c2 in con.execute("PRAGMA table_info(`%s`)" % t).fetchall()]
            data = [dict(x) for x in con.execute("SELECT * FROM `%s` ORDER BY id DESC LIMIT 5" % t).fetchall()]
            tabs.append({"name": t, "cols": cols, "count": cnt, "rows": data})
    finally:
        con.close()
    return {"ok": True, "tables": tabs}

def api_app_files(params):

    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "缺应用名"}
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    d = os.path.join(APPS_WORK, safe)
    if not os.path.isdir(d):
        return {"ok": False, "error": "应用尚未生成（请先「生成前后端」）"}
    files = []
    for label, rel in (("app.py（后端）", os.path.join("app.py")),
                       ("index.html（前端页面）", os.path.join("templates", "index.html")),
                       ("ROUTES.md（路由说明）", os.path.join("templates", "ROUTES.md"))):
        fp = os.path.join(d, rel)
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                files.append({"label": label, "content": f.read()})
    return {"ok": True, "files": files, "path": d}

def api_app_db_exec(params):
    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "缺应用名"}
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
    dbp = os.path.join(APPS_WORK, safe, "data.db")
    if not os.path.exists(dbp):
        return {"ok": False, "error": "应用尚未生成（请先「生成前后端」）"}
    action = str(params.get("action", ""))
    table = str(params.get("table", ""))
    if not re.match(r"^[\w\u4e00-\u9fa5-]+$", table):
        return {"ok": False, "error": "非法表名"}
    _FIELD_RE = re.compile(r"^[\w\u4e00-\u9fa5-]+$")
    def _bad(v):
        return not v or not _FIELD_RE.match(v)
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    try:
        if action == "update_cell":
            field = str(params.get("field", ""))
            rowid = int(params.get("id", 0))
            if _bad(field) or field == "id":
                return {"ok": False, "error": "非法字段"}
            cols = [c["name"] for c in con.execute("PRAGMA table_info(`%s`)" % table)]
            if field not in cols:
                return {"ok": False, "error": "字段不存在: %s" % field}
            con.execute("UPDATE `%s` SET `%s`=? WHERE id=?" % (table, field), (params.get("value"), rowid))
        elif action == "insert_row":
            values = params.get("values") or {}
            cols = [c["name"] for c in con.execute("PRAGMA table_info(`%s`)" % table)]
            vd = {k: v for k, v in values.items() if k in cols and _FIELD_RE.match(k) and k != "id"}
            if not vd:
                return {"ok": False, "error": "没有可写入的字段值"}
            ks = list(vd.keys())
            con.execute("INSERT INTO `%s` (%s) VALUES (%s)" % (table, ",".join("`%s`" % k for k in ks), ",".join("?" * len(ks))), list(vd.values()))
        elif action == "delete_row":
            rowid = int(params.get("id", 0))
            con.execute("DELETE FROM `%s` WHERE id=?" % table, (rowid,))
        elif action == "add_column":
            colname = str(params.get("field", ""))
            ctype = str(params.get("ctype", "TEXT")).upper()
            if _bad(colname) or colname == "id":
                return {"ok": False, "error": "非法字段名"}
            if ctype not in ("TEXT", "INTEGER", "REAL", "NUMERIC"):
                ctype = "TEXT"
            con.execute("ALTER TABLE `%s` ADD COLUMN `%s` %s" % (table, colname, ctype))
        elif action == "drop_column":
            colname = str(params.get("field", ""))
            if _bad(colname) or colname == "id":
                return {"ok": False, "error": "不能删除主键 id"}
            cols = [c["name"] for c in con.execute("PRAGMA table_info(`%s`)" % table)]
            if colname not in cols:
                return {"ok": False, "error": "字段不存在"}
            keep = [c for c in cols if c != colname]
            if len(keep) < 1:
                return {"ok": False, "error": "至少要保留一个字段"}
            con.execute("BEGIN")
            con.execute("ALTER TABLE `%s` RENAME TO _dp_old" % table)
            con.execute("CREATE TABLE `%s` (%s, PRIMARY KEY(id))" % (table, ",".join("`%s`" % c for c in keep)))
            con.execute("INSERT INTO `%s` (%s) SELECT %s FROM _dp_old" % (table, ",".join("`%s`" % c for c in keep), ",".join("`%s`" % c for c in keep)))
            con.execute("DROP TABLE _dp_old")
            con.execute("COMMIT")
        elif action == "drop_table":
            con.execute("DROP TABLE IF EXISTS `%s`" % table)
        elif action == "create_table":
            tname = str(params.get("table", ""))
            cols = params.get("cols") or []
            if _bad(tname):
                return {"ok": False, "error": "非法表名"}
            defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
            for c in cols:
                cn = str(c.get("name", ""))
                ct = str(c.get("type", "TEXT")).upper()
                if _bad(cn) or cn == "id":
                    continue
                if ct not in ("TEXT", "INTEGER", "REAL", "NUMERIC"):
                    ct = "TEXT"
                defs.append("`%s` %s" % (cn, ct))
            con.execute("CREATE TABLE IF NOT EXISTS `%s` (%s)" % (tname, ",".join(defs)))
        else:
            return {"ok": False, "error": "未知操作: %s" % action}
        con.commit()
    except Exception as e:
        try:
            con.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        con.close()
    return {"ok": True}

# ---------- 网址分析器 ----------
T_AUTO = '# -*- coding: utf-8 -*-\n# 由 DeployPanel 网址分析器生成 · 简易抓取 · {title}\nimport requests\nfrom bs4 import BeautifulSoup\n\nURL = "{url}"\nheaders = {{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeployPanel/1.0"}}\n\ndef main():\n    r = requests.get(URL, headers=headers, timeout=15)\n    r.raise_for_status()\n    r.encoding = r.apparent_encoding or "utf-8"\n    soup = BeautifulSoup(r.text, "html.parser")\n    print("标题:", soup.title.get_text(strip=True) if soup.title else "无")\n    print("段落数:", len(soup.find_all("p")), "表格数:", len(soup.find_all("table")),\n          "链接数:", len(soup.find_all("a")), "图片数:", len(soup.find_all("img")))\n    print("--- 正文前 5 段 ---")\n    for p in soup.find_all("p")[:5]:\n        t = p.get_text(strip=True)\n        if t:\n            print(t[:200])\n\nif __name__ == "__main__":\n    main()\n'
T_TABLE = '# -*- coding: utf-8 -*-\n# 由 DeployPanel 网址分析器生成 · 表格爬取为 CSV · {title}\nimport requests, csv\nfrom bs4 import BeautifulSoup\n\nURL = "{url}"\nheaders = {{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeployPanel/1.0"}}\nOUT = "output_table.csv"\n\ndef main():\n    r = requests.get(URL, headers=headers, timeout=15)\n    r.raise_for_status()\n    r.encoding = r.apparent_encoding or "utf-8"\n    soup = BeautifulSoup(r.text, "html.parser")\n    tables = soup.find_all("table")\n    if not tables:\n        print("该页面未发现 <table> 表格元素")\n        return\n    rows = []\n    for tr in tables[0].find_all("tr"):\n        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]\n        if cells:\n            rows.append(cells)\n    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:\n        csv.writer(f).writerows(rows)\n    print("已写入", OUT, "共", len(rows), "行")\n    for row in rows[:10]:\n        print(row)\n\nif __name__ == "__main__":\n    main()\n'
T_LIST = '# -*- coding: utf-8 -*-\n# 由 DeployPanel 网址分析器生成 · 列表/条目爬取 · {title}\nimport requests\nfrom bs4 import BeautifulSoup\n\nURL = "{url}"\nheaders = {{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeployPanel/1.0"}}\n\ndef main():\n    r = requests.get(URL, headers=headers, timeout=15)\n    r.raise_for_status()\n    r.encoding = r.apparent_encoding or "utf-8"\n    soup = BeautifulSoup(r.text, "html.parser")\n    items = soup.find_all("li")\n    print("共", len(items), "个列表项")\n    for li in items[:20]:\n        t = li.get_text(" ", strip=True)\n        a = li.find("a")\n        href = a.get("href") if a else ""\n        if t:\n            print("-", t[:150], (href if href and href.startswith("http") else ""))\n\nif __name__ == "__main__":\n    main()\n'
T_JSON = '# -*- coding: utf-8 -*-\n# 由 DeployPanel 网址分析器生成 · JSON 数据拉取 · {title}\nimport requests, json\n\nURL = "{url}"\nheaders = {{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeployPanel/1.0"}}\n\ndef main():\n    r = requests.get(URL, headers=headers, timeout=15)\n    r.raise_for_status()\n    data = r.json()\n    def walk(o, depth=0):\n        if depth > 2:\n            return\n        if isinstance(o, dict):\n            for k, v in list(o.items())[:12]:\n                print("  " * depth + str(k) + ":", str(v)[:120] if not isinstance(v, (dict, list)) else "")\n                walk(v, depth + 1)\n        elif isinstance(o, list):\n            print("  " * depth + "[列表] 共", len(o), "项")\n            if o:\n                walk(o[0], depth + 1)\n    walk(data)\n    with open("output.json", "w", encoding="utf-8") as f:\n        json.dump(data, f, ensure_ascii=False, indent=2)\n    print("已写入 output.json")\n\nif __name__ == "__main__":\n    main()\n'
SCRIPT_MAP = {"auto": T_AUTO, "table": T_TABLE, "list": T_LIST, "json": T_JSON}

def _clean_dup_url(u):
    """清理粘贴重复的 URL：https://a.com/x?m=1https://a.com/x?m=1 → 取第一个"""
    m1 = re.match(r"(https?://[^/?#]+)", u)
    if not m1:
        return u
    host1 = m1.group(1)
    for m in re.finditer(r"https?://", u):
        if m.start() > 0 and u[m.start():].startswith(host1):
            return u[:m.start()]
    return u


def _detect_platform(host):
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "抖音"
    if "bilibili.com" in host or "b23.tv" in host:
        return "B站"
    if "kuaishou.com" in host or "gifshow.com" in host:
        return "快手"
    if "weibo.com" in host:
        return "微博"
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    return ""


def _fetch_page(u):
    """抓取页面 HTML（带 UA，编码自动识别 utf-8/gbk）"""
    hd = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "zh-CN,zh;q=0.9",
          "Referer": "https://www.baidu.com/"}
    req = urllib.request.Request(u, headers=hd)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read(4 * 1024 * 1024)
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if enc in ("gzip", "x-gzip"):
            import gzip
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            import zlib
            raw = zlib.decompress(raw)
        head = raw[:2000]
        ctype = resp.headers.get("Content-Type", "").lower()
        for enc in ("utf-8", "gbk", "gb18030"):
            if enc in ctype or ("charset=" + enc) in head.decode("ascii", "ignore").lower():
                try:
                    return raw.decode(enc, "replace")
                except Exception:
                    break
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return raw.decode("gb18030", "replace")


def _extract_text(html):
    """粗略正文提取：去脚本/导航标签后取最长中文文本段"""
    h = re.sub(r"<(script|style|nav|header|footer|aside|form|svg)[^>]*>.*?</\1>", "\n", html,
               flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|h[1-6]|li|tr|section|article)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"&nbsp;?", " ", h)
    h = re.sub(r"&[a-z]+;", " ", h)
    lines = [ln.strip() for ln in h.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 6 and not re.fullmatch(r"[\W_]+", ln)]
    best, cur = [], []
    for ln in lines:
        if len(ln) >= 10:
            cur.append(ln)
        else:
            if sum(len(x) for x in cur) > sum(len(x) for x in best):
                best = cur
            cur = []
    if sum(len(x) for x in cur) > sum(len(x) for x in best):
        best = cur
    return best if best else lines[:200]


def _extract_images(html, base_url):
    """漫画/图文页：提取内容图片（过滤图标/小图）"""
    out, seen = [], set()
    pat = re.compile(r"<img[^>]+?(?:data-src|data-original|src)=[\"']([^\"']+)[\"']", re.I)
    for m in pat.finditer(html):
        u = urllib.parse.urljoin(base_url, m.group(1))
        if not u.startswith(("http://", "https://")):
            continue
        low = u.lower()
        if any(k in low for k in ("logo", "icon", "avatar", "emoji", "sprite", "spinner",
                                  "loading", "ad-", "banner", "qr", "erweima", "data:image")):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= 120:
            break
    return out



def api_media_episodes(params):
    """选集/分P：kind=bangumi 传 season_id（番剧分集）；kind=video 传 bvid（视频分P）
    返回 episodes: [{index, title, dur, url}]"""
    kind = str(params.get("kind") or "")
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Referer": "https://www.bilibili.com/", "Accept-Encoding": "gzip, deflate"}
    eps = []
    try:
        if kind == "bangumi":
            sid = str(params.get("season_id") or "")
            if not sid:
                return {"ok": False, "error": "缺少 season_id"}
            txt = _fetch_page("https://api.bilibili.com/pgc/view/web/season?season_id=" + sid)
            d = json.loads(txt)
            res = d.get("result") or {}
            for e in (res.get("episodes") or []):
                epid = e.get("id") or ""
                dur = e.get("duration") or 0
                try:
                    dur = int(dur) // 1000
                except Exception:
                    dur = 0
                # B站 pgc 接口部分集 duration 为异常毫秒值（如 16000=16秒），
                # 真实剧集不可能这么短，标注 -- 避免与下载后实际时长对不上
                dur_txt = (str(dur // 60) + ":" + str(dur % 60).zfill(2)) if dur >= 60 else "--"
                eps.append({"index": e.get("title") or str(epid), "title": e.get("long_title") or e.get("title") or "",
                            "dur": dur_txt, "url": ("https://www.bilibili.com/bangumi/play/ep" + str(epid)) if epid else ""})
        elif kind == "video":
            bv = str(params.get("bvid") or "")
            if not bv:
                return {"ok": False, "error": "缺少 bvid"}
            html = _fetch_page("https://www.bilibili.com/video/" + bv)
            m = re.search(r'"pages":(\[.*?\])(?=[,}])', html, re.S)
            if not m:
                return {"ok": False, "error": "该视频无分P（单集视频）"}
            pages = json.loads(m.group(1))
            for pg in pages[:50]:
                dur = pg.get("duration") or 0
                dur_txt = (str(int(dur) // 60) + ":" + str(int(dur) % 60).zfill(2)) if dur else ""
                n = pg.get("page") or 0
                eps.append({"index": "P" + str(n), "title": pg.get("part") or "",
                            "dur": dur_txt, "url": ("https://www.bilibili.com/video/" + bv + ("?p=" + str(n) if n and n > 1 else ""))})
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}
    if not eps:
        return {"ok": False, "error": "未获取到分集（可能需要登录）"}
    return {"ok": True, "episodes": eps, "total": len(eps)}


def api_media_article(params):
    """POST {url} → 提取网页正文（小说/文章阅读）"""
    u = str(params.get("url") or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "请提供 http(s):// 链接"}
    try:
        html = _fetch_page(u)
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
        title = (m.group(1).strip() if m else "")[:80] or "页面内容"
        text = _extract_text(html)
        if not text:
            return {"ok": False, "error": "未提取到正文（页面可能需要登录或为图片型内容）"}
        return {"ok": True, "title": title, "text": text, "chars": sum(len(x) for x in text)}
    except Exception as e:
        return {"ok": False, "error": "抓取失败：" + str(e)[:150]}


def api_media_manga(params):
    """POST {url} → 提取图片序列（漫画/图集阅读）"""
    u = str(params.get("url") or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "请提供 http(s):// 链接"}
    try:
        html = _fetch_page(u)
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
        title = (m.group(1).strip() if m else "")[:80] or "图片内容"
        imgs = _extract_images(html, u)
        if not imgs:
            return {"ok": False, "error": "未提取到图片（页面可能需要登录或动态加载）"}
        return {"ok": True, "title": title, "images": imgs}
    except Exception as e:
        return {"ok": False, "error": "抓取失败：" + str(e)[:150]}



def _cls_tag(title):
    """按标题关键词给内容分类（教程/音乐MV/解说/剪辑合集/纪录片/影视剧集/游戏/直播/其他）"""
    t = (title or "").lower()
    rules = [
        ("音乐MV", ["mv", "音乐", "歌曲", "演唱", "歌单", "ost", "主题曲", "片尾曲", " live", "live版", "翻唱", "纯音乐", "伴奏"]),
        ("直播", ["直播", "开播", "live 房间"]),
        ("教程", ["教程", "课程", "入门", "教学", "实战", "从零", "lesson", "tutorial", "公开课", "讲座", "系列教程", "带做"]),
        ("解说", ["解说", "解析", "reaction", "盘点", "评测", "影评", "杂谈", "解读", "一口气看完", "带你看", "深度解读"]),
        ("剪辑合集", ["混剪", "剪辑", "合集", "精选", "踩点", "燃向", "多p", "大赏"]),
        ("纪录片", ["纪录片", "纪实", "考古", "全景"]),
        ("影视剧集", ["第1集", "第2集", "第3集", "第4集", "第5集", "第6集", "第7集", "第8集", "第9集", "第10集",
                       "第11集", "第12集", "第13集", "第14集", "第15集", "第16集", "第17集", "第18集", "第19集", "第20集",
                       "全集", "完整版", "正片", "预告", "电影", "电视剧", "番剧", "剧集", "花絮", "cut"]),
        ("游戏", ["游戏", "攻略", "实况", "原神", "王者", "我的世界", "minecraft", "英雄联盟", "lol", "吃鸡", "元神"]),
    ]
    if re.search(r"第[0-9一二三四五六七八九十百零]+[集话期]|更新至|连载|大结局|终章", t):
        return "影视剧集"
    for name, kws in rules:
        for k in kws:
            if k in t:
                return name
    return "其他"


# ===== 内容过滤名单（全部可配置，用户可在界面/ filters.json 中自由增删，不剥夺用户控制权） =====
_FILTER_DEFAULTS = {
    "black_dom": ["baike.baidu", "baike.so.com", "zdic.net", "hanyuguoxue", "chinesewords.org", "jd.com",
                  "taobao.com", "tmall.com", "dangdang.com", "39.net", "docin.com",
                  "wenku.baidu", "cqvip", "wanfangdata", "dict.", "cnki",
                  "jjwxc", "qidian", "zhulang", "huayue", "biquge",
                  "weread", "fanqienovel", "fanqie", "qingting", "ximalaya",
                  "read.qq.com", "books.read", "yunqi", "chuangshi", "qingyunian",
                  "zhangyue", "17k.com", "hengyan", "shuqi", "readnovel", "wuxiaworld",
                  "linovel", "ciweimao", "sfacg"],
    "black_title": ["站长", "APP下载", "网盘资源", "文库", "下载文档"],
    "video_doms": ["bilibili.com", "douyin.com", "ixigua.com", "weibo.com", "kuaishou.com",
                   "acfun.cn", "163.com", "icourse163.org", "mooc", "study.163.com",
                   "open.163.com", "youtube.com", "vimeo.com", "pearvideo.com",
                   "haokan.baidu.com", "v.baidu.com", "toutiao.com", "cctv.com", "cntv.cn",
                   "cnmooc", "cloud.tencent.com/edu", "1905.com"],
    "paid_doms": ["v.qq.com", "iqiyi.com", "youku.com", "mgtv.com", "tv.sohu.com",
                  "letv.com", "pptv.com", "kankan.com", "fun.tv", "miguvideo.com",
                  "ke.qq.com", "xue.taobao.com", "qiyi", "yidianzixun.com/video",
                  "mgtvtv.com", "le.com", "letv", "vip.qq.com", "vip.iqiyi.com"],
    "junk_title": ["解说", "速看", "一口气", "盘点", "混剪", "影评", "书评", "读后感",
                   "深度解析", "剧情解读", "剧情讲解", "解读", "小说", "在线阅读", "最新章节",
                   "大结局", "剧情介绍", "第一集到", "reaction", "盘点top", "盘点TOP",
                   "动画解说", "逐集解说", "漫剪", "安利", "科普"],
    "free_sites": ["tv.cctv.com", "jishi.cctv.com", "bilibili.com", "1905.com",
                   "open.163.com", "icourse163.org", "ixigua.com", "v.douyin.com",
                   "v.kuaishou.com", "pearvideo.com", "tv.cctv.com/videos"],
    "pirate_doms": ["btbtdy.com", "btbtdy.cc", "dy2018.com", "dytt8.net", "dytt89.com",
                    "80s.tw", "80s.la", "okzyw.com", "1080zyk.com", "zxzj.pro",
                    "66s.cc", "nfmovies.com", "tvb123.com", "qq7799.com", "5dianying.com"],
}
_FILTER_KEYS = ("black_dom", "black_title", "video_doms", "paid_doms", "junk_title", "free_sites", "pirate_doms")
_FILTERS = dict(_FILTER_DEFAULTS)
_FILTER_FILE = os.path.join(DATA, "filters.json")

def _load_filters():
    """从 data/filters.json 加载用户自定义名单；文件不存在则写入默认值"""
    global _FILTERS
    try:
        if os.path.exists(_FILTER_FILE):
            with open(_FILTER_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            _FILTERS = dict(_FILTER_DEFAULTS)
            for k in _FILTER_KEYS:
                v = d.get(k)
                if isinstance(v, list) and all(isinstance(x, str) for x in v):
                    _FILTERS[k] = v
        else:
            _FILTERS = dict(_FILTER_DEFAULTS)
            with open(_FILTER_FILE, "w", encoding="utf-8") as f:
                json.dump(_FILTERS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("filters load error: " + repr(e))
        _FILTERS = dict(_FILTER_DEFAULTS)
    return _FILTERS

_load_filters()

_WEB_BLACK_DOM = tuple(_FILTERS["black_dom"])
_WEB_BLACK_TITLE = tuple(_FILTERS["black_title"])
_WEB_VIDEO_DOMS = tuple(_FILTERS["video_doms"])
_WEB_PAID_DOMS = tuple(_FILTERS["paid_doms"])
_WEB_JUNK_TITLE = tuple(_FILTERS["junk_title"])
_WEB_FREE_SITES = tuple(_FILTERS["free_sites"])
_WEB_PIRATE_DOMS = tuple(_FILTERS["pirate_doms"])

def _web_engine_bing_videos(q):
    """必应视频垂直搜索（/videos/search）：返回各平台真实视频页（抖音/B站/央视频等公开内容），
    标题取自视频卡封面 alt。与网页搜索互补，显著提升「全网搜视频」召回。"""
    kw = urllib.parse.quote(q)
    u = "https://www.bing.com/videos/search?q=%s&setlang=zh-hans" % kw
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    items, seen = [], set()
    for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url, inner = m.group(1), m.group(2)
        low = url.lower()
        if "bing.com/" in low:
            continue
        if not any(k in low for k in ("video", "play", "bangumi", "/v/", "bvid=", "/av", "?modal")):
            continue
        tm = re.search(r'alt="([^"]{4,160})"', inner)
        title = tm.group(1) if tm else ""
        if not title:
            tm2 = re.search(r'title="([^"]{4,160})"', inner)
            title = tm2.group(1) if tm2 else ""
        if not title:
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title[:120], "url": url,
                      "summary": "必应视频搜索 · 全网公开视频", "engine": "必应视频"})
        if len(items) >= 18:
            break
    return items


def _web_engine_bing(q):
    """Bing 网页搜索"""
    kw = urllib.parse.quote(q)
    u = "https://cn.bing.com/search?q=" + kw + "&count=30"
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        body_t = re.sub(r"<[^>]+>", " ", m.group(3))
        body_t = re.sub(r"\s+", " ", body_t).strip()[:160]
        if url.startswith("http") and title:
            out.append({"title": title[:120], "url": url, "summary": body_t})
    return out

def _web_engine_baidu(q):
    """百度网页搜索（结果为百度跳转链接，解析播放时 yt-dlp 自动跟随）"""
    kw = urllib.parse.quote(q)
    u = "https://www.baidu.com/s?wd=" + kw + "&rn=15"
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http") and title:
            out.append({"title": title[:120], "url": url, "summary": ""})
    return out

def _web_engine_sogou(q):
    """搜狗网页搜索"""
    kw = urllib.parse.quote(q)
    u = "https://www.sogou.com/web?query=" + kw
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http") and title:
            out.append({"title": title[:120], "url": url, "summary": ""})
    return out

def _web_engine_360(q):
    """360 网页搜索"""
    kw = urllib.parse.quote(q)
    u = "https://www.so.com/s?q=" + kw
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http") and title:
            out.append({"title": title[:120], "url": url, "summary": ""})
    return out

_WEB_FREE_SITES = ("tv.cctv.com", "jishi.cctv.com", "bilibili.com", "1905.com",
                  "open.163.com", "icourse163.org", "ixigua.com", "v.douyin.com",
                  "v.kuaishou.com", "pearvideo.com", "tv.cctv.com/videos")

def _web_engine_site(kw, site):
    """站内定向搜索：site:免费平台 关键词（提高免费正片召回）"""
    q = urllib.parse.quote(kw)
    u = "https://cn.bing.com/search?q=site%%3A%s+%s&count=12" % (site, q)
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity"}
    try:
        status, body = http_get(u, timeout=12, headers=hd)
    except Exception:
        return []
    if status != 200:
        return []
    html = body.decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>', html, re.S):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        body_t = re.sub(r"<[^>]+>", " ", m.group(3))
        body_t = re.sub(r"\s+", " ", body_t).strip()[:160]
        if url.startswith("http") and title:
            out.append({"title": title[:120], "url": url, "summary": body_t})
    return out

# 内容过滤开关：存在 data/.nofilter 时跳过全部过滤（调试模式，显示全部搜索结果）
def _nofilter_on():
    return os.path.exists(os.path.join(DATA, ".nofilter"))

def _web_video_search(q, page=1):
    """全网视频搜索：多搜索引擎（Bing/百度/搜狗）并行抓取 → 融合去重 →
    按「视频平台域名 + 标题关键词」标记视频内容。仅公开网页，不采集个人隐私；
    解析播放由 yt-dlp 尝试，需会员/付费的内容会明确提示失败。"""
    kw = q.strip()
    if not kw:
        return {"ok": False, "error": "请输入关键词"}
    raw = []
    for eng in (_web_engine_bing_videos, _web_engine_bing, _web_engine_baidu, _web_engine_sogou, _web_engine_360):
        try:
            raw += eng(kw)
        except Exception:
            pass
    for site in _WEB_FREE_SITES:
        try:
            raw += _web_engine_site(kw, site)
        except Exception:
            pass
    # 融合去重（按 URL，百度跳转链接去 query 尾参后再去重）
    seen, items = set(), []
    for it in raw:
        url = it["url"]
        low = url.lower()
        if not _nofilter_on():
            if any(b in low for b in _WEB_BLACK_DOM):
                continue
            if any(d in low for d in _WEB_PIRATE_DOMS):
                continue  # 非授权/盗版影视站（默认过滤，用户可在「过滤配置」中放行）
            if any(k in it["title"] for k in _WEB_BLACK_TITLE):
                continue
        key = url.split("&")[0]
        if key in seen:
            continue
        seen.add(key)
        dom = (urllib.parse.urlparse(url).netloc or "").replace("www.", "")
        title_low = it["title"].lower()
        if not _nofilter_on():
            if any(d in low for d in _WEB_PAID_DOMS):
                continue  # 付费/会员平台直接过滤，仅保留免费公开内容
            if any(k in it["title"] for k in _WEB_JUNK_TITLE):
                continue  # 解说/速看/小说/书评等非正片内容过滤
            if not any(d in low for d in _WEB_VIDEO_DOMS):
                continue  # 非免费视频平台域名过滤（不再按标题词判定，杜绝小说/解说/官网混入）
        lv = 2
        dom_cnt = {}
        for _x in items:
            dom_cnt[_x["domain"]] = dom_cnt.get(_x["domain"], 0) + 1
        if dom_cnt.get(dom, 0) >= 10:
            continue  # 同域名配额平衡：单站最多 10 条，配额让给更多来源（抖音片段大户降噪）
        items.append({"title": it["title"][:120], "url": url, "domain": dom[:40],
                      "summary": it["summary"][:160],
                      "hint": True, "level": lv,
                      "engine": it.get("engine", "必应/百度/搜狗/360")})
        if len(items) >= 30:
            break
    items.sort(key=lambda x: (-x.get("level", 0), x["engine"]))
    return {"ok": True, "items": items, "total": len(items), "engines": ["必应", "百度", "搜狗"]}

def api_media_nofilter(body):
    """内容过滤开关：on=True=开启内容过滤(仅免费公开)；on=False=关闭过滤进入调试模式(显示全部结果)"""
    on = (body or {}).get("on")
    f = os.path.join(DATA, ".nofilter")
    if isinstance(on, bool):
        if on:
            try:
                os.remove(f)  # 开启过滤 → 删除调试标记
            except Exception:
                pass
        else:
            try:
                with open(f, "w") as _f:
                    _f.write("nofilter debug")  # 关闭过滤 → 创建调试标记
            except Exception:
                pass
    return {"ok": True, "on": not os.path.exists(f), "note": "on=内容过滤开启(仅免费公开视频) / off=调试模式(显示全部结果)"}

def api_media_filters(body):
    """内容过滤名单配置：空 body 查询全部；{list:'paid_doms',op:'add'|'remove'|'set',value:...} 修改"""
    global _FILTERS, _WEB_BLACK_DOM, _WEB_BLACK_TITLE, _WEB_VIDEO_DOMS, _WEB_PAID_DOMS, _WEB_JUNK_TITLE, _WEB_FREE_SITES
    lst = (body or {}).get("list")
    op = (body or {}).get("op")
    val = (body or {}).get("value")
    if lst in _FILTER_KEYS and op in ("add", "remove", "set"):
        cur = list(_FILTERS[lst])
        if op == "add" and isinstance(val, str) and val.strip() and val.strip() not in cur:
            cur.append(val.strip())
        elif op == "remove" and isinstance(val, str):
            cur = [x for x in cur if x != val.strip()]
        elif op == "set" and isinstance(val, list):
            cur = [str(x).strip() for x in val if str(x).strip()]
        _FILTERS[lst] = cur
        try:
            with open(_FILTER_FILE, "w", encoding="utf-8") as f:
                json.dump(_FILTERS, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log("filters save error: " + repr(e))
        # 同步生效
        _WEB_BLACK_DOM = tuple(_FILTERS["black_dom"])
        _WEB_BLACK_TITLE = tuple(_FILTERS["black_title"])
        _WEB_VIDEO_DOMS = tuple(_FILTERS["video_doms"])
        _WEB_PAID_DOMS = tuple(_FILTERS["paid_doms"])
        _WEB_JUNK_TITLE = tuple(_FILTERS["junk_title"])
        _WEB_FREE_SITES = tuple(_FILTERS["free_sites"])
        _WEB_PIRATE_DOMS = tuple(_FILTERS["pirate_doms"])
    return {"ok": True, "filters": {k: _FILTERS[k] for k in _FILTER_KEYS}}

def _bili_search(stype, q, page=1):
    """B站 wbi 公开搜索，返回 (items, ok)"""
    items = []
    kw = urllib.parse.quote(q)
    u = ("https://api.bilibili.com/x/web-interface/wbi/search/type?search_type=" + stype
         + "&keyword=" + kw + "&page=" + str(min(page, 50)))
    hd = {"User-Agent": (_DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"),
          "Referer": "https://www.bilibili.com/"}
    status, body = http_get(u, timeout=15, headers=hd)
    if status != 200:
        return items, False
    d = json.loads(body.decode("utf-8", "replace"))
    if d.get("code") != 0:
        return items, False
    res = (d.get("data") or {}).get("result") or []
    for it in res[:30]:
        title = re.sub(r"<[^>]+>", "", it.get("title") or "")
        if stype == "video":
            arcurl = it.get("arcurl") or ""
            if "cheese" in arcurl.lower() or "bilibili.com/cheese" in arcurl.lower():
                continue
            bvid = it.get("bvid") or ""
            dur = it.get("duration") or ""
            dur_txt = dur if isinstance(dur, str) and ":" in dur else (str(int(dur) // 60) + ":" + str(int(dur) % 60).zfill(2)) if str(dur).isdigit() else ""
            items.append({"platform": "B站", "kind": "video", "title": title,
                          "url": arcurl or ("https://www.bilibili.com/video/" + bvid),
                          "bv": bvid, "duration": dur_txt, "author": it.get("author") or "",
                          "play": it.get("play") or 0})
        elif stype == "article":
            cid = it.get("id") or ""
            art_url = it.get("arcurl") or it.get("url") or (("https://www.bilibili.com/read/cv" + str(cid)) if cid else "")
            pub = str(it.get("pubdate") or it.get("pub_time") or "")[:10]
            items.append({"platform": "B站专栏", "kind": "article", "title": title,
                          "url": art_url, "bv": "", "duration": ((it.get("author") or "") + (" · " + pub if pub else "")),
                          "author": it.get("author") or "", "play": it.get("view") or 0})
        elif stype == "live":
            roomid = it.get("roomid") or ""
            online = it.get("online") or 0
            items.append({"platform": "B站直播", "kind": "live", "title": title,
                          "url": ("https://live.bilibili.com/" + str(roomid)) if roomid else "",
                          "bv": "", "duration": "在线 " + str(online) + " 人",
                          "author": it.get("uname") or "", "play": 0})
        elif stype == "media_bangumi":
            sid = it.get("season_id") or ""
            mid = it.get("media_id") or ""
            items.append({"platform": "B站番剧", "kind": "bangumi", "title": title,
                          "url": ("https://www.bilibili.com/bangumi/play/ss" + str(sid)) if sid else (("https://www.bilibili.com/bangumi/media/md" + str(mid)) if mid else ""),
                          "bv": "", "duration": ",".join((it.get("areas") or [])[:2]),
                          "author": it.get("type_name") or "", "play": it.get("order") or 0})
    return items, True



def api_video_search(params):
    """公开视频聚合搜索：B站多类别（视频/直播/番剧/专栏）聚合 + 内容分类。
    仅返回公开内容；付费/会员内容不索引。"""
    q = str(params.get("q") or "").strip()
    if not q:
        return {"ok": False, "error": "请输入关键词"}
    page = int(params.get("page") or 1)
    src = str(params.get("source") or "all")
    want = {"all": ["video", "live", "media_bangumi"],
            "video": ["video"], "live": ["live"], "bangumi": ["media_bangumi"], "article": ["article"]}.get(src, ["video"])
    items = []
    srcs = []
    for st in want:
        try:
            its, ok = _bili_search(st, q, page)
        except Exception:
            its, ok = [], False
        if its:
            items.extend(its)
            nm = {"video": "B站视频", "live": "B站直播", "media_bangumi": "B站番剧", "article": "B站专栏"}.get(st, st)
            srcs.append(nm + "公开搜索")
    # 每条内容打内容分类
    for it in items:
        it["tag"] = _cls_tag(it.get("title") or "")
    tags = []
    seen = {}
    for it in items:
        t = it["tag"]
        seen[t] = seen.get(t, 0) + 1
    tags = [{"name": k, "count": v} for k, v in seen.items()]
    tags.sort(key=lambda x: -x["count"])
    if not items:
        return {"ok": False, "error": "公开搜索暂不可用（B站接口或网络异常）；可去平台站内搜索后把链接粘贴到下方直接播放/下载"}
    return {"ok": True, "items": items, "tags": tags, "sources": srcs,
            "note": "仅索引公开/免费内容；付费会员内容不在结果中；多平台（YouTube/西瓜/爱奇艺）因接口需登录/签名或防盗链，暂不可稳定直连播放"}


def api_url_playinfo(params):
    """GET url=... → yt-dlp 提取直链（公开视频直接播放用）"""
    u = str(params.get("url") or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "请提供 http(s):// 链接"}
    try:
        task.update(phase="解析中")
        cached = _play_cache_get(u)
        if cached is not None:
            if cached.get("err"):
                raise RuntimeError(cached["err"])
            info = cached["info"] or {}
        else:
            try:
                info = _yt_playinfo(u)
            except Exception as pe:
                _play_cache_put(u, err=str(pe)[:160])
                raise
            _play_cache_put(u, info=info)
        info["ok"] = True
        return info
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def api_url_play(self, params):
    """GET /api/url/play?url=<直链>&a=<音频流>&ref=<来源页>
    分离流用 ffmpeg 实时合并成 fMP4 流式转发；单流直接代理（支持 Range）。"""
    u = str(params.get("url") or "").strip()
    a = str(params.get("a") or "").strip()
    ref = str(params.get("ref") or "").strip()
    if not u.startswith(("http://", "https://")):
        self._send(400, "text/plain; charset=utf-8", "bad url"); return
    ua = _DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"
    hd = {"User-Agent": ua, "Accept": "*/*"}
    if ref.startswith(("http://", "https://")):
        hd["Referer"] = ref
    # 分离流：ffmpeg 合并
    if a and a.startswith(("http://", "https://")):
        ff = os.path.join(BASE, "bin", "ffmpeg", "bin", "ffmpeg.exe")
        if os.path.exists(ff):
            try:
                hdrs = "User-Agent: %s\r\nReferer: %s\r\nAccept: */*\r\n" % (ua, ref or "https://www.bilibili.com/")
                proc = subprocess.Popen(
                    [ff, "-headers", hdrs, "-i", u, "-headers", hdrs, "-i", a,
                     "-c", "copy", "-movflags", "frag_keyframe+empty_moov",
                     "-f", "mp4", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "none")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                while True:
                    blk = proc.stdout.read(64 * 1024)
                    if not blk:
                        break
                    try:
                        self.wfile.write(blk)
                    except Exception:
                        break
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            except Exception:
                pass
    rng = self.headers.get("Range")
    try:
        req = urllib.request.Request(u, headers=hd)
        if rng:
            req.add_header("Range", rng)
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.send_response(resp.status)
            ct = resp.headers.get("Content-Type") or "application/octet-stream"
            if "octet-stream" in ct and ("stream" in ct or True):
                low = u.lower().split("?")[0]
                if low.endswith(".mp4"):
                    ct = "video/mp4"
                elif low.endswith(".webm"):
                    ct = "video/webm"
                elif low.endswith(".mov"):
                    ct = "video/quicktime"
            self.send_header("Content-Type", ct)
            for k in ("Content-Length", "Content-Range", "Accept-Ranges"):
                v = resp.headers.get(k)
                if v:
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                blk = resp.read(64 * 1024)
                if not blk:
                    break
                try:
                    self.wfile.write(blk)
                except Exception:
                    break
    except Exception as e:
        try:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(str(e)[:120].encode("utf-8", "replace"))
        except Exception:
            pass


def api_url_analyze(params):
    url = _clean_dup_url(str(params.get("url", "")).strip())
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "请输入 http(s):// 开头的网址"}
    import urllib.request as _ur
    try:
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DeployPanel/1.0)",
                                        "Accept": "text/html,application/json,*/*"})
        with _ur.urlopen(req, timeout=10) as resp:
            raw = resp.read(1024 * 1024 + 1)
            ctype = resp.headers.get("Content-Type", "")
            final = resp.geturl()
            status = resp.status
    except Exception as e:
        return {"ok": False, "error": "抓取失败: " + str(e)[:120]}
    if len(raw) > 1024 * 1024:
        return {"ok": False, "error": "页面超过 1MB，为保护性能已停止"}
    enc = ""
    cl = ctype.lower()
    if "charset=" in cl:
        enc = cl.split("charset=")[-1].split(";")[0].strip().strip('"').strip("'")
    if not enc:
        mc = re.search(rb'<meta[^>]+charset\s*=\s*["\'\s]*([\w-]+)', raw[:8192], re.I)
        if mc:
            enc = mc.group(1).decode("ascii", "ignore")
    if not enc:
        mc = re.search(rb'<meta[^>]+content\s*=\s*["\'][^"\']*charset\s*=\s*([\w-]+)', raw[:8192], re.I)
        if mc:
            enc = mc.group(1).decode("ascii", "ignore")
    text = None
    cands = ([enc] if enc else []) + ["utf-8", "gb18030", "gbk", "latin-1"]
    for c in cands:
        try:
            text = raw.decode(c)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    title = ""
    tm = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if tm:
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip()[:200]
    is_json = "json" in ctype.lower()
    tables = len(re.findall(r"<table", text, re.I))
    lists = len(re.findall(r"<li", text, re.I))
    imgs = len(re.findall(r"<img", text, re.I))
    links = len(re.findall(r"<a[^>]+href=", text, re.I))
    paras = re.findall(r"<p[^>]*>(.*?)</p>", text, re.S | re.I)
    paras = [re.sub(r"<[^>]+>", "", p).strip() for p in paras]
    paras = [p for p in paras if p][:5]
    robots_ok, robots_note = True, "未发现 robots.txt 限制"
    try:
        up = urllib.parse.urlparse(final)
        rq = _ur.Request(up.scheme + "://" + up.netloc + "/robots.txt", headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(rq, timeout=5) as rr:
            rt = rr.read(8192).decode("utf-8", "replace")
            path = up.path or "/"
            if "Disallow: " + path in rt:
                robots_ok, robots_note = False, "robots.txt 明确禁止该路径（Disallow），请勿抓取"
            elif "Disallow: /" in rt:
                robots_ok, robots_note = False, "robots.txt 存在 Disallow: / 全站限制，请勿抓取"
            else:
                robots_note = "robots.txt 未限制该路径，可抓取"
    except Exception:
        robots_note = "未找到 robots.txt"
    img_list = []
    try:
        for m in re.finditer(r'(?:src|data-src)\s*=\s*["\']([^"\' ]+)["\']', text, re.I):
            u = m.group(1)
            if not u.startswith(("http://", "https://")):
                u = urllib.parse.urljoin(final, u)
            if u.startswith(("http://", "https://")) and u not in img_list:
                img_list.append(u)
            if len(img_list) >= 12:
                break
    except Exception:
        pass
    video_list = []
    try:
        for pat in (r'<video[^>]+src=["\']([^"\' ]+)["\']', r'<source[^>]+src=["\']([^"\' ]+)["\']',
                    r'data-src=["\']([^"\' ]+\.(?:mp4|webm|mov|m3u8|flv)(?:\?[^"\' ]*)?)["\']',
                    r'href=["\']([^"\' ]+\.(?:mp4|webm|mov|m3u8|flv)(?:\?[^"\' ]*)?)["\']'):
            for m in re.finditer(pat, text, re.I):
                u = m.group(1)
                if not u.startswith(("http://", "https://")):
                    u = urllib.parse.urljoin(final, u)
                if u.startswith(("http://", "https://")) and u not in video_list:
                    video_list.append(u)
                if len(video_list) >= 10:
                    break
            if len(video_list) >= 10:
                break
    except Exception:
        pass
    sec = _url_security_check(final)
    platform = _detect_platform(urllib.parse.urlparse(final).netloc.lower())
    return {"ok": True, "url": final, "status": status, "title": title, "is_json": is_json,
            "tables": tables, "lists": lists, "imgs": imgs, "links": links, "paras": paras,
            "robots_ok": robots_ok, "robots_note": robots_note, "snippet": text[:600],
            "sec_level": sec["level"], "sec_reasons": sec["reasons"], "img_list": img_list,
            "video_list": video_list, "platform": platform}

BRAND_DOMAINS = ["taobao","tmall","alipay","weixin","wechat","wxpay","qq","tencent","qzone",
              "baidu","jd","jingdong","pinduoduo","meituan","dianping","163","126","paypal",
              "facebook","google","youtube","microsoft","apple","office","amazon","ebay",
              "github","doubao","bytedance","douyin","kuaishou","bilibili","zhihu","weibo",
              "icbc","ccb","boc","abcchina","cmbchina","bankcomm"]
SHORTENER_HOSTS = {"bit.ly","t.cn","dwz.cn","tinyurl.com","goo.gl","is.gd","url.cn","cutt.ly",
                   "shorturl.at","rb.gy","soo.gd","kutt.it"}
BLACKLIST_FILE = os.path.join(DATA, "phishing_blacklist.txt")
_DL_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]


def _lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _norm_domain(h):
    h = h.lower().strip()
    if h.startswith("www."):
        h = h[4:]
    m = re.match(r"^([a-z0-9-]+)\.(com|cn|net|org|io|cc|top|xyz|vip|club|site|info|biz|tv|online|shop|wang|tech|cloud)$", h)
    if m:
        return m.group(1).split("-")[0]
    return h.split(".")[0].split("-")[0] if "." in h else h.split("-")[0]


def _load_blacklist():
    try:
        if os.path.exists(BLACKLIST_FILE):
            return {l.strip().lower() for l in open(BLACKLIST_FILE, encoding="utf-8", errors="replace") if l.strip() and not l.strip().startswith("#")}
    except Exception:
        pass
    return set()


def _url_security_check(url):
    reasons, level = [], "safe"
    up = urllib.parse.urlparse(url)
    host = (up.hostname or "").lower()
    if not url.lower().startswith("https://"):
        reasons.append("非 HTTPS 加密连接（http），数据可能被窃听或篡改")
        level = "warn"
    if up.port and up.port not in (80, 443):
        reasons.append("使用非常规端口 %s，需谨慎" % up.port)
        level = "warn"
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        reasons.append("直连 IP 地址而非域名，为钓鱼站点常见手法")
        level = "warn"
    h2 = host[4:] if host.startswith("www.") else host
    if h2 in SHORTENER_HOSTS:
        reasons.append("短链接服务，最终跳转目标请在下载/访问前人工确认")
        level = "warn"
    bl = _load_blacklist()
    if host in bl or h2 in bl:
        return {"level": "danger", "reasons": ["命中本地恶意/钓鱼域名黑名单（可编辑 data/phishing_blacklist.txt）"]}
    core = _norm_domain(h2)
    if core:
        for b in BRAND_DOMAINS:
            if core == b:
                continue
            lc, lb = len(core), len(b)
            hit = False
            if abs(lc - lb) <= 1 and _lev(core, b) <= 1:
                hit = True  # 单字符变体（ta0bao / paypai / gooogle）
            elif lc == lb + 2 and core.startswith(b[:2]) and _lev(core, b) <= 1:
                hit = True  # 双字符变体且前缀相同
            elif abs(lc - lb) <= 1 and _lev(core, b) <= 2 and core[:3] == b[:3] and core != b:
                hit = True  # 前缀相同的双字符变体（paypai→paypal）
            elif lc > lb + 2 and core.startswith(b) and re.match(r"^[a-z0-9]+$", core[lb:]):
                hit = True  # 品牌名+纯字符后缀（taobao123）
            if hit:
                return {"level": "danger",
                        "reasons": ["域名与知名站点「%s」高度相似（%s），极可能是仿冒钓鱼站" % (b, host)]}
    return {"level": level, "reasons": reasons}


def api_url_check(params):
    url = _clean_dup_url(str(params.get("url", "")).strip())
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "请输入 http(s):// 开头的网址"}
    sec = _url_security_check(url)
    # 跟随跳转检查最终目标（最多 3 跳，HEAD）
    try:
        import urllib.request as _ur
        cur, hops = url, 0
        while hops < 3:
            req = _ur.Request(cur, headers={"User-Agent": _DL_UA_POOL[0], "Accept": "*/*"})
            with _ur.urlopen(req, timeout=8) as resp:
                if resp.status not in (301, 302, 303, 307, 308) or not resp.headers.get("Location"):
                    break
                cur = urllib.parse.urljoin(cur, resp.headers["Location"])
                hops += 1
        fin = _url_security_check(cur)
        if fin["level"] == "danger" and sec["level"] != "danger":
            sec = {"level": "danger", "reasons": sec["reasons"] + ["最终跳转目标被判定为危险：" + cur[:120]]}
        elif fin["level"] == "warn" and sec["level"] == "safe":
            sec = {"level": "warn", "reasons": sec["reasons"] + fin["reasons"]}
        sec["final_url"] = cur
    except Exception as e:
        sec["jump_check"] = "跳转检查失败: " + str(e)[:80]
    return {"ok": True, **sec}


def _pick_filename(resp, url, idx):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8\'')?\"?([^\"\r\n;]+)\"?", cd, re.I)
    if m:
        fn = m.group(1).strip()
        try:
            fn = urllib.parse.unquote(fn)
        except Exception:
            pass
        if fn and "/" not in fn and "\\" not in fn:
            return re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", fn)[:80]
    up = urllib.parse.urlparse(url)
    base = os.path.basename(up.path)
    if base and "." in base:
        return re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", base)[:80]
    ct = resp.headers.get("Content-Type", "").split(";")[0]
    ext = {".png": "png", ".jpeg": "jpg", ".jpg": "jpg", ".gif": "gif", ".webp": "webp",
           ".pdf": "pdf", ".zip": "zip", ".csv": "csv", ".json": "json", ".xml": "xml"}.get(ct, "bin")
    return "download_%d.%s" % (idx, ext)


def _download_one(u, ddir):
    import urllib.request as _ur
    strategies = [
        {"ua": _DL_UA_POOL[0], "ref": None},
        {"ua": _DL_UA_POOL[1], "ref": "/".join(u.split("/")[:3]) + "/"},
        {"ua": _DL_UA_POOL[2], "ref": None, "identity": True},
        {"ua": _DL_UA_POOL[3], "ref": "/".join(u.split("/")[:3]) + "/", "identity": True},
    ]
    last_err = ""
    for i, st in enumerate(strategies):
        try:
            hd = {"User-Agent": st["ua"], "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
            if st.get("ref"):
                hd["Referer"] = st["ref"]
            if st.get("identity"):
                hd["Accept-Encoding"] = "identity"
            req = _ur.Request(u, headers=hd)
            with _ur.urlopen(req, timeout=20) as resp:
                data = resp.read(10 * 1024 * 1024 + 1)
                if len(data) > 10 * 1024 * 1024:
                    last_err = "资源超过 10MB，已停止"
                    continue
                fn = _pick_filename(resp, u, i)
                fp = os.path.join(ddir, fn)
                n = 1
                while os.path.exists(fp):
                    fp = os.path.join(ddir, "%s_%d%s" % (os.path.splitext(fn)[0], n, os.path.splitext(fn)[1]))
                    n += 1
                with open(fp, "wb") as f:
                    f.write(data)
                return {"url": u, "ok": True, "file": os.path.basename(fp), "size": len(data),
                        "strategy": "环境%d" % (i + 1), "ctype": resp.headers.get("Content-Type", "").split(";")[0]}
        except Exception as e:
            last_err = str(e)[:120]
    return {"url": u, "ok": False, "error": last_err, "tried": len(strategies)}


def api_url_download(params):
    urls = params.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u).strip() for u in urls if str(u).strip().startswith(("http://", "https://"))][:20]
    if not urls:
        return {"ok": False, "error": "请提供 http(s):// 的资源地址"}
    ddir = os.path.join(WORKSPACES, "downloads")
    try:
        os.makedirs(ddir, exist_ok=True)
    except Exception:
        ddir = os.path.join(os.path.expanduser("~"), "deploypanel_downloads")
        os.makedirs(ddir, exist_ok=True)
    results = [_download_one(u, ddir) for u in urls]
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "dir": ddir, "total": len(results), "ok_count": ok_n, "results": results}


def _open_with_fallback(url, headers, timeout):
    import urllib.request as _ur
    try:
        return _ur.urlopen(_ur.Request(url, headers=headers), timeout=timeout)
    except Exception:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        return _ur.urlopen(_ur.Request(url, headers=headers), timeout=timeout, context=ctx)


def _stream_fetch(u, ddir, timeout=30, max_mb=300, prog=None):
    """流式下载（分块写盘），返回 (path, size, ctype)；prog(down, total) 进度回调"""
    import urllib.request as _ur
    hd = {"User-Agent": _DL_UA_POOL[0], "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    with _open_with_fallback(u, hd, timeout) as resp:
        ctype = resp.headers.get("Content-Type", "").split(";")[0]
        fn = _pick_filename(resp, u, 0)
        fp = os.path.join(ddir, fn)
        n = 1
        while os.path.exists(fp):
            fp = os.path.join(ddir, "%s_%d%s" % (os.path.splitext(fn)[0], n, os.path.splitext(fn)[1]))
            n += 1
        total = 0
        clen = resp.headers.get("Content-Length")
        try:
            clen = int(clen) if clen else 0
        except Exception:
            clen = 0
        with open(fp, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_mb * 1024 * 1024:
                    f.close()
                    os.remove(fp)
                    raise RuntimeError("超过 %dMB 已停止" % max_mb)
                f.write(chunk)
                if prog:
                    prog(total, clen)
        return fp, total, ctype


def _yt_playinfo(u):
    """yt-dlp 提取直链（不下载文件）。返回 dict(title, url, aurl, ext, height)；
    分离流（B站高清）返回视频流 url + 音频流 aurl，播放时由后端 ffmpeg 实时合并。
    仅供公开视频直接播放。"""
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp 未安装。请运行: python -m pip install yt-dlp 后重启面板。")
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 18, "nocheckcertificate": True, "retries": 1,
            "extractor_retries": 1}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(u, download=False)
    if not info:
        raise RuntimeError("未能解析该链接（可能需登录、版权/地区限制）")
    title = info.get("title") or info.get("id") or "视频"
    fmts = info.get("formats") or []
    def _score(f_):
        h = f_.get("height") or 0
        v = f_.get("vcodec") or ""
        return (1000000 if v.startswith("avc") else (500000 if "h264" in v else 0)) + h
    # 一体流（音视频同文件）优先：避免 B站分离音频流（试看/占位）浏览器解码失败
    comb = None
    for f in fmts:
        proto = f.get("protocol") or ""
        ext = f.get("ext") or ""
        vc = f.get("vcodec")
        ac = f.get("acodec")
        if "m3u8" in proto:
            continue
        if ext not in ("mp4", "webm", "mov", "flv"):
            continue
        if not vc or vc == "none" or not ac or ac == "none":
            continue
        if comb is None or _score(f) > _score(comb):
            comb = f
    vpick = comb
    if comb and comb.get("url"):
        vurl = comb["url"]
        aurl = ""
        acodec = comb.get("acodec") or ""
    else:
        # 无一体流：分离流（视频流 + 纯音频流）
        vpick = None
        for f in fmts:
            proto = f.get("protocol") or ""
            ext = f.get("ext") or ""
            vc = f.get("vcodec")
            if "m3u8" in proto:
                continue
            if ext not in ("mp4", "webm", "mov", "flv"):
                continue
            if not vc or vc == "none":
                continue
            if vpick is None or _score(f) > _score(vpick):
                vpick = f
        apick = None
        for f in fmts:
            ac = f.get("acodec")
            if not ac or ac == "none":
                continue
            if f.get("vcodec") and f.get("vcodec") != "none":
                continue
            proto = f.get("protocol") or ""
            if "m3u8" in proto:
                continue
            if apick is None:
                apick = f
        vurl = None
        if vpick and vpick.get("url"):
            vurl = vpick["url"]
        elif info.get("url"):
            vurl = info["url"]
        aurl = apick.get("url") if apick and apick.get("url") else ""
        acodec = (apick.get("acodec") if apick else "") or ""
    if not vurl:
        raise RuntimeError("未找到可播放的视频直链（可能需要登录或会员）")
    return {"title": title, "url": vurl, "aurl": aurl,
            "ext": (vpick.get("ext") if vpick else (info.get("ext") or "")) or "",
            "height": (vpick.get("height") if vpick else 0) or 0,
            "vcodec": (vpick.get("vcodec") if vpick else "") or "",
            "acodec": acodec}


def _dl_ytdlp(u, ddir, prog=None, out_path=None, prefer_single=False):
    """通用下载：yt-dlp 解析（B站/微博/快手/YouTube 及 1000+ 站点）。失败抛错给引导。
    out_path: 指定输出路径（边下边播时预生成文件名，下载开始即有文件可播）
    prefer_single: 优先单文件 mp4（如 B站 360/480p mp4），下载过程文件持续增长可边下边播"""
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp 未安装。请在本机运行: python -m pip install yt-dlp，然后重启面板。")
    import yt_dlp
    def _hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if prog and total:
                prog(done, total)
    opts = {
        "outtmpl": out_path or os.path.join(ddir, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "progress_hooks": [_hook], "nocheckcertificate": True,
        "socket_timeout": 30, "retries": 3, "concurrent_fragment_downloads": 8,
        "ffmpeg_location": os.path.join(BASE, "bin", "ffmpeg", "bin"),
    }
    if prefer_single:
        # 优先单文件 mp4（边下边播）；无则保持默认（分离流下完合并后也可播）
        try:
            with yt_dlp.YoutubeDL({**opts, "quiet": True, "no_warnings": True}) as ydl0:
                info0 = ydl0.extract_info(u, download=False)
            fmts = info0.get("formats") or []
            single_id = None
            for f in fmts:
                if (f.get("ext") == "mp4" and f.get("vcodec") and f.get("vcodec") != "none"
                        and f.get("acodec") and f.get("acodec") != "none"):
                    single_id = f.get("format_id") or ""
                    break
            if single_id:
                opts["format"] = single_id
        except Exception:
            pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(u, download=True)
        fp = ""
        if info:
            reqs = info.get("requested_downloads") or []
            if reqs:
                fp = reqs[0].get("filepath") or ""
            if not fp:
                fp = ydl.prepare_filename(info)
        if not fp or not os.path.exists(fp):
            raise RuntimeError("yt-dlp 未生成文件（可能需登录、版权/地区限制，或站点接口变动）")
    ext = os.path.splitext(fp)[1].lower()
    ct = _PREVIEW_MIME.get(ext) or "application/octet-stream"
    return fp, os.path.getsize(fp), ct, "yt-dlp 通用"


def _dl_m3u8(u, ddir, timeout=15, prog=None):
    """HLS m3u8：解析分片清单 → 下载分片 → 拼接 .ts；prog(done, total)"""
    import urllib.request as _ur
    hd = {"User-Agent": _DL_UA_POOL[0], "Accept": "*/*"}
    with _open_with_fallback(u, hd, timeout) as resp:
        txt = resp.read(2 * 1024 * 1024).decode("utf-8", "replace")
    segs, key_uri = [], None
    for l in txt.splitlines():
        l = l.strip()
        if l.startswith("#EXT-X-KEY:"):
            m = re.search(r'URI="([^"]+)"', l)
            if m:
                key_uri = urllib.parse.urljoin(u, m.group(1))
        elif l and not l.startswith("#"):
            segs.append(urllib.parse.urljoin(u, l))
    if not segs:
        raise RuntimeError("m3u8 未解析到分片（可能不是标准 HLS 清单）")
    if key_uri:
        raise RuntimeError("分片已加密（AES-128），本机离线无法解密；可用 ffmpeg 处理: ffmpeg -i \"%s\" out.mp4" % u)
    if len(segs) > 500:
        segs = segs[:500]
    parts = []
    for i, s in enumerate(segs):
        try:
            with _open_with_fallback(s, hd, timeout) as rr:
                parts.append(rr.read())
            if prog:
                prog(i + 1, len(segs))
        except Exception as e:
            raise RuntimeError("第 %d/%d 个分片下载失败: %s" % (i + 1, len(segs), str(e)[:80]))
    base = os.path.splitext(os.path.basename(urllib.parse.urlparse(u).path))[0] or "stream"
    base = re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", base)[:60]
    fp = os.path.join(ddir, base + ".ts")
    n = 1
    while os.path.exists(fp):
        fp = os.path.join(ddir, "%s_%d.ts" % (base, n))
        n += 1
    with open(fp, "wb") as f:
        for p in parts:
            f.write(p)
    return fp, sum(len(p) for p in parts), "video/mp2t"


def api_url_dl_video(params):
    urls = params.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    urls = [_clean_dup_url(str(u).strip()) for u in urls if str(u).strip().startswith(("http://", "https://"))][:5]
    if not urls:
        return {"ok": False, "error": "请提供 http(s):// 的视频地址"}
    ddir = os.path.join(WORKSPACES, "downloads", "videos")
    try:
        os.makedirs(ddir, exist_ok=True)
    except Exception:
        ddir = os.path.join(os.path.expanduser("~"), "deploypanel_videos")
        os.makedirs(ddir, exist_ok=True)
    results = []
    for u in urls:
        try:
            if ".m3u8" in u.lower() or ".m3u" in u.lower():
                fp, size, ct = _dl_m3u8(u, ddir)
            else:
                fp, size, ct = _stream_fetch(u, ddir, timeout=60, max_mb=300)
            results.append({"url": u, "ok": True, "file": os.path.basename(fp), "size": size,
                            "ctype": ct, "kind": "m3u8 合并" if ".m3u8" in u.lower() else "直链"})
        except Exception as e:
            results.append({"url": u, "ok": False, "error": str(e)[:150]})
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "dir": ddir, "total": len(results), "ok_count": ok_n, "results": results}


_DL_TASKS = {}
_DL_LOCK = threading.Lock()
_DL_TID = [0]


def _dl_platform(u, ddir, prog=None):
    """平台视频一律走 yt-dlp（自带签名/直链/合并处理）。"""
    return _dl_ytdlp(u, ddir, prog=prog)


def _dl_bilibili(u, ddir, prog=None):
    """B站：解析 window.__playinfo__（未登录可拿低清晰度直链）"""
    import urllib.request as _ur
    hd = {"User-Agent": _DL_UA_POOL[0], "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://www.bilibili.com/"}
    with _open_with_fallback(u, hd, 20) as resp:
        txt = resp.read(4 * 1024 * 1024).decode("utf-8", "replace")
    m = re.search(r"window\.__playinfo__\s*=\s*({.*?})\s*</script>", txt, re.S)
    if not m:
        m2 = re.search(r"__playinfo__\s*=\s*({.*?})\s*</script>", txt, re.S)
        if not m2:
            raise RuntimeError("B站未登录时页面不再内嵌播放直链（接口需登录/签名）。请用：① B站 App/网页「缓存」或「下载」；② 浏览器打开该视频 → F12 → Network → 筛选 media 类型复制 .mp4 地址，粘贴到本面板「一键下载」。面板不做接口对抗。")
        m = m2
    d = json.loads(m.group(1))
    durl = (d.get("data") or {}).get("durl") or []
    if not durl:
        raise RuntimeError("B站返回数据中没有视频流（可能仅会员/登录可见）")
    cand = durl[0].get("url", "")
    if not cand:
        raise RuntimeError("B站视频流地址为空")
    if cand.startswith("blob:"):
        raise RuntimeError("B站视频为 blob 流，无法直链下载（请用 B 站客户端缓存）")
    fp, size, ct = _stream_fetch(cand, ddir, timeout=60, max_mb=500, prog=prog)
    return fp, size, ct, "B站解析"


def _dl_douyin(u, ddir, prog=None):
    """抖音：提取视频 id → 尝试公开 iteminfo 接口；失败给出明确引导（不做加密对抗）"""
    import urllib.request as _ur
    m = re.search(r"modal_id=(\d+)", u) or re.search(r"/video/(\d+)", u) or re.search(r"(\d{15,20})", u)
    if not m:
        raise RuntimeError("未识别到抖音视频 ID，请提供含 modal_id= 或 /video/ 的抖音链接")
    vid = m.group(1)
    last_err = ""
    for api in (
        "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=%s" % vid,
        "https://www.iesdouyin.com/share/video/%s" % vid,
    ):
        try:
            req = _ur.Request(api, headers={"User-Agent": _DL_UA_POOL[0], "Accept-Language": "zh-CN,zh;q=0.9",
                                           "Referer": "https://www.douyin.com/"})
            with _open_with_fallback(api, {"User-Agent": _DL_UA_POOL[0], "Accept-Language": "zh-CN,zh;q=0.9",
                                           "Referer": "https://www.douyin.com/"}, 15) as resp:
                raw = resp.read(2 * 1024 * 1024)
                txt = raw.decode("utf-8", "replace")
            if "item_list" in txt or "aweme_list" in txt or '"video"' in txt[:2000] or 'play_addr' in txt:
                d = json.loads(txt)
                items = d.get("item_list") or d.get("aweme_list") or []
                if items:
                    v = items[0].get("video", {})
                    play = v.get("play_addr", {}).get("url_list") or v.get("download_addr", {}).get("url_list") or []
                    for cand in play:
                        if cand.startswith(("http://", "https://")):
                            try:
                                fp, size, ct = _stream_fetch(cand, ddir, timeout=60, max_mb=500, prog=prog)
                                return fp, size, ct, "抖音解析"
                            except Exception as e:
                                last_err = str(e)[:100]
                                continue
                    raise RuntimeError("已拿到接口数据但未找到可用视频流：%s" % (last_err or "url_list 为空"))
            last_err = "接口返回空数据"
        except Exception as e:
            last_err = str(e)[:120]
    raise RuntimeError("抖音接口未开放（未登录/风控加密，ID %s）。已尝试公开接口失败：%s。请用以下任一方式：① 抖音 App/网页点「保存到相册」；② 浏览器打开该视频 → F12 → Network → 筛选 media 类型复制 .mp4 地址，粘贴到本面板「一键下载」。面板不做接口对抗。" % (vid, last_err))


_PLAY_DIR = os.path.join(DATA, "tmp_play")
os.makedirs(_PLAY_DIR, exist_ok=True)
_PLAY_TASKS = {}
_PLAY_LOCK = threading.Lock()
_PLAY_TID = [0]


def _play_worker(task):
    """临时文件播放：yt-dlp 提取直链 → ffmpeg 边拉边转封装为 fMP4（moov 前置/分片），
    输出文件从第一秒起就是可播容器，播放器任意时刻可边下边播；
    ffmpeg 带 -reconnect 自动重连规避 CDN 长连接限流。不写下载日志/列表。"""
    u = task["url"]
    out = os.path.join(task["dir"], "pl_%s.mp4" % task["id"])
    task.update(path=out)
    ff = os.path.join(BASE, "bin", "ffmpeg", "bin", "ffmpeg.exe")
    logf = os.path.join(task["dir"], "pl_%s.log" % task["id"])
    try:
        info = _yt_playinfo(u)
        vurl = info.get("url") or ""
        aurl = info.get("aurl") or ""
        if not vurl:
            raise RuntimeError("未能解析出直链（可能需登录或会员）")
        ua = _DL_UA_POOL[0] if isinstance(_DL_UA_POOL, list) and _DL_UA_POOL else "Mozilla/5.0"
        ref = "https://www.bilibili.com/"
        hdrs = "User-Agent: %s\r\nReferer: %s\r\nAccept: */*\r\n" % (ua, ref)
        args = [ff, "-hide_banner", "-y",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30",
                "-rw_timeout", "15000000", "-timeout", "15000000",
                "-headers", hdrs, "-i", vurl]
        if aurl:
            args += ["-headers", hdrs, "-i", aurl, "-map", "0:v", "-map", "1:a"]
        else:
            args += ["-map", "0:v", "-map", "0:a?"]
        vc = (info.get("vcodec") or "").lower()
        # 音频统一转码 AAC（B站分离流 m4a copy 进 fMP4 会在浏览器解码失败）
        if vc and (vc.startswith("hvc") or vc.startswith("hev") or vc.startswith("av")):
            # HEVC 浏览器无法直接解码 → 转码 H264（画质 veryfast，可播优先）
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                     "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", out]
        else:
            args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                     "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", out]
        src_dur = float(info.get("duration") or 0)
        ffprobe = os.path.join(BASE, "bin", "ffmpeg", "bin", "ffprobe.exe")
        def _probe_dur(fp):
            try:
                r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", fp], capture_output=True, text=True, timeout=30)
                return float(r.stdout.strip() or 0)
            except Exception:
                return 0.0
        # 转封装（最多 2 次）：拉流不完整（只拉到开头几秒且未报错）时自动重拉
        for _attempt in range(2):
            with open(logf, "wb") as lg:
                proc = subprocess.Popen(args, stdout=lg, stderr=lg)
            while proc.poll() is None:
                try:
                    sz = os.path.getsize(out)
                except Exception:
                    sz = 0
                task.update(bytes_done=sz)
                time.sleep(0.4)
            if proc.returncode != 0:
                raise RuntimeError("转封装失败（rc=%s），该内容可能需登录或会员" % proc.returncode)
            out_dur = _probe_dur(out)
            # 源时长为 0（未知）时不校验；差异阈值 8%
            if src_dur > 0 and out_dur > 0 and abs(out_dur - src_dur) / src_dur > 0.08:
                try:
                    os.remove(out)
                except Exception:
                    pass
                continue  # 拉流不完整，重拉一次
            break
        if src_dur > 0 and out_dur > 0 and abs(out_dur - src_dur) / src_dur > 0.08:
            raise RuntimeError("源站拉流不完整（转出 %ds / 源 %ds），已重试仍失败，请稍后重试" % (int(out_dur), int(src_dur)))
        task.update(done=True, size=os.path.getsize(out), pct=100, bytes_done=os.path.getsize(out))
    except Exception as e:
        task.update(done=True, error=str(e)[:200])
    finally:
        try:
            if os.path.exists(logf):
                os.remove(logf)
        except Exception:
            pass


_PLAY_CACHE = {}  # url -> {"t": time, "info": dict, "err": str}
_PLAY_CACHE_LOCK = threading.Lock()

def _play_cache_get(u):
    with _PLAY_CACHE_LOCK:
        c = _PLAY_CACHE.get(u)
        if c and time.time() - c["t"] < 600:
            return c
    return None

def _play_cache_put(u, info=None, err=None):
    with _PLAY_CACHE_LOCK:
        _PLAY_CACHE[u] = {"t": time.time(), "info": info, "err": err}

def api_url_play_prep(params):
    """POST {url} → 启动临时播放准备任务（完整拉取到 data/tmp_play）"""
    u = str(params.get("url") or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "请提供 http(s):// 链接"}
    with _PLAY_LOCK:
        _PLAY_TID[0] += 1
        tid = "pl%d" % _PLAY_TID[0]
        task = {"id": tid, "url": u, "dir": _PLAY_DIR, "done": False, "pct": 0,
                "path": "", "size": 0, "error": ""}
        _PLAY_TASKS[tid] = task
    threading.Thread(target=_play_worker, args=(task,), daemon=True).start()
    return {"ok": True, "task_id": tid, "path": os.path.join(_PLAY_DIR, "pl_%s.mp4" % tid),
            "streaming": True}


def api_url_play_progress(params):
    tid = str(params.get("task_id", ""))
    with _PLAY_LOCK:
        t = _PLAY_TASKS.get(tid)
        if not t:
            return {"ok": False, "error": "任务不存在或已过期"}
        r = dict(t)
    return {"ok": True, "task_id": tid, "done": r.get("done"), "pct": r.get("pct"),
            "path": r.get("path", ""), "size": r.get("size", 0),
            "bytes_done": r.get("bytes_done", 0), "error": r.get("error", "")}


def api_url_play_clean(params):
    """GET path=… → 删除播放临时文件（仅限 data/tmp_play 内）"""
    fp = str(params.get("path") or "").strip()
    if not fp:
        return {"ok": False, "error": "缺少 path"}
    root = os.path.normpath(_PLAY_DIR)
    real = os.path.normpath(fp)
    if not real.startswith(root) or not os.path.isfile(real):
        return {"ok": False, "error": "仅允许清理播放临时文件"}
    try:
        os.remove(real)
        return {"ok": True, "removed": os.path.basename(real)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _dl_worker(task):
    ddir = task["dir"]
    mode = task.get("mode", "auto")
    results = []
    for idx, u in enumerate(task["urls"]):
        task["cur"] = idx + 1
        task["item_url"] = u
        task["item_prog"] = (0, 0)
        task["item_msg"] = "下载中…"
        try:
            host = urllib.parse.urlparse(u).netloc.lower()
            if mode == "ytdlp":
                fp, size, ct, kind = _dl_ytdlp(u, ddir, prog=lambda d, t: task.update(item_prog=(d, t)))
            elif "bilibili.com" in host or "b23.tv" in host or "douyin.com" in host or "iesdouyin.com" in host:
                fp, size, ct, kind = _dl_platform(u, ddir, prog=lambda d, t: task.update(item_prog=(d, t)))
            elif ".m3u8" in u.lower() or ".m3u" in u.lower():
                fp, size, ct = _dl_m3u8(u, ddir, timeout=20,
                                        prog=lambda d, t: task.update(item_prog=(d, t)))
                kind = "m3u8 合并"
            else:
                fp, size, ct = _stream_fetch(u, ddir, timeout=60, max_mb=300,
                                             prog=lambda d, t: task.update(item_prog=(d, t)))
                kind = "直链"
            try:
                _log_dl(fp, u, kind)
            except Exception:
                pass
            results.append({"url": u, "ok": True, "file": os.path.basename(fp), "size": size,
                            "ctype": ct, "kind": kind})
        except Exception as e:
            results.append({"url": u, "ok": False, "error": str(e)[:150]})
    with _DL_LOCK:
        task["results"] = results
        task["done"] = True
        task["ok_count"] = sum(1 for r in results if r.get("ok"))


def api_url_dl_start(params):
    urls = params.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u).strip() for u in urls if str(u).strip().startswith(("http://", "https://"))][:5]
    if not urls:
        return {"ok": False, "error": "请提供 http(s):// 的资源地址"}
    mode = "ytdlp" if str(params.get("mode", "")).strip().lower() == "ytdlp" else "auto"
    ddir = os.path.join(WORKSPACES, "downloads", "videos")
    try:
        os.makedirs(ddir, exist_ok=True)
    except Exception:
        ddir = os.path.join(os.path.expanduser("~"), "deploypanel_videos")
        os.makedirs(ddir, exist_ok=True)
    with _DL_LOCK:
        _DL_TID[0] += 1
        tid = "dl%d" % _DL_TID[0]
        task = {"id": tid, "dir": ddir, "urls": urls, "cur": 0, "total": len(urls),
                "done": False, "results": [], "ok_count": 0, "item_prog": (0, 0), "item_url": "", "item_msg": "排队中",
                "mode": mode}
        _DL_TASKS[tid] = task
    threading.Thread(target=_dl_worker, args=(task,), daemon=True).start()
    return {"ok": True, "task_id": tid, "total": len(urls)}


def api_url_dl_tasks(params):
    """下载任务面板：进行中任务 + 最近完成结果（含失败原因）"""
    with _DL_LOCK:
        running, done = [], []
        for tid, t in _DL_TASKS.items():
            d = dict(t)
            if d.get("done"):
                done.append({"task_id": tid, "done": True, "ok_count": d.get("ok_count"),
                             "total": d.get("total"), "results": d.get("results") or [],
                             "item_url": d.get("item_url")})
                continue
            down, total = d.get("item_prog", (0, 0))
            pct = int(down * 100 / total) if total else -1
            running.append({"task_id": tid, "pct": pct, "down": down,
                            "item_url": d.get("item_url"), "cur": d.get("cur"), "total": d.get("total")})
    done = done[-3:][::-1]
    return {"ok": True, "running": running, "done": done}


def api_url_dl_progress(params):
    tid = str(params.get("task_id", ""))
    with _DL_LOCK:
        task = _DL_TASKS.get(tid)
        if not task:
            return {"ok": False, "error": "任务不存在或已过期"}
        t = dict(task)
    down, total = t.get("item_prog", (0, 0))
    pct = 0
    if t.get("done"):
        pct = 100
    elif total and total > 0:
        pct = int(down * 100 / total)
    else:
        pct = -1
    return {"ok": True, "task_id": tid, "done": t.get("done"), "cur": t.get("cur"),
            "total": t.get("total"), "item_url": t.get("item_url"), "item_msg": t.get("item_msg"),
            "pct": pct, "down": down, "total_bytes": total,
            "results": t.get("results"), "ok_count": t.get("ok_count")}


def _log_dl(fp, url, kind):
    """下载来源日志：data/download_log.json（path 去重更新，最多留 500 条）"""
    p = os.path.join(DATA, "download_log.json")
    recs = load_json(p, {"items": []})
    norm = os.path.normpath(fp)
    items = [i for i in recs.get("items", []) if os.path.normpath(str(i.get("path", ""))) != norm]
    items.append({"path": fp, "url": url, "ts": time.time(), "kind": kind})
    save_json(p, {"items": items[-500:]})


def _dl_files_list():
    out = []
    root = os.path.join(WORKSPACES, "downloads")
    log = load_json(os.path.join(DATA, "download_log.json"), {"items": []})
    by_path = {os.path.normpath(str(i.get("path", ""))): i for i in log.get("items", [])}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for fn in sorted(files):
            fp = os.path.join(base, fn)
            try:
                st = os.stat(fp)
            except Exception:
                continue
            rec = by_path.get(os.path.normpath(fp), {})
            out.append({"name": fn, "dir": base, "path": fp,
                        "size": st.st_size, "mtime": st.st_mtime,
                        "url": rec.get("url", ""), "kind": rec.get("kind", "")})
    out.sort(key=lambda x: -x["mtime"])
    return out


def api_url_dl_files(params):
    try:
        return {"ok": True, "root": os.path.join(WORKSPACES, "downloads"), "files": _dl_files_list()[:60]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


_PREVIEW_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".ts": "video/mp2t", ".m3u8": "application/vnd.apple.mpegurl",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".aac": "audio/aac",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
    ".py": "text/plain; charset=utf-8", ".js": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".csv": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8", ".xml": "text/plain; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8", ".yml": "text/plain; charset=utf-8",
    ".html": "text/plain; charset=utf-8", ".ini": "text/plain; charset=utf-8",
}


def api_url_dl_open(params):
    fp = str(params.get("path", ""))
    root = os.path.normpath(os.path.join(WORKSPACES, "downloads"))
    try:
        real = os.path.normpath(fp)
        if not os.path.normcase(real).startswith(os.path.normcase(root)):
            return {"ok": False, "error": "仅允许打开面板下载目录内的文件"}
        if os.path.isdir(real):
            os.startfile(real)
        elif os.path.isfile(real):
            os.startfile(os.path.dirname(real))
            os.startfile(real)
        else:
            return {"ok": False, "error": "路径不存在"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def api_url_dl_delete(params):
    """批量删除下载目录内文件/子目录（白名单校验，禁止删根目录）"""
    import shutil as _sh
    paths = params.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    root = os.path.normpath(os.path.join(WORKSPACES, "downloads"))
    deleted, failed = [], []
    for fp in paths:
        real = os.path.normpath(str(fp))
        if not os.path.normcase(real).startswith(os.path.normcase(root)):
            failed.append({"path": fp, "error": "不在下载目录内"})
            continue
        if real == root:
            failed.append({"path": fp, "error": "不允许删除根目录"})
            continue
        if not os.path.exists(real):
            failed.append({"path": fp, "error": "不存在"})
            continue
        try:
            if os.path.isfile(real):
                os.remove(real)
            elif os.path.isdir(real):
                _sh.rmtree(real)
            else:
                failed.append({"path": fp, "error": "无法删除"})
                continue
            deleted.append(fp)
        except Exception as e:
            failed.append({"path": fp, "error": str(e)[:80]})
    if deleted:
        try:
            lp = os.path.join(DATA, "download_log.json")
            recs = load_json(lp, {"items": []})
            dead = {os.path.normpath(x) for x in deleted}
            recs["items"] = [i for i in recs.get("items", []) if os.path.normpath(str(i.get("path", ""))) not in dead]
            save_json(lp, recs)
        except Exception:
            pass
    return {"ok": True, "deleted": deleted, "failed": failed}


def api_url_gen_script(params):
    url = _clean_dup_url(str(params.get("url", "")).strip())
    mode = str(params.get("mode", "auto"))
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "请输入 http(s):// 开头的网址"}
    if mode not in SCRIPT_MAP:
        mode = "auto"
    title = url
    try:
        a = api_url_analyze({"url": url})
        if a.get("ok") and a.get("title"):
            title = a["title"]
    except Exception:
        pass
    script = SCRIPT_MAP[mode].format(title=title[:40], url=url)
    return {"ok": True, "script": script, "name": "crawl_%s.py" % mode, "mode": mode}

def api_url_run_script(params):
    script = str(params.get("script", ""))
    if not script.strip():
        return {"ok": False, "error": "脚本为空"}
    if len(script) > 60000:
        return {"ok": False, "error": "脚本过大"}
    # 依赖检查与自动安装（requests / beautifulsoup4）
    try:
        import importlib.util as _iu
        _need = []
        if _iu.find_spec("requests") is None:
            _need.append("requests")
        if _iu.find_spec("bs4") is None:
            _need.append("beautifulsoup4")
    except Exception:
        _need = ["requests", "beautifulsoup4"]
    if _need:
        try:
            _ins = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet"] + _need,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "依赖安装超时，请稍后重试或手动执行: pip install %s" % " ".join(_need)}
        if _ins.returncode != 0:
            return {"ok": False, "error": "缺少依赖 %s 且自动安装失败：%s" % (",".join(_need), ((_ins.stderr or _ins.stdout) or "")[-200:])}
    crawls = os.path.join(WORKSPACES, "crawls")
    try:
        os.makedirs(crawls, exist_ok=True)
    except Exception:
        crawls = os.path.join(os.path.expanduser("~"), "deploypanel_crawls")
        os.makedirs(crawls, exist_ok=True)
    fp = os.path.join(crawls, "_run_crawl.py")
    with open(fp, "w", encoding="utf-8", newline="") as f:
        f.write(script)
    try:
        p = subprocess.run([sys.executable, fp], cwd=crawls, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.stderr else "")
        if len(out) > 300000:
            out = out[:300000] + "\n...(输出过长已截断)"
        return {"ok": True, "output": out, "exit": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "脚本运行超时（20 秒已中止）"}
    except Exception as e:
        return {"ok": False, "error": "运行失败: " + str(e)[:120]}

# ---------- HTTP 服务 ----------
def api_search(params):
    query = params.get("q", "").strip()
    if not query:
        return {"ok": False, "error": "请输入搜索关键词"}
    r = github_search(query)
    if not r.get("ok") or not r["items"]:
        off = github_search_offline(query)
        if off["items"]:
            off["note"] = "当前使用离线缓存结果（联网失败：" + r.get("error", "") + "）"
            return off
        return {"ok": False, "error": r.get("error", "无结果")}
    return r

def api_repo(params):
    full = params.get("full_name", "")
    if "/" not in full:
        return {"ok": False, "error": "仓库格式应为 owner/name"}
    a = repo_analysis(full)
    return {"ok": True, **a}

def api_audit(params):
    full = params.get("full_name", "")
    if "/" not in full:
        return {"ok": False, "error": "仓库格式应为 owner/name"}
    report = run_audit(full)
    return {"ok": True, "audit": report}

def api_download(params):
    full = params.get("full_name", "")
    if "/" not in full:
        return {"ok": False, "error": "仓库格式应为 owner/name"}
    r = download_repo(full)
    if not r.get("ok"):
        return r
    env = detect_env(r["path"])
    return {"ok": True, "path": r["path"], "cached": r.get("cached", False), "env": env}

def api_deploy(params):
    full = params.get("full_name", "")
    path = params.get("path", "")
    if "/" not in full or not os.path.isdir(path):
        return {"ok": False, "error": "参数错误：需要 full_name 与已下载的 path"}
    # 部署前强制审核（在线可自动跑，离线用缓存/已生成的报告）
    audit = None
    try:
        audit = run_audit(full)
        if not audit.get("deploy_allowed"):
            return {"ok": False, "deploy_blocked": True, "audit": audit,
                    "error": "GPL 律师审核未通过（" + audit["verdict_zh"] + "），部署已阻止。请先阅读审核报告。"}
    except Exception as e:
        audit = {"verdict_zh": "审核异常", "deploy_allowed": False}
        return {"ok": False, "error": "审核执行失败：" + str(e)}
    env = detect_env(path)
    dep = deploy_project(full, path, env)
    return {"ok": True, "deploy": dep, "audit": audit}

def api_export(params):
    full = params.get("full_name", "")
    path = params.get("path", "")
    if "/" not in full:
        return {"ok": False, "error": "仓库格式应为 owner/name"}
    if not path or not os.path.isdir(path):
        safe = full.replace("/", "__").replace("\\", "_").replace(":", "_")
        cand = os.path.join(WORKSPACES, safe)
        if os.path.isdir(cand) and os.listdir(cand):
            path = cand
        else:
            return {"ok": False, "error": "项目尚未下载，请先在「项目搜索」中下载该项目"}
    env = detect_env(path)
    audit = None
    try:
        audit = run_audit(full)
    except Exception:
        pass
    zip_path = export_package(full, path, env, audit)
    return {"ok": True, "zip_path": zip_path, "zip_name": os.path.basename(zip_path)}

def api_settings(params):
    return {"ok": True, "hint": "面板不保存任何凭据；GitHub 以匿名公共限额访问"}

def api_status(params):
    return {"ok": True, "app": "DeployPanel", "version": "1.0", "offline_ready": True,
            "data_dir": DATA, "workspaces": WORKSPACES}

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/" or parsed.path == "/index.html" or parsed.path == "/app.html":
                with open(os.path.join(BASE, "app.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if parsed.path == "/api/video/search":
                return self._send(200, json.dumps(api_video_search(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_tasks":
                return self._send(200, json.dumps(api_url_dl_tasks(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/play_progress":
                return self._send(200, json.dumps(api_url_play_progress(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/play_clean":
                return self._send(200, json.dumps(api_url_play_clean(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/playinfo":
                return self._send(200, json.dumps(api_url_playinfo(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/play":
                api_url_play(self, params)
            if parsed.path == "/api/url/analyze":
                return self._send(200, json.dumps(api_url_analyze(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/check":
                return self._send(200, json.dumps(api_url_check(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/gen_script":
                return self._send(200, json.dumps(api_url_gen_script(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_progress":
                return self._send(200, json.dumps(api_url_dl_progress(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_files":
                return self._send(200, json.dumps(api_url_dl_files(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/preview":
                fp = params.get("path", "")
                dl_root = os.path.normpath(os.path.join(WORKSPACES, "downloads"))
                play_root = os.path.normpath(_PLAY_DIR)
                real = os.path.normpath(fp)
                in_dl = os.path.normcase(real).startswith(os.path.normcase(dl_root))
                in_play = os.path.normcase(real).startswith(os.path.normcase(play_root))
                if not (in_dl or in_play):
                    return self._send(404, b"not found", "text/plain; charset=utf-8")
                def _play_task_done():
                    with _PLAY_LOCK:
                        for _t in _PLAY_TASKS.values():
                            if _t.get("path") == real and _t.get("done"):
                                return True
                    return False
                if not os.path.isfile(real):
                    # 播放任务解析/启动中：等临时文件创建（最多 15s，播放器请求超时安全区）
                    if in_play:
                        deadline = time.time() + 15
                        while time.time() < deadline and not os.path.isfile(real):
                            time.sleep(0.15)
                    if not os.path.isfile(real):
                        return self._send(404, b"not found", "text/plain; charset=utf-8")
                ext = os.path.splitext(real)[1].lower()
                ctype = _PREVIEW_MIME.get(ext, "application/octet-stream")
                size = os.path.getsize(real)
                # 文件刚创建（过小）且任务未完成：等有足够分片头（最多 8s）
                if in_play and size < 2097152 and not _play_task_done():
                    deadline = time.time() + 8
                    while time.time() < deadline and os.path.getsize(real) < 2097152:
                        if _play_task_done():
                            break
                        time.sleep(0.12)
                    size = os.path.getsize(real)
                # 边下边播：请求超出已下载位置且任务未完成 → 短等待（最多 15s）后立即返回当前数据，
                # 播放器卡缓冲时由前端 reload 从当前位置续播
                if in_play:
                    rng0 = self.headers.get("Range", "")
                    if rng0.startswith("bytes="):
                        mm = re.match(r"bytes=(\d*)-(\d*)", rng0)
                        if mm:
                            r_end = int(mm.group(2) or (size - 1))
                            if r_end >= size and not _play_task_done():
                                deadline = time.time() + 15
                                while time.time() < deadline and os.path.getsize(real) <= r_end:
                                    if _play_task_done():
                                        break
                                    time.sleep(0.12)
                                size = os.path.getsize(real)
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    m = re.match(r"bytes=(\d*)-(\d*)", rng)
                    if m:
                        start = int(m.group(1) or 0)
                        end = int(m.group(2) or (size - 1))
                        if start >= size:
                            if in_play and not _play_task_done():
                                # 任务未完成：返回空 200（不 416），前端 reload 续播
                                return self._send(200, b"", ctype, {"Accept-Ranges": "bytes", "Cache-Control": "no-store"})
                            return self._send(416, b"", "text/plain; charset=utf-8")
                        end = min(end, size - 1)
                        with open(real, "rb") as f:
                            f.seek(start)
                            body = f.read(end - start + 1)
                        return self._send(206, body, ctype, {"Content-Range": "bytes %d-%d/%d" % (start, end, size),
                                                             "Accept-Ranges": "bytes",
                                                             "Cache-Control": "no-store"})
                with open(real, "rb") as f:
                    body = f.read()
                return self._send(200, body, ctype, {"Accept-Ranges": "bytes", "Cache-Control": "no-store"})
            if parsed.path == "/api/url/run_script":
                return self._send(200, json.dumps(api_url_run_script(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/search":
                return self._send(200, json.dumps(api_search(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/suggest":
                return self._send(200, json.dumps(api_suggest(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/repo":
                return self._send(200, json.dumps(api_repo(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/audit":
                return self._send(200, json.dumps(api_audit(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/download":
                return self._send(200, json.dumps(api_download(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/deploy":
                return self._send(200, json.dumps(api_deploy(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/export":
                return self._send(200, json.dumps(api_export(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/settings":
                return self._send(200, json.dumps(api_settings(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/deploy-list":
                records = load_json(os.path.join(DATA, "deployments.json"), {"items": []})
                items = []
                for r in records.get("items", []):
                    if not isinstance(r, dict):
                        continue
                    env = r.get("env")
                    if not isinstance(env, dict):
                        env = {"type": "static", "zh": "静态站点", "runtime": None,
                               "deps": [], "start_hint": "", "icon": "🌐"}
                    items.append({
                        "repo": r.get("repo", "unknown"),
                        "path": r.get("path", "") or "",
                        "env": env,
                        "script": r.get("script", "") or "",
                        "ts": r.get("ts", 0) or 0,
                    })
                return self._send(200, json.dumps({"items": items}, ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/open-folder":
                return self._send(200, json.dumps(api_open_folder(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/delete-project":
                return self._send(200, json.dumps(api_delete_project(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/service":
                return self._send(200, json.dumps(api_service(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/scan":
                return self._send(200, json.dumps(api_db_scan(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/tables":
                return self._send(200, json.dumps(api_db_tables(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/table":
                return self._send(200, json.dumps(api_db_table(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/insert":
                return self._send(200, json.dumps(api_db_insert(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/update":
                return self._send(200, json.dumps(api_db_update(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/delete":
                return self._send(200, json.dumps(api_db_delete(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/add-column":
                return self._send(200, json.dumps(api_db_add_column(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/db/create-table":
                return self._send(200, json.dumps(api_db_create_table(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/blocks":
                return self._send(200, json.dumps(api_blocks(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/blocks/save":
                return self._send(200, json.dumps(api_blocks_save(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/blocks/delete":
                return self._send(200, json.dumps(api_blocks_delete(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/run":
                return self._send(200, json.dumps(api_flow_run(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/export":
                return self._send(200, json.dumps(api_flow_export(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/save":
                return self._send(200, json.dumps(api_flow_save(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/load":
                return self._send(200, json.dumps(api_flow_load(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/list":
                return self._send(200, json.dumps(api_flow_list(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/flow/delete":
                return self._send(200, json.dumps(api_flow_delete(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/components/save":
                return self._send(200, json.dumps(api_app_comp_save(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/components/delete":
                return self._send(200, json.dumps(api_app_comp_delete(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/components":
                return self._send(200, json.dumps(api_app_components(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/save":
                return self._send(200, json.dumps(api_app_save(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/load":
                return self._send(200, json.dumps(api_app_load(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/list":
                return self._send(200, json.dumps(api_app_list(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/delete":
                return self._send(200, json.dumps(api_app_delete(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/generate":
                return self._send(200, json.dumps(api_app_generate(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/run":
                return self._send(200, json.dumps(api_app_run(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/stop":
                return self._send(200, json.dumps(api_app_stop(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/status":
                return self._send(200, json.dumps(api_app_status(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/dbview":
                return self._send(200, json.dumps(api_app_dbview(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/files":
                return self._send(200, json.dumps(api_app_files(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/dbexec":
                return self._send(200, json.dumps(api_app_db_exec(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path.startswith("/uploads/"):
                fn = os.path.basename(parsed.path)
                fp = os.path.join(UPLOADS, fn)
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "image/png")
                return self._send(404, json.dumps({"ok": False, "error": "图片不存在"}).encode("utf-8"))
            if parsed.path == "/api/app/fetch-img":
                return self._send(200, json.dumps(api_app_fetch_img(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/status":
                return self._send(200, json.dumps(api_status(params), ensure_ascii=False).encode("utf-8"))
            if parsed.path.startswith("/api/export-file"):
                zip_path = params.get("zip", "")
                if zip_path and os.path.exists(zip_path):
                    with open(zip_path, "rb") as f:
                        return self._send(200, f.read(), "application/zip",
                                          {"Content-Disposition": "attachment; filename=" + os.path.basename(zip_path)})
                return self._send(404, json.dumps({"ok": False, "error": "文件不存在"}).encode("utf-8"))
            return self._send(404, json.dumps({"ok": False, "error": "not found"}).encode("utf-8"))
        except Exception as e:
            log("handler error: " + repr(e))
            return self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln) if ln else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        try:
            if parsed.path == "/api/app/upload":
                return self._send(200, json.dumps(api_app_upload(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/app/dbexec":
                return self._send(200, json.dumps(api_app_db_exec(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/run_script":
                return self._send(200, json.dumps(api_url_run_script(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/download":
                return self._send(200, json.dumps(api_url_download(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_video":
                return self._send(200, json.dumps(api_url_dl_video(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/episodes":
                return self._send(200, json.dumps(api_media_episodes(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/article":
                return self._send(200, json.dumps(api_media_article(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/manga":
                return self._send(200, json.dumps(api_media_manga(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/filters":
                return self._send(200, json.dumps(api_media_filters(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/nofilter":
                return self._send(200, json.dumps(api_media_nofilter(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/media/websearch":
                q = (body or {}).get("q", "")
                page = int((body or {}).get("page") or 1)
                if not q:
                    return self._send(200, json.dumps({"ok": False, "error": "请输入关键词"}, ensure_ascii=False).encode("utf-8"))
                return self._send(200, json.dumps(_web_video_search(q, page), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/play_prep":
                return self._send(200, json.dumps(api_url_play_prep(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_start":
                return self._send(200, json.dumps(api_url_dl_start(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_open":
                return self._send(200, json.dumps(api_url_dl_open(body), ensure_ascii=False).encode("utf-8"))
            if parsed.path == "/api/url/dl_delete":
                return self._send(200, json.dumps(api_url_dl_delete(body), ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            log("handler post error: " + repr(e))
            return self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
        return self._send(404, json.dumps({"ok": False, "error": "not found"}).encode("utf-8"))

    def log_message(self, *a):
        pass

def main():
    log("DeployPanel 启动: http://127.0.0.1:" + str(PORT))
    log("工作目录: " + WORKSPACES)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        log("已停止")

if __name__ == "__main__":
    main()
