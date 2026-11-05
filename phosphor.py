#!/usr/bin/env python3
"""Phosphor - a fast, light API client with a phosphor-green terminal look.

Pure Python + Tkinter, no third-party packages, one file.
    sudo apt install python3-tk
    python3 phosphor.py

Shortcuts: Ctrl+Enter send | Ctrl+S save | Ctrl+K quick open | Ctrl+T new tab | Ctrl+W close tab
           Ctrl+Tab next tab | Ctrl+L focus URL | Ctrl+Shift+E environments | Ctrl +/- zoom
"""
import base64
import copy
import datetime
import http.cookiejar
import http.cookies
import json
import mimetypes
import operator
import os
import queue
import random
import re
import shlex
import ssl
import threading
import time
import traceback
import uuid
import zlib
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from xml.dom import minidom

APP = "Phosphor"
_CFG_BASE = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CFG_DIR = os.path.join(_CFG_BASE, "phosphor")
DATA_FILE = os.path.join(CFG_DIR, "data.json")
OLD_DATA_FILE = os.path.join(_CFG_BASE, "minipost", "data.json")  # imported once if you used Phosphor
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
SHORT = {"GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH", "DELETE": "DEL", "HEAD": "HEAD", "OPTIONS": "OPT"}
RAW_TYPES = {"JSON": "application/json", "Text": "text/plain", "XML": "application/xml",
             "HTML": "text/html", "JavaScript": "application/javascript"}
MAX_READ = 25 * 1024 * 1024      # never download more than this into memory
MAX_SHOW = 1_500_000             # max characters rendered in the response view
MAX_PRETTY = 8 * 1024 * 1024     # skip pretty-printing above this size
HL_LIMIT = 400_000               # skip syntax highlighting above this size
CODE_KINDS = ["cURL", "Python (requests)", "JavaScript (fetch)"]

PALETTES = {
    "dark": dict(
        bg="#0a0f0c", bg2="#070b09", panel="#101712", text_bg="#0c130e", fg="#d6f5e1", muted="#6f8f7a",
        border="#1f3025", btn="#16211a", btn_h="#203126", accent="#39ff88", accent_h="#7dffb0",
        accent_fg="#03140a", accent_t="#39ff88", selbg="#1a4a2f", ok="#39ff88", warn="#ffd166",
        err="#ff5c7a", pill_fg="#03140a", info="#5cc8ff", jkey="#6ee7ff", jstr="#9dff7a", jnum="#ffc46b",
        jkw="#ff7ad9", found="#ffe45c", m_GET="#39ff88", m_POST="#ffd166", m_PUT="#5cc8ff",
        m_PATCH="#c792ff", m_DELETE="#ff5c7a", m_HEAD="#6ee7ff", m_OPTIONS="#9aa6a0"),
    "light": dict(
        bg="#f2f7f3", bg2="#e7efe9", panel="#ffffff", text_bg="#ffffff", fg="#0e1f14", muted="#587264",
        border="#cddcd2", btn="#e3ece6", btn_h="#d3e2d8", accent="#1fe07a", accent_h="#4eeb97",
        accent_fg="#04170b", accent_t="#0a8a45", selbg="#bdf0d2", ok="#0a8a45", warn="#a86a00",
        err="#d63b5c", pill_fg="#ffffff", info="#1d7fc4", jkey="#0e7490", jstr="#15803d", jnum="#b45309",
        jkw="#a21caf", found="#ffe45c", m_GET="#0a8a45", m_POST="#a86a00", m_PUT="#1d7fc4",
        m_PATCH="#7c3aed", m_DELETE="#d63b5c", m_HEAD="#0e7490", m_OPTIONS="#587264"),
}


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# =============================================================== data / storage
def uid():
    return uuid.uuid4().hex[:10]


def new_state():
    return {"name": "", "method": "GET", "url": "", "params": [], "headers": [],
            "auth": {"type": "None", "token": "", "user": "", "pass": "", "key": "", "value": "", "in": "Header"},
            "body": {"mode": "none", "raw_type": "JSON", "raw": "", "form": [], "urlenc": [], "file": ""},
            "tests": "", "opts": {"timeout": 30, "verify": True, "follow": True}, "ref": None}


def norm_state(s):
    base = new_state()
    for k, v in (s or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def load_data():
    d, migrated = {}, False
    for path in (DATA_FILE, OLD_DATA_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            migrated = path == OLD_DATA_FILE
            break
        except Exception:
            continue
    if migrated:
        d["theme"] = "dark"
    for k, v in (("collections", []), ("history", []), ("envs", []), ("globals", []), ("active_env", ""),
                 ("theme", "dark"), ("session", {}), ("geometry", ""), ("zoom", 10), ("proxy", ""),
                 ("restore_tabs", True)):
        d.setdefault(k, v)
    return d


def save_data(d):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except Exception:
        pass


def human(n):
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1048576 else f"{n / 1048576:.1f} MB"


# =============================================================== variables
VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def dyn_var(name):
    if name == "$timestamp":
        return str(int(time.time()))
    if name == "$isoTimestamp":
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if name in ("$guid", "$randomUUID"):
        return str(uuid.uuid4())
    if name == "$randomInt":
        return str(random.randint(0, 1000))
    return None


def subst(text, vars_):
    def rep(m):
        n = m.group(1)
        if n in vars_:
            return str(vars_[n])
        d = dyn_var(n) if n.startswith("$") else None
        return d if d is not None else m.group(0)
    return VAR_RE.sub(rep, text or "")


# =============================================================== request building / sending
def build_multipart(parts, boundary):
    out = []
    for k, v, is_file in parts:
        k = k.replace('"', "%22")
        out.append(f"--{boundary}\r\n".encode())
        if is_file:
            fn = os.path.basename(v).replace('"', "%22")
            mt = mimetypes.guess_type(fn)[0] or "application/octet-stream"
            out.append(f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                       f"Content-Type: {mt}\r\n\r\n".encode())
            with open(v, "rb") as f:
                out.append(f.read())
        else:
            out.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            out.append(v.encode("utf-8"))
        out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


def prepare(st, vars_):
    """Turn a saved request state into something sendable (variables resolved)."""
    S = lambda t: subst(t, vars_)
    url = S(st["url"]).strip()
    if not url:
        raise ValueError("Enter a URL first")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "http://" + url
    headers = [(S(k).strip(), S(v)) for on, k, v in st["headers"] if on and k.strip()]
    have = lambda n: n.lower() in {k.lower() for k, _ in headers}
    a = st["auth"]
    if a["type"] == "Bearer" and a["token"] and not have("Authorization"):
        headers.append(("Authorization", "Bearer " + S(a["token"])))
    elif a["type"] == "Basic" and not have("Authorization"):
        tok = base64.b64encode(f'{S(a["user"])}:{S(a["pass"])}'.encode()).decode()
        headers.append(("Authorization", "Basic " + tok))
    elif a["type"] == "API Key" and a["key"]:
        if a["in"] == "Query":
            url += ("&" if "?" in url else "?") + urllib.parse.quote(S(a["key"])) + "=" + urllib.parse.quote(S(a["value"]))
        else:
            headers.append((S(a["key"]), S(a["value"])))
    b = st["body"]
    mode, data, ctype, form, body_text, bfile = b["mode"], None, None, None, "", ""
    if mode == "raw" and b["raw"]:
        body_text = S(b["raw"])
        data = body_text.encode("utf-8")
        ctype = RAW_TYPES.get(b["raw_type"], "text/plain")
    elif mode == "urlenc":
        pairs = [(S(k), S(v)) for on, k, v in b["urlenc"] if on and k]
        if pairs:
            body_text = urllib.parse.urlencode(pairs)
            data = body_text.encode()
            ctype = "application/x-www-form-urlencoded"
    elif mode == "form-data":
        form = []
        for on, k, v in b["form"]:
            if on and k:
                v = S(v)
                if v.startswith("@"):
                    path = os.path.expanduser(v[1:])
                    if not os.path.isfile(path):
                        raise ValueError(f"File not found: {path}")
                    form.append((S(k), path, True))
                else:
                    form.append((S(k), v, False))
        if form:
            boundary = "----Phosphor" + uuid.uuid4().hex
            data = build_multipart(form, boundary)
            ctype = "multipart/form-data; boundary=" + boundary
    elif mode == "binary" and b["file"]:
        bfile = os.path.expanduser(S(b["file"]))
        if not os.path.isfile(bfile):
            raise ValueError(f"File not found: {bfile}")
        with open(bfile, "rb") as f:
            data = f.read()
        ctype = "application/octet-stream"
    if ctype:
        if mode == "form-data":
            headers = [h for h in headers if h[0].lower() != "content-type"]
        if not have("Content-Type") or mode == "form-data":
            headers.append(("Content-Type", ctype))
    gen = [h for h in headers if not (mode == "form-data" and h[0].lower() == "content-type")]
    full = list(headers)
    names = {k.lower() for k, _ in full}
    for k, v in (("User-Agent", APP + "/1.0"), ("Accept", "*/*"), ("Accept-Encoding", "gzip, deflate")):
        if k.lower() not in names:
            full.append((k, v))
    o = st["opts"]
    try:
        timeout = max(1.0, float(o.get("timeout", 30)))
    except (TypeError, ValueError):
        timeout = 30.0
    return {"method": st["method"], "url": urllib.parse.quote(url, safe="%/:?&=#+@,;!$'()*~[]-._"),
            "headers": full, "gen_headers": gen, "data": data, "mode": mode, "form": form,
            "body_text": body_text, "file": bfile, "timeout": timeout,
            "verify": bool(o.get("verify", True)), "follow": bool(o.get("follow", True)), "proxy": "",
            "unresolved": sorted({m.group(1) for m in VAR_RE.finditer(" ".join([url, body_text] + [k + v for k, v in full]))})}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def prettify(text, ctype):
    s = text.lstrip()[:1]
    ct = ctype.lower()
    try:
        if "json" in ct or (s and s in "{["):
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False), "json"
        if "xml" in ct or text.lstrip().startswith("<?xml"):
            x = minidom.parseString(text.encode("utf-8")).toprettyxml(indent="  ")
            return "\n".join(l for l in x.splitlines() if l.strip()), "xml"
    except Exception:
        pass
    return None, "html" if "html" in ct else "text"


def parse_set_cookie(h):
    c = http.cookies.SimpleCookie()
    try:
        c.load(h)
    except Exception:
        return [(h, "", "")]
    out = []
    for name, m in c.items():
        attrs = "; ".join(k if v is True else f"{k}={v}" for k, v in m.items() if v)
        out.append((name, m.value, attrs))
    return out


def execute(p, jar):
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if p.get("proxy"):
        handlers.append(urllib.request.ProxyHandler({"http": p["proxy"], "https": p["proxy"]}))
    if not p["verify"]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    if not p["follow"]:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(p["url"], data=p["data"], method=p["method"])
    for k, v in p["headers"]:
        req.add_header(k, v)
    t0 = time.perf_counter()
    try:
        resp = opener.open(req, timeout=p["timeout"])
    except urllib.error.HTTPError as e:
        resp = e
    ttfb = time.perf_counter() - t0
    chunks, total, trunc = [], 0, False
    try:
        while True:
            b = resp.read(65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
            if total >= MAX_READ:
                trunc = True
                break
    finally:
        resp.close()
    dt = time.perf_counter() - t0
    raw = b"".join(chunks)
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    try:
        if enc in ("gzip", "x-gzip"):
            raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
        elif enc == "deflate":
            try:
                raw = zlib.decompressobj().decompress(raw)
            except zlib.error:
                raw = zlib.decompressobj(-15).decompress(raw)
    except Exception:
        pass
    ctype = resp.headers.get("Content-Type", "") or ""
    charset = "utf-8"
    if "charset=" in ctype.lower():
        charset = ctype.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
    texty = (not ctype) or any(t in ctype.lower() for t in ("text", "json", "xml", "javascript", "html", "form", "yaml", "csv"))
    if not texty and b"\x00" in raw[:2048]:
        text = f"<binary data, {human(len(raw))} - use Save to write it to disk>"
        pretty, kind = None, "text"
    else:
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        pretty, kind = (prettify(text, ctype) if len(text) < MAX_PRETTY else (None, "text"))
    status = getattr(resp, "status", None) or resp.code
    return {"status": status, "reason": getattr(resp, "reason", "") or "", "headers": list(resp.headers.items()),
            "cookies": resp.headers.get_all("Set-Cookie") or [], "raw": raw, "text": text, "pretty": pretty,
            "kind": kind, "size": len(raw), "time": dt, "ttfb": ttfb, "ctype": ctype, "truncated": trunc,
            "url": resp.geturl()}


# =============================================================== tests (tiny assertion language)
_MISSING = object()
OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}
LINE_RE = re.compile(r"^(?P<lhs>.+?)\s+(?P<op>==|!=|<=|>=|<|>|not contains|contains|exists)\s*(?P<rhs>.*)$")
SET_RE = re.compile(r"^set\s+(?P<var>[\w.\-$]+)\s*=\s*(?P<expr>.+)$", re.I)


def _tostr(v):
    if v is None:
        return "null"
    if v is _MISSING:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def json_get(obj, path):
    for m in re.finditer(r"\[(\d+)\]|([^.\[\]]+)", path):
        try:
            if m.group(1) is not None:
                obj = obj[int(m.group(1))]
            else:
                k = m.group(2)
                obj = obj[int(k)] if isinstance(obj, list) and k.isdigit() else obj[k]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return obj


def resolve(expr, res, cache):
    e = expr.strip()
    low = e.lower()
    if low == "status":
        return res["status"]
    if low == "time":
        return round(res["time"] * 1000)
    if low == "size":
        return res["size"]
    if low == "body":
        return res["text"]
    if low.startswith("header "):
        name = e[7:].strip().lower()
        for k, v in res["headers"]:
            if k.lower() == name:
                return v
        return _MISSING
    if low == "json" or low.startswith("json.") or low.startswith("json["):
        if "j" not in cache:
            try:
                cache["j"] = json.loads(res["text"])
            except Exception:
                cache["j"] = _MISSING
        if cache["j"] is _MISSING:
            return _MISSING
        return json_get(cache["j"], e[4:].lstrip("."))
    raise ValueError("unknown expression")


def run_tests(script, res, set_var):
    out, cache = [], {}
    for line in (script or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            m = SET_RE.match(line)
            if m:
                v = resolve(m["expr"], res, cache)
                if v is _MISSING:
                    out.append((False, f"{line}   (value not found)"))
                else:
                    set_var(m["var"], _tostr(v))
                    out.append((True, f"{m['var']} = {_tostr(v)[:80]}"))
                continue
            m = LINE_RE.match(line)
            if not m:
                out.append((False, f"{line}   (can't parse)"))
                continue
            lhs, op, rhs = m["lhs"], m["op"], m["rhs"].strip()
            if len(rhs) >= 2 and rhs[0] == rhs[-1] and rhs[0] in "\"'":
                rhs = rhs[1:-1]
            v = resolve(lhs, res, cache)
            got = ""
            if op == "exists":
                ok = v is not _MISSING
            elif v is _MISSING:
                ok = op in ("!=", "not contains")
            else:
                a = _tostr(v)
                got = a[:60]
                if op == "contains":
                    ok = rhs in a
                elif op == "not contains":
                    ok = rhs not in a
                else:
                    x, y = _num(a), _num(rhs)
                    ok = OPS[op](x, y) if x is not None and y is not None else OPS[op](a, rhs)
            out.append((ok, line if ok else f"{line}   (got: {got or 'missing'})"))
        except ValueError as e:
            out.append((False, f"{line}   ({e})"))
    return out


# =============================================================== import / export / code generation
def parse_curl(cmd):
    cmd = re.sub(r"\\\s*\n", " ", cmd.strip())
    try:
        toks = shlex.split(cmd)
    except ValueError as e:
        raise ValueError(f"Bad curl command: {e}")
    if not toks or toks[0] != "curl":
        raise ValueError("Not a curl command")
    st = new_state()
    method = url = user = None
    headers, data, form = [], [], []
    skip_arg = {"-o", "--output", "-m", "--max-time", "--connect-timeout", "-x", "--proxy", "-w", "-T",
                "--cert", "--key", "--cacert", "-c", "-K", "--retry", "-e", "--referer", "-E"}
    i = 1
    try:
        while i < len(toks):
            t = toks[i]
            if t in ("-X", "--request"):
                i += 1
                method = toks[i].upper()
            elif t in ("-H", "--header"):
                i += 1
                k, _, v = toks[i].partition(":")
                headers.append([True, k.strip(), v.strip()])
            elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
                i += 1
                data.append(toks[i])
            elif t in ("-F", "--form"):
                i += 1
                form.append(toks[i])
            elif t in ("-u", "--user"):
                i += 1
                user = toks[i]
            elif t in ("-A", "--user-agent"):
                i += 1
                headers.append([True, "User-Agent", toks[i]])
            elif t in ("-b", "--cookie"):
                i += 1
                headers.append([True, "Cookie", toks[i]])
            elif t == "--url":
                i += 1
                url = toks[i]
            elif t in ("-I", "--head"):
                method = "HEAD"
            elif t in ("-k", "--insecure"):
                st["opts"]["verify"] = False
            elif t in skip_arg:
                i += 1
            elif t.startswith("-"):
                pass
            elif url is None:
                url = t
            i += 1
    except IndexError:
        raise ValueError("Incomplete curl command")
    if not url:
        raise ValueError("No URL found in curl command")
    ct = next((h[2].lower() for h in headers if h[1].lower() == "content-type"), "")
    st["url"], st["headers"] = url, headers
    if form:
        rows = []
        for f in form:
            k, _, v = f.partition("=")
            rows.append([True, k, v.split(";")[0] if v.startswith("@") else v])
        st["body"].update(mode="form-data", form=rows)
        st["headers"] = [h for h in headers if h[1].lower() != "content-type"]
    elif data:
        body = "&".join(data)
        if "x-www-form-urlencoded" in ct or (not ct and "=" in body and not body.lstrip().startswith(("{", "[", "<"))):
            st["body"].update(mode="urlenc", urlenc=[[True, k, v] for k, v in urllib.parse.parse_qsl(body, keep_blank_values=True)])
            st["headers"] = [h for h in headers if h[1].lower() != "content-type"]
        else:
            rt = "JSON" if (body.lstrip()[:1] in ("{", "[") or "json" in ct) else "XML" if "xml" in ct else "HTML" if "html" in ct else "Text"
            st["body"].update(mode="raw", raw=body, raw_type=rt)
    st["method"] = method or ("POST" if (data or form) else "GET")
    if user:
        u, _, p = user.partition(":")
        st["auth"].update(type="Basic", user=u, **{"pass": p})
    return st


def pm_to_state(it):
    r = it.get("request") or {}
    if isinstance(r, str):
        r = {"method": "GET", "url": r}
    st = new_state()
    st["name"] = it.get("name", "")
    st["method"] = (r.get("method") or "GET").upper()
    u = r.get("url", "")
    st["url"] = u if isinstance(u, str) else (u.get("raw") or "")
    st["headers"] = [[not h.get("disabled", False), h.get("key", ""), str(h.get("value", ""))] for h in (r.get("header") or [])]
    b = r.get("body") or {}
    mode = b.get("mode")
    if mode == "raw":
        lang = ((b.get("options") or {}).get("raw") or {}).get("language", "text")
        st["body"].update(mode="raw", raw=b.get("raw", ""), raw_type={"json": "JSON", "xml": "XML", "html": "HTML", "javascript": "JavaScript"}.get(lang, "Text"))
    elif mode == "urlencoded":
        st["body"].update(mode="urlenc", urlenc=[[not p.get("disabled", False), p.get("key", ""), str(p.get("value", ""))] for p in b.get("urlencoded", [])])
    elif mode == "formdata":
        rows = []
        for p in b.get("formdata", []):
            v = ("@" + str(p.get("src") or "")) if p.get("type") == "file" else str(p.get("value", ""))
            rows.append([not p.get("disabled", False), p.get("key", ""), v])
        st["body"].update(mode="form-data", form=rows)
    a = r.get("auth")
    if a and a.get("type"):
        t = a["type"]
        raw = a.get(t)
        d = raw if isinstance(raw, dict) else {x.get("key"): x.get("value", "") for x in (raw or [])}
        if t == "bearer":
            st["auth"].update(type="Bearer", token=d.get("token", ""))
        elif t == "basic":
            st["auth"].update(type="Basic", user=d.get("username", ""), **{"pass": d.get("password", "")})
        elif t == "apikey":
            st["auth"].update(type="API Key", key=d.get("key", ""), value=d.get("value", ""), **{"in": "Query" if d.get("in") == "query" else "Header"})
    st["tests"] = it.get("_mp_tests", "")
    return st


def pm_import(obj):
    def conv(items):
        out = []
        for it in items:
            if "item" in it:
                out.append({"id": uid(), "type": "folder", "name": it.get("name", "Folder"), "children": conv(it["item"])})
            else:
                out.append({"id": uid(), "type": "request", "name": it.get("name", "Request"), "req": pm_to_state(it)})
        return out
    return {"id": uid(), "type": "folder", "name": obj.get("info", {}).get("name", "Imported"), "children": conv(obj.get("item", []))}


def state_to_pm(name, st):
    st = norm_state(st)
    b = st["body"]
    body = None
    if b["mode"] == "raw":
        body = {"mode": "raw", "raw": b["raw"], "options": {"raw": {"language": b["raw_type"].lower()}}}
    elif b["mode"] == "urlenc":
        body = {"mode": "urlencoded", "urlencoded": [{"key": k, "value": v, **({} if on else {"disabled": True})} for on, k, v in b["urlenc"]]}
    elif b["mode"] == "form-data":
        fd = []
        for on, k, v in b["form"]:
            e = {"key": k, "type": "file", "src": v[1:]} if v.startswith("@") else {"key": k, "value": v, "type": "text"}
            if not on:
                e["disabled"] = True
            fd.append(e)
        body = {"mode": "formdata", "formdata": fd}
    req = {"method": st["method"], "url": {"raw": st["url"]},
           "header": [{"key": k, "value": v, **({} if on else {"disabled": True})} for on, k, v in st["headers"]]}
    if body:
        req["body"] = body
    a = st["auth"]
    if a["type"] == "Bearer":
        req["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": a["token"], "type": "string"}]}
    elif a["type"] == "Basic":
        req["auth"] = {"type": "basic", "basic": [{"key": "username", "value": a["user"]}, {"key": "password", "value": a["pass"]}]}
    elif a["type"] == "API Key":
        req["auth"] = {"type": "apikey", "apikey": [{"key": "key", "value": a["key"]}, {"key": "value", "value": a["value"]},
                                                   {"key": "in", "value": "query" if a["in"] == "Query" else "header"}]}
    item = {"name": name, "request": req}
    if st["tests"].strip():
        item["_mp_tests"] = st["tests"]
    return item


def pm_export(node):
    def conv(n):
        if n["type"] == "folder":
            return {"name": n["name"], "item": [conv(c) for c in n["children"]]}
        return state_to_pm(n["name"], n["req"])
    root = conv(node) if node["type"] == "folder" else {"name": node["name"], "item": [conv(node)]}
    return {"info": {"name": root["name"], "_postman_id": str(uuid.uuid4()),
                     "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": root["item"]}


def gen_code(kind, p):
    hs, m = p["gen_headers"], p["method"]
    if kind == "cURL":
        L = ["curl" + (" -I" if m == "HEAD" else "" if m == "GET" else f" -X {m}") + " " + shlex.quote(p["url"])]
        L += ["-H " + shlex.quote(f"{k}: {v}") for k, v in hs]
        if p["mode"] == "form-data":
            L += ["-F " + shlex.quote(f"{k}=@{v}" if f else f"{k}={v}") for k, v, f in p["form"]]
        elif p["mode"] == "binary":
            L.append("--data-binary " + shlex.quote("@" + p["file"]))
        elif p["data"]:
            L.append("--data-raw " + shlex.quote(p["body_text"]))
        if not p["verify"]:
            L.append("-k")
        if p["follow"]:
            L.append("-L")
        return " \\\n  ".join(L)
    if kind == "Python (requests)":
        L = ["import requests", "", f"url = {p['url']!r}"]
        args = [f'"{m}"', "url"]
        if hs:
            L.append("headers = " + json.dumps(dict(hs), indent=4, ensure_ascii=False))
            args.append("headers=headers")
        if p["mode"] == "form-data":
            fl = [f'    ({k!r}, (os.path.basename({v!r}), open({v!r}, "rb")))' if f else f"    ({k!r}, (None, {v!r}))" for k, v, f in p["form"]]
            L[0] = "import os\nimport requests"
            L.append("files = [\n" + ",\n".join(fl) + "\n]")
            args.append("files=files")
        elif p["mode"] == "binary":
            L.append(f'with open({p["file"]!r}, "rb") as f:\n    payload = f.read()')
            args.append("data=payload")
        elif p["data"]:
            L.append(f"payload = {p['body_text']!r}")
            args.append("data=payload.encode('utf-8')")
        if not p["verify"]:
            args.append("verify=False")
        if not p["follow"]:
            args.append("allow_redirects=False")
        L += ["", "response = requests.request(" + ", ".join(args) + ")", "print(response.status_code)", "print(response.text)"]
        return "\n".join(L)
    L = []
    opts = [f'  method: "{m}"']
    if hs:
        opts.append("  headers: " + json.dumps(dict(hs), indent=4, ensure_ascii=False).replace("\n", "\n  "))
    if p["mode"] == "form-data":
        L.append("const form = new FormData();")
        for k, v, f in p["form"]:
            L.append(f"form.append({json.dumps(k)}, fileInput.files[0]); // file: {v}" if f else f"form.append({json.dumps(k)}, {json.dumps(v)});")
        opts.append("  body: form")
    elif p["data"] and p["mode"] != "binary":
        opts.append("  body: " + json.dumps(p["body_text"], ensure_ascii=False))
    if not p["follow"]:
        opts.append('  redirect: "manual"')
    L += [f"const response = await fetch({json.dumps(p['url'])}, {{", ",\n".join(opts), "});",
          "console.log(response.status);", "console.log(await response.text());"]
    return "\n".join(L)


# =============================================================== widgets
def autosb(sb):
    """yscrollcommand/xscrollcommand that hides the (grid-managed) scrollbar when everything fits."""
    def f(lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            sb.grid_remove()
        else:
            sb.grid()
        sb.set(lo, hi)
    return f


class Tip:
    """Small hover tooltip."""

    def __init__(self, widget, text, app):
        self.w, self.text, self.app, self.tw, self.job = widget, text, app, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _enter(self, _e=None):
        self._hide()
        self.job = self.w.after(450, self._show)

    def _show(self):
        self.job = None
        try:
            P = self.app.pal
            self.tw = tw = tk.Toplevel(self.w)
            tw.wm_overrideredirect(True)
            tw.configure(bg=P["border"])
            tk.Label(tw, text=self.text() if callable(self.text) else self.text, bg=P["panel"], fg=P["fg"], padx=9, pady=4, font=self.app.f_small).pack(padx=1, pady=1)
            tw.wm_geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 6}")
        except tk.TclError:
            self.tw = None

    def _hide(self, _e=None):
        try:
            if self.job:
                self.w.after_cancel(self.job)
            if self.tw:
                self.tw.destroy()
        except tk.TclError:
            pass
        self.job, self.tw = None, None


class SegTabs(ttk.Frame):
    """Flat tab strip with a glowing underline (replaces ttk.Notebook)."""

    def __init__(self, master):
        super().__init__(master)
        self.hdr = ttk.Frame(self)
        self.hdr.grid(row=0, column=0, sticky="ew")
        ttk.Frame(self, style="Rule.TFrame", height=1).grid(row=1, column=0, sticky="ew")
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self.items, self.cur = [], None

    def add(self, frame, text=""):
        cell = ttk.Frame(self.hdr)
        cell.pack(side="left")
        lab = ttk.Label(cell, text=text, style="Seg.TLabel", cursor="hand2")
        lab.pack()
        line = ttk.Frame(cell, style="SegLine.TFrame", height=2)
        line.pack(fill="x")
        it = [frame, lab, line, text]
        self.items.append(it)
        lab.bind("<Button-1>", lambda e, f=frame: self.select(f))
        lab.bind("<Enter>", lambda e, f=frame: self.cur is not f and lab.configure(style="SegHover.TLabel"))
        lab.bind("<Leave>", lambda e, f=frame: self.cur is not f and lab.configure(style="Seg.TLabel"))
        frame.grid(row=2, column=0, sticky="nsew")
        if self.cur is None:
            self.select(frame)
        else:
            frame.grid_remove()

    def _find(self, key):
        if isinstance(key, int):
            return self.items[key]
        for it in self.items:
            if it[0] is key:
                return it
        raise KeyError(key)

    def tab(self, key, text=None):
        it = self._find(key)
        if text is not None and text != it[3]:
            it[3] = text
            it[1].configure(text=text)

    def select(self, key):
        it = self._find(key)
        for o in self.items:
            on = o is it
            o[1].configure(style="SegSel.TLabel" if on else "Seg.TLabel")
            o[2].configure(style="SegLineOn.TFrame" if on else "SegLine.TFrame")
            if on:
                o[0].grid()
            else:
                o[0].grid_remove()
        self.cur = it[0]


class DocTabs(ttk.Frame):
    """Request tabs: coloured method badge, title, close button, + button."""

    def __init__(self, master, app, on_new, on_close, on_menu):
        super().__init__(master)
        self.app, self.on_close, self.on_menu = app, on_close, on_menu
        self.bar = ttk.Frame(self, style="Bar.TFrame")
        self.bar.grid(row=0, column=0, sticky="ew")
        self.strip = ttk.Frame(self.bar, style="Bar.TFrame")
        self.strip.pack(side="left", fill="y")
        plus = ttk.Button(self.bar, text="＋", width=3, style="GhostBar.TButton", command=on_new)
        plus.pack(side="left", padx=6, pady=3)
        Tip(plus, "New tab   Ctrl+T", app)
        ttk.Frame(self, style="Rule.TFrame", height=1).grid(row=1, column=0, sticky="ew")
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self.items, self.cur = [], None
        self.bar.bind("<Configure>", lambda e: self._titles())

    def _find(self, pane):
        for it in self.items:
            if it["pane"] is pane:
                return it
        raise KeyError(pane)

    def _style(self, it, on):
        it["cell"].configure(style="TabSel.TFrame" if on else "Tab.TFrame")
        it["row"].configure(style="TabSel.TFrame" if on else "Tab.TFrame")
        it["line"].configure(style="SegLineOn.TFrame" if on else "Tab.TFrame")
        mth = it["method"]
        if mth in SHORT:
            it["m"].configure(style=f"{mth}.DocSel.TLabel" if on else f"{mth}.Doc.TLabel")
        else:
            it["m"].configure(style="DocSel.TLabel" if on else "Doc.TLabel")
        it["t"].configure(style="DocSel.TLabel" if on else "Doc.TLabel")
        it["x"].configure(style="CloseSel.TLabel" if on else "Close.TLabel")

    def add(self, pane, method="GET", title=""):
        cell = ttk.Frame(self.strip, style="Tab.TFrame")
        cell.pack(side="left", fill="y")
        ttk.Frame(cell, style="Rule.TFrame", width=1).pack(side="right", fill="y")
        line = ttk.Frame(cell, style="Tab.TFrame", height=2)
        line.pack(side="top", fill="x")
        row = ttk.Frame(cell, style="Tab.TFrame")
        row.pack(side="top", fill="x")
        m = ttk.Label(row, text=SHORT.get(method, method), style="Doc.TLabel")
        m.pack(side="left", padx=(12, 6), pady=7)
        t = ttk.Label(row, style="Doc.TLabel")
        t.pack(side="left")
        x = ttk.Label(row, text="×", style="Close.TLabel", cursor="hand2")
        x.pack(side="left", padx=(10, 8))
        it = dict(pane=pane, cell=cell, line=line, row=row, m=m, t=t, x=x, method=method, title=title)
        self.items.append(it)
        Tip(t, lambda it=it: f"{it['method']}   {it['title'] or 'New Request'}", self.app)
        for w in (cell, row, m, t):
            w.bind("<Button-1>", lambda e, p=pane: self.select(p))
            w.bind("<Button-2>", lambda e, p=pane: self.on_close(p))
            w.bind("<Button-3>", lambda e, p=pane: self.on_menu(p, e))
        x.bind("<Button-1>", lambda e, p=pane: self.on_close(p))
        x.bind("<Enter>", lambda e, i=it: i["x"].configure(style="CloseHotSel.TLabel" if self.cur is i["pane"] else "CloseHot.TLabel"))
        x.bind("<Leave>", lambda e, i=it: i["x"].configure(style="CloseSel.TLabel" if self.cur is i["pane"] else "Close.TLabel"))
        pane.grid(row=2, column=0, sticky="nsew")
        self._style(it, False)
        if self.cur is None:
            self.select(pane)
        else:
            pane.grid_remove()
        self._titles()

    def select(self, pane):
        it = self._find(pane)
        for o in self.items:
            on = o is it
            self._style(o, on)
            if on:
                o["pane"].grid()
            else:
                o["pane"].grid_remove()
        self.cur = pane

    def forget(self, pane):
        it = self._find(pane)
        i = self.items.index(it)
        self.items.remove(it)
        it["cell"].destroy()
        pane.grid_remove()
        if self.cur is pane:
            self.cur = None
            if self.items:
                self.select(self.items[min(i, len(self.items) - 1)]["pane"])
        self._titles()

    def tab(self, pane, method=None, title=None):
        try:
            it = self._find(pane)
        except KeyError:
            return
        if method is not None:
            it["method"] = method
            it["m"].configure(text=SHORT.get(method, method))
            self._style(it, self.cur is pane)
        if title is not None:
            it["title"] = title
        self._titles()

    def _titles(self):
        n = len(self.items)
        if not n:
            return
        W = self.bar.winfo_width() - 70
        per = 9999 if W <= 0 else W // n
        dot = per < 118            # crowded: shrink the method badge to a coloured dot
        chars = 24 if W <= 0 else max(3, min(24, (per - (62 if dot else 100)) // 7))
        for it in self.items:
            t = it["title"] or "New Request"
            it["t"].configure(text=t if len(t) <= chars else t[:chars - 1] + "…")
            it["m"].configure(text="●" if dot else SHORT.get(it["method"], it["method"]))

    def panes(self):
        return [i["pane"] for i in self.items]

    def tabs(self):
        return [str(i["pane"]) for i in self.items]

    def current(self):
        return self.cur

    def current_index(self):
        for i, it in enumerate(self.items):
            if it["pane"] is self.cur:
                return i
        return 0

    def select_index(self, i):
        if self.items:
            self.select(self.items[max(0, min(i, len(self.items) - 1))]["pane"])


class KVTable(ttk.Frame):
    """Editable key/value table with enable checkboxes (rows kept in a python list)."""

    def __init__(self, master, on_change=None, file_btn=False, height=4):
        super().__init__(master)
        self.on_change = on_change
        self.rows = []
        self.tv = ttk.Treeview(self, columns=("on", "key", "value"), show="headings", selectmode="browse", height=height)
        for c, t, w, s in (("on", "", 34, False), ("key", "Key", 200, True), ("value", "Value", 320, True)):
            self.tv.heading(c, text=t, anchor="w")
            self.tv.column(c, width=w, minwidth=30, stretch=s, anchor="center" if c == "on" else "w")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=autosb(ys))
        self.tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(bar, text="+ Add", style="Tool.TButton", command=self.add).pack(side="left")
        ttk.Button(bar, text="Delete", style="Tool.TButton", command=self.delete).pack(side="left", padx=4)
        if file_btn:
            ttk.Button(bar, text="Attach file…", style="Tool.TButton", command=self.attach).pack(side="left")
        self.tv.bind("<Button-1>", self._click)
        self.tv.bind("<Double-1>", self._dbl)
        self.tv.bind("<Delete>", lambda e: self.delete())
        self.set([])

    def _changed(self):
        if self.on_change:
            self.on_change()

    def _ensure_blank(self):
        if not self.rows or self.rows[-1][1] or self.rows[-1][2]:
            self.rows.append([True, "", ""])

    def refresh(self, select=None):
        try:
            self.tv.tag_configure("off", foreground=self.winfo_toplevel().pal["muted"])
        except Exception:
            pass
        self.tv.delete(*self.tv.get_children())
        for i, (on, k, v) in enumerate(self.rows):
            self.tv.insert("", "end", iid=str(i), values=(("☑" if on else "☐") if (k or v) else "", k, v), tags=() if on else ("off",))
        if select is not None and 0 <= select < len(self.rows):
            self.tv.selection_set(str(select))

    def get(self):
        return [[bool(r[0]), r[1], r[2]] for r in self.rows if r[1] or r[2]]

    def set(self, rows):
        self.rows = [[bool(a), str(b), str(c)] for a, b, c in rows]
        self._ensure_blank()
        self.refresh()

    def _click(self, e):
        if self.tv.identify_region(e.x, e.y) == "cell" and self.tv.identify_column(e.x) == "#1":
            iid = self.tv.identify_row(e.y)
            if iid:
                i = int(iid)
                self.rows[i][0] = not self.rows[i][0]
                self.refresh(i)
                self._changed()
                return "break"

    def _dbl(self, e):
        if self.tv.identify_region(e.x, e.y) != "cell":
            return
        col, iid = self.tv.identify_column(e.x), self.tv.identify_row(e.y)
        if col == "#1":
            return
        if not iid:
            self.add()
        else:
            self._edit(int(iid), col)

    def add(self):
        self._ensure_blank()
        i = len(self.rows) - 1
        self.refresh(i)
        self.after(20, lambda: self._edit(i, "#2"))

    def delete(self):
        s = self.tv.selection()
        if not s:
            return
        i = int(s[0])
        if i < len(self.rows):
            del self.rows[i]
        self._ensure_blank()
        self.refresh()
        self._changed()

    def attach(self):
        p = filedialog.askopenfilename(parent=self.winfo_toplevel())
        if not p:
            return
        s = self.tv.selection()
        i = int(s[0]) if s else len(self.rows) - 1
        r = self.rows[i]
        r[2] = "@" + p
        if not r[1]:
            r[1] = "file"
        self._ensure_blank()
        self.refresh(i)
        self._changed()

    def _edit(self, i, col):
        self.tv.see(str(i))
        self.update_idletasks()
        bb = self.tv.bbox(str(i), col)
        if not bb or i >= len(self.rows):
            return
        idx = 1 if col == "#2" else 2
        e = ttk.Entry(self.tv)
        e.place(x=bb[0], y=bb[1], width=bb[2], height=bb[3])
        e.insert(0, self.rows[i][idx])
        e.focus_set()
        e.select_range(0, "end")
        done = [False]

        def finish(commit, nxt=False):
            if done[0]:
                return
            done[0] = True
            val = e.get()
            e.destroy()
            if commit and i < len(self.rows) and self.rows[i][idx] != val:
                self.rows[i][idx] = val
                self._ensure_blank()
                self.refresh(i)
                self._changed()
            if nxt and idx == 1:
                self.after(10, lambda: self._edit(i, "#3"))

        e.bind("<Return>", lambda ev: finish(True))
        e.bind("<Escape>", lambda ev: finish(False))
        e.bind("<Tab>", lambda ev: (finish(True, True), "break")[1])
        e.bind("<FocusOut>", lambda ev: finish(True))


# =============================================================== request tab
class RequestPane(ttk.Frame):
    def __init__(self, master, app, state=None):
        super().__init__(master)
        self.app = app
        self.ref, self.name = None, ""
        self.busy, self.token, self._guard = False, None, False
        self.resp_text, self.resp_pretty, self.resp_kind, self.last_raw = "", None, "text", b""
        self._build()
        self.set_state(norm_state(state))

    # ---------- layout
    def _build(self):
        app = self.app
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(12, 4))
        self.method = ttk.Combobox(top, values=METHODS, width=8, state="readonly", style="GET.Method.TCombobox")
        self.method.pack(side="left", ipady=3)
        app.reg_combo(self.method)
        self.method.bind("<<ComboboxSelected>>", self._method_changed)
        self.url_var = tk.StringVar()
        self.url = ttk.Entry(top, textvariable=self.url_var, font=app.f_mono)
        self.url.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        self.url.bind("<Return>", lambda e: self.send())
        self.url.bind("<<Paste>>", lambda e: self.after(30, self._check_curl))
        self.url_var.trace_add("write", self._url_changed)
        self.send_btn = ttk.Button(top, text="Send  ▸", style="Accent.TButton", width=9, command=self.send)
        self.send_btn.pack(side="left")
        ttk.Button(top, text="Save", command=lambda: app.save_pane(self)).pack(side="left", padx=(8, 0))
        code_btn = ttk.Button(top, text="Code", command=self.show_code)
        code_btn.pack(side="left", padx=(8, 0))
        Tip(code_btn, "Generate code: cURL, Python, JavaScript", app)
        self.hint = ttk.Label(self, style="Hint.TLabel")
        self._hint_on = False

        self._sash_set = False
        self.pw = ttk.PanedWindow(self, orient="vertical")
        self.pw.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.pw.bind("<Map>", lambda e: self.after(40, self._init_sash))
        up = ttk.Frame(self.pw)
        self.pw.add(up, weight=2)
        self.rn = SegTabs(up)
        self.rn.pack(fill="both", expand=True)
        pad = (2, 10, 2, 2)

        # Params
        self.f_params = ttk.Frame(self.rn, padding=pad)
        self.rn.add(self.f_params, text="Params")
        self.params = KVTable(self.f_params, on_change=self._params_changed)
        self.params.pack(fill="both", expand=True)

        # Auth
        self.f_auth = ttk.Frame(self.rn, padding=(2, 14, 2, 2))
        self.rn.add(self.f_auth, text="Auth")
        self.auth_type = ttk.Combobox(self.f_auth, values=["None", "Bearer", "Basic", "API Key"], state="readonly", width=16)
        app.reg_combo(self.auth_type)
        self.a_vars = {k: tk.StringVar() for k in ("token", "user", "pass", "key", "value")}
        self.a_in = ttk.Combobox(self.f_auth, values=["Header", "Query"], state="readonly", width=16)
        app.reg_combo(self.a_in)
        mk = lambda k, show="": ttk.Entry(self.f_auth, textvariable=self.a_vars[k], width=56, show=show)
        rows = [("Type", self.auth_type, None), ("Token", mk("token"), {"Bearer"}), ("Username", mk("user"), {"Basic"}),
                ("Password", mk("pass", "•"), {"Basic"}), ("Key", mk("key"), {"API Key"}),
                ("Value", mk("value"), {"API Key"}), ("Add to", self.a_in, {"API Key"})]
        self._arows = []
        for i, (lab, w, types) in enumerate(rows):
            l = ttk.Label(self.f_auth, text=lab, style="Muted.TLabel")
            l.grid(row=i, column=0, sticky="w", pady=6, padx=(0, 16))
            w.grid(row=i, column=1, sticky="w")
            self._arows.append((l, w, types))
        ttk.Label(self.f_auth, text="Supports {{variables}}. The Authorization header is added for you when you send.",
                  style="Hint.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.auth_type.bind("<<ComboboxSelected>>", lambda e: self._auth_layout())

        # Headers
        self.f_hdr = ttk.Frame(self.rn, padding=pad)
        self.rn.add(self.f_hdr, text="Headers")
        self.hdrs = KVTable(self.f_hdr, on_change=self._refresh_counts)
        self.hdrs.pack(fill="both", expand=True)
        ttk.Label(self.f_hdr, text="Added automatically: User-Agent, Accept, Accept-Encoding, and Content-Type (from Body).",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        # Body
        self.f_body = ttk.Frame(self.rn, padding=pad)
        self.rn.add(self.f_body, text="Body")
        bm = ttk.Frame(self.f_body)
        bm.pack(fill="x", pady=(0, 8))
        self.body_mode = tk.StringVar(value="none")
        for v, t in (("none", "none"), ("raw", "raw"), ("form-data", "form-data"), ("urlenc", "x-www-form-urlencoded"), ("binary", "binary")):
            ttk.Radiobutton(bm, text=t, value=v, variable=self.body_mode, command=self._body_layout).pack(side="left", padx=(0, 16))
        self.bstack = ttk.Frame(self.f_body)
        self.bstack.pack(fill="both", expand=True)
        self.bstack.rowconfigure(0, weight=1)
        self.bstack.columnconfigure(0, weight=1)
        self.bf = {}
        f = ttk.Frame(self.bstack)
        ttk.Label(f, text="This request has no body.", style="Muted.TLabel").pack(anchor="w", pady=10)
        self.bf["none"] = f
        f = ttk.Frame(self.bstack)
        rb = ttk.Frame(f)
        rb.pack(fill="x", pady=(0, 6))
        self.raw_type = ttk.Combobox(rb, values=list(RAW_TYPES), state="readonly", width=12)
        self.raw_type.pack(side="left")
        app.reg_combo(self.raw_type)
        ttk.Button(rb, text="Beautify", style="Tool.TButton", command=self.beautify).pack(side="left", padx=8)
        box, self.raw = app.text_box(f, height=6)
        box.pack(fill="both", expand=True)
        self.raw.bind("<KeyRelease>", lambda e: self._hl_vars())
        self.bf["raw"] = f
        f = ttk.Frame(self.bstack)
        self.form = KVTable(f, file_btn=True, height=4)
        self.form.pack(fill="both", expand=True)
        ttk.Label(f, text="Tip: a value starting with @ is sent as a file (e.g. @/home/me/pic.png).", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        self.bf["form-data"] = f
        f = ttk.Frame(self.bstack)
        self.urlenc = KVTable(f, height=4)
        self.urlenc.pack(fill="both", expand=True)
        self.bf["urlenc"] = f
        f = ttk.Frame(self.bstack)
        self.file_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.file_var).pack(side="left", fill="x", expand=True, pady=10, ipady=3)
        ttk.Button(f, text="Browse…", command=lambda: self.file_var.set(filedialog.askopenfilename(parent=self) or self.file_var.get())).pack(side="left", padx=8)
        self.bf["binary"] = f
        for w in self.bf.values():
            w.grid(row=0, column=0, sticky="nsew")

        # Tests
        self.f_tests = ttk.Frame(self.rn, padding=pad)
        self.rn.add(self.f_tests, text="Tests")
        ttk.Label(self.f_tests, justify="left", wraplength=800, style="Hint.TLabel",
                  text="One rule per line:   status == 200   ·   time < 800   ·   header Content-Type contains json   ·   "
                       "json.data.id exists   ·   json.items[0].name == \"x\"   ·   body contains ok   ·   "
                       "set authToken = json.token   (saves the value into the active environment)").pack(anchor="w", pady=(0, 6))
        box, self.tests = app.text_box(self.f_tests, height=4)
        box.pack(fill="both", expand=True)
        self.tests.bind("<KeyRelease>", lambda e: self._refresh_counts())

        # Settings
        self.f_set = ttk.Frame(self.rn, padding=(2, 14, 2, 2))
        self.rn.add(self.f_set, text="Settings")
        self.verify, self.follow = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Checkbutton(self.f_set, text="Verify SSL certificate", variable=self.verify).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Checkbutton(self.f_set, text="Follow redirects", variable=self.follow).grid(row=1, column=0, sticky="w", pady=5)
        tf = ttk.Frame(self.f_set)
        tf.grid(row=2, column=0, sticky="w", pady=5)
        ttk.Label(tf, text="Timeout (seconds)", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        self.timeout = ttk.Spinbox(tf, from_=1, to=600, width=6)
        self.timeout.pack(side="left")

        # ----- response
        dn = ttk.Frame(self.pw)
        self.pw.add(dn, weight=3)
        sb = ttk.Frame(dn)
        sb.pack(fill="x", pady=(10, 6))
        ttk.Label(sb, text="Response", style="Bold.TLabel").pack(side="left")
        self.lb_size = ttk.Label(sb, style="Muted.TLabel")
        self.lb_size.pack(side="right", padx=(14, 0))
        self.lb_time = ttk.Label(sb, style="Muted.TLabel")
        self.lb_time.pack(side="right", padx=(14, 0))
        self.lb_status = ttk.Label(sb, style="PillOff.TLabel")
        self.lb_status.pack(side="right")
        self.sn = SegTabs(dn)
        self.sn.pack(fill="both", expand=True)
        bf = ttk.Frame(self.sn, padding=(0, 8, 0, 0))
        self.sn.add(bf, text="Body")
        tb = ttk.Frame(bf)
        tb.pack(fill="x", pady=(0, 6))
        self.view = tk.StringVar(value="Pretty")
        for t in ("Pretty", "Raw"):
            ttk.Radiobutton(tb, text=t, value=t, variable=self.view, command=self._render_body).pack(side="left", padx=(0, 10))
        self.wrap = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="Wrap", variable=self.wrap, command=lambda: self.rbody.configure(wrap="word" if self.wrap.get() else "none")).pack(side="left", padx=(4, 0))
        self.find_var = tk.StringVar()
        fe = ttk.Entry(tb, textvariable=self.find_var, width=18)
        fe.pack(side="left", padx=(16, 4), ipady=2)
        fe.bind("<Return>", lambda e: self.find())
        ttk.Button(tb, text="Find", style="Tool.TButton", command=self.find).pack(side="left")
        self.find_lb = ttk.Label(tb, style="Hint.TLabel")
        self.find_lb.pack(side="left", padx=8)
        ttk.Button(tb, text="Save…", style="Tool.TButton", command=self.save_resp).pack(side="right")
        ttk.Button(tb, text="Copy", style="Tool.TButton", command=self.copy_resp).pack(side="right", padx=6)
        box, self.rbody = app.text_box(bf, readonly=True, height=8)
        box.pack(fill="both", expand=True)
        hf = ttk.Frame(self.sn, padding=(0, 8, 0, 0))
        self.sn.add(hf, text="Headers")
        box, self.rhead = app.text_box(hf, readonly=True, height=6)
        box.pack(fill="both", expand=True)
        cf = ttk.Frame(self.sn, padding=(0, 8, 0, 0))
        self.sn.add(cf, text="Cookies")
        self.ctv = ttk.Treeview(cf, columns=("name", "value", "attrs"), show="headings", height=5)
        for c, t, w in (("name", "Name", 160), ("value", "Value", 260), ("attrs", "Attributes", 360)):
            self.ctv.heading(c, text=t, anchor="w")
            self.ctv.column(c, width=w, anchor="w")
        self.ctv.pack(fill="both", expand=True)
        tf2 = ttk.Frame(self.sn, padding=(0, 8, 0, 0))
        self.sn.add(tf2, text="Test results")
        box, self.rtests = app.text_box(tf2, readonly=True, height=6)
        box.pack(fill="both", expand=True)
        self._empty_response()

    def _init_sash(self):
        h = self.pw.winfo_height()
        if not self._sash_set and h > 300:
            self._sash_set = True
            try:
                self.pw.sashpos(0, int(h * 0.46))
            except tk.TclError:
                pass

    # ---------- method colour, URL hint
    def _method_style(self):
        m = self.method.get()
        self.method.configure(style=f"{m}.Method.TCombobox" if m in SHORT else "TCombobox")

    def _method_changed(self, _e=None):
        self._method_style()
        self.app.retitle(self)
        self.method.selection_clear()

    def _pill(self, text, kind="muted"):
        self.lb_status.configure(text=text, style=f"Pill.{kind}.TLabel" if text else "PillOff.TLabel")

    def update_hint(self):
        url = self.url_var.get()
        text, style = "", "Hint.TLabel"
        if "{{" in url:
            res = subst(url, self.app.vars())
            miss = sorted({m.group(1) for m in VAR_RE.finditer(res)})
            if miss:
                text = "⚠  Undefined variable" + ("s" if len(miss) > 1 else "") + ": " + ", ".join("{{" + n + "}}" for n in miss)
                style = "HintWarn.TLabel"
            else:
                text = "→  " + res
        if text:
            self.hint.configure(text=text, style=style)
            if not self._hint_on:
                self.hint.pack(fill="x", padx=14, before=self.pw)
                self._hint_on = True
        elif self._hint_on:
            self.hint.pack_forget()
            self._hint_on = False

    # ---------- state
    def get_state(self):
        return {"name": self.name, "method": self.method.get(), "url": self.url_var.get(),
                "params": self.params.get(), "headers": self.hdrs.get(),
                "auth": {"type": self.auth_type.get(), "token": self.a_vars["token"].get(), "user": self.a_vars["user"].get(),
                         "pass": self.a_vars["pass"].get(), "key": self.a_vars["key"].get(),
                         "value": self.a_vars["value"].get(), "in": self.a_in.get()},
                "body": {"mode": self.body_mode.get(), "raw_type": self.raw_type.get(), "raw": self.raw.get("1.0", "end-1c"),
                         "form": self.form.get(), "urlenc": self.urlenc.get(), "file": self.file_var.get()},
                "tests": self.tests.get("1.0", "end-1c"),
                "opts": {"timeout": self._timeout(), "verify": self.verify.get(), "follow": self.follow.get()},
                "ref": self.ref}

    def set_state(self, s):
        self._guard = True
        self.name, self.ref = s.get("name", ""), s.get("ref")
        self.method.set(s["method"])
        self._method_style()
        self.url_var.set(s["url"])
        self.params.set(s["params"])
        self.hdrs.set(s["headers"])
        a = s["auth"]
        self.auth_type.set(a["type"])
        self.a_in.set(a["in"])
        for k in self.a_vars:
            self.a_vars[k].set(a.get(k, ""))
        b = s["body"]
        self.body_mode.set(b["mode"])
        self.raw_type.set(b["raw_type"])
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", b["raw"])
        self.form.set(b["form"])
        self.urlenc.set(b["urlenc"])
        self.file_var.set(b["file"])
        self.tests.delete("1.0", "end")
        self.tests.insert("1.0", s["tests"])
        o = s["opts"]
        self.verify.set(o.get("verify", True))
        self.follow.set(o.get("follow", True))
        self.timeout.set(o.get("timeout", 30))
        self._guard = False
        if not s["params"] and "?" in s["url"]:
            self._sync_params_from_url()
        self._auth_layout()
        self._body_layout()
        self._hl_vars()
        self._refresh_counts()
        self.update_hint()

    def _timeout(self):
        try:
            return max(1.0, float(self.timeout.get()))
        except ValueError:
            return 30.0

    # ---------- small UI behaviours
    def _auth_layout(self):
        t = self.auth_type.get()
        for l, w, types in self._arows:
            if types is None or t in types:
                l.grid()
                w.grid()
            else:
                l.grid_remove()
                w.grid_remove()
        self._refresh_counts()

    def _body_layout(self):
        self.bf[self.body_mode.get()].tkraise()
        self._refresh_counts()

    def _refresh_counts(self):
        n = lambda rows: sum(1 for on, k, v in rows if on and k)
        pc, hc = n(self.params.get()), n(self.hdrs.get())
        self.rn.tab(self.f_params, text=f"Params ({pc})" if pc else "Params")
        self.rn.tab(self.f_hdr, text=f"Headers ({hc})" if hc else "Headers")
        self.rn.tab(self.f_auth, text="Auth •" if self.auth_type.get() != "None" else "Auth")
        self.rn.tab(self.f_body, text="Body •" if self.body_mode.get() != "none" else "Body")
        self.rn.tab(self.f_tests, text="Tests •" if self.tests.get("1.0", "end-1c").strip() else "Tests")

    def _url_changed(self, *_):
        if self._guard:
            return
        self._sync_params_from_url()
        self.app.retitle(self)
        self.update_hint()

    def _sync_params_from_url(self):
        url = self.url_var.get()
        q = url.partition("?")[2]
        pairs = []
        if q:
            for part in q.split("&"):
                if part:
                    k, _, v = part.partition("=")
                    pairs.append([True, k, v])
        disabled = [r for r in self.params.rows if not r[0] and (r[1] or r[2])]
        self.params.set(pairs + disabled)
        self._refresh_counts()

    def _params_changed(self):
        base = self.url_var.get().partition("?")[0]
        q = "&".join(f"{k}={v}" for on, k, v in self.params.get() if on and (k or v))
        self._guard = True
        self.url_var.set(base + ("?" + q if q else ""))
        self._guard = False
        self._refresh_counts()
        self.update_hint()

    def _check_curl(self):
        t = self.url_var.get().strip()
        if t.lower().startswith("curl "):
            try:
                st = parse_curl(t)
            except ValueError:
                return
            st["ref"], st["name"] = self.ref, self.name
            self.set_state(norm_state(st))
            self.app.retitle(self)
            self.app.say("Imported cURL command")

    def _hl_vars(self):
        t = self.raw
        t.tag_remove("var", "1.0", "end")
        s = t.get("1.0", "end-1c")
        if "{{" not in s or len(s) > 200000:
            return
        for ln, line in enumerate(s.split("\n"), 1):
            for m in VAR_RE.finditer(line):
                t.tag_add("var", f"{ln}.{m.start()}", f"{ln}.{m.end()}")

    def beautify(self):
        s = self.raw.get("1.0", "end-1c")
        try:
            if self.raw_type.get() == "XML":
                x = minidom.parseString(s.encode()).toprettyxml(indent="  ")
                out = "\n".join(l for l in x.splitlines() if l.strip())
            else:
                out = json.dumps(json.loads(s), indent=2, ensure_ascii=False)
        except Exception as e:
            self.app.notify(f"Can't format body:\n{e}", error=True)
            return
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", out)
        self._hl_vars()

    # ---------- sending
    def send(self):
        if self.busy:
            self.cancel()
            return
        state = self.get_state()
        try:
            prep = prepare(state, self.app.vars())
        except (ValueError, OSError) as e:
            self._show_error(e)
            return
        prep["proxy"] = (self.app.data.get("proxy") or "").strip()
        if prep["unresolved"]:
            self.app.say("Undefined variable: " + ", ".join("{{" + n + "}}" for n in prep["unresolved"]), 6000, "warn")
        self.busy = True
        token = self.token = object()
        self.send_btn.configure(text="Cancel", style="Danger.TButton")
        self._pill("Sending…", "muted")
        self.lb_time.configure(text="")
        self.lb_size.configure(text="")
        self.app.run_bg(lambda: execute(prep, self.app.jar), lambda res: self._finish(token, state, res))

    def _send_idle(self):
        self.send_btn.configure(text="Send  ▸", style="Accent.TButton")

    def cancel(self):
        self.busy, self.token = False, None
        self._send_idle()
        self._pill("Cancelled", "warn")

    def _show_error(self, e):
        self.busy, self.token = False, None
        self._send_idle()
        self._pill("Error", "err")
        self.lb_time.configure(text="")
        self.lb_size.configure(text="")
        msg = getattr(e, "reason", None) or e
        self._ro(self.rbody, f"Request failed:\n{msg}")
        self._ro(self.rhead, "")
        self._ro(self.rtests, "")
        self.ctv.delete(*self.ctv.get_children())

    def _finish(self, token, state, res):
        if token is not self.token:
            return
        if isinstance(res, Exception):
            self._show_error(res)
            return
        self.busy, self.token = False, None
        self._send_idle()
        c = res["status"]
        self._pill(f"{c} {res['reason']}".strip(), "ok" if c < 300 else "info" if c < 400 else "warn" if c < 500 else "err")
        self.lb_time.configure(text=f"{res['time'] * 1000:.0f} ms")
        self.lb_size.configure(text=human(res["size"]))
        self.resp_text, self.resp_pretty, self.resp_kind, self.last_raw = res["text"], res["pretty"], res["kind"], res["raw"]
        self.res = res
        self._render_body()
        self._ro(self.rhead, "\n".join(f"{k}: {v}" for k, v in res["headers"]))
        self.sn.tab(1, text=f"Headers ({len(res['headers'])})")
        self.ctv.delete(*self.ctv.get_children())
        n = 0
        for h in res["cookies"]:
            for row in parse_set_cookie(h):
                self.ctv.insert("", "end", values=row)
                n += 1
        self.sn.tab(2, text=f"Cookies ({n})" if n else "Cookies")
        results = run_tests(state["tests"], res, self.app.set_var)
        t = self.rtests
        t.configure(state="normal")
        t.delete("1.0", "end")
        for ok, msg in results:
            t.insert("end", ("✓  " if ok else "✗  ") + msg + "\n", "ok" if ok else "bad")
        if not results:
            t.insert("end", "No tests defined (see the Tests tab of the request).", "muted")
        t.configure(state="disabled")
        npass = sum(1 for ok, _ in results if ok)
        self.sn.tab(3, text=f"Test results ({npass}/{len(results)})" if results else "Test results")
        self.app.add_history(state)
        self.app.refresh_hints()
        self.app.update_env_info()

    # ---------- response rendering
    def _ro(self, t, text):
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text)
        t.configure(state="disabled")

    def _empty_response(self):
        t = self.rbody
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", "◉\n", ("glyph",))
        t.insert("end", "Press Send to run this request\n", ("muted", "center"))
        t.insert("end", "Ctrl+Enter send   ·   Ctrl+K quick open   ·   paste a cURL command into the URL bar", ("muted", "center2"))
        t.configure(state="disabled")
        self._pill("")

    def _render_body(self):
        pretty = self.view.get() == "Pretty" and self.resp_pretty is not None
        text = self.resp_pretty if pretty else self.resp_text
        kind = self.resp_kind if pretty else "text"
        shown = text[:MAX_SHOW]
        t = self.rbody
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag in ("jkey", "jstr", "jnum", "jkw", "found"):
            t.tag_remove(tag, "1.0", "end")
        t.insert("1.0", shown)
        if kind == "json" and len(shown) < HL_LIMIT:
            self._hl_json(shown)
        if len(text) > MAX_SHOW or getattr(self, "res", {}).get("truncated"):
            t.insert("end", "\n\n… output truncated (use Save… to get the full body)", "muted")
        t.configure(state="disabled")
        self.find_lb.configure(text="")
        self._fpos = None

    _JSON_TOK = re.compile(r'(?P<jkey>"(?:[^"\\]|\\.)*"(?=\s*:))|(?P<jstr>"(?:[^"\\]|\\.)*")|'
                           r'(?P<jnum>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<jkw>\btrue\b|\bfalse\b|\bnull\b)')

    def _hl_json(self, s):
        t = self.rbody
        for ln, line in enumerate(s.split("\n"), 1):
            for m in self._JSON_TOK.finditer(line):
                t.tag_add(m.lastgroup, f"{ln}.{m.start()}", f"{ln}.{m.end()}")

    def find(self):
        term = self.find_var.get()
        t = self.rbody
        t.tag_remove("found", "1.0", "end")
        if not term:
            self.find_lb.configure(text="")
            return
        idx, n, first = "1.0", 0, None
        while n < 2000:
            idx = t.search(term, idx, stopindex="end", nocase=True)
            if not idx:
                break
            end = f"{idx}+{len(term)}c"
            t.tag_add("found", idx, end)
            first = first or idx
            n += 1
            idx = end
        cur = getattr(self, "_fpos", None)
        nxt = t.search(term, f"{cur}+1c" if cur else "1.0", stopindex="end", nocase=True) or first
        self._fpos = nxt
        if nxt:
            t.see(nxt)
        self.find_lb.configure(text=f"{n} match{'es' if n != 1 else ''}" if n else "no match")

    def copy_resp(self):
        self.clipboard_clear()
        self.clipboard_append(self.resp_pretty if (self.view.get() == "Pretty" and self.resp_pretty is not None) else self.resp_text)
        self.app.say("Response copied")

    def save_resp(self):
        if not self.last_raw and not self.resp_text:
            return
        p = filedialog.asksaveasfilename(parent=self, initialfile="response.txt")
        if p:
            with open(p, "wb") as f:
                f.write(self.last_raw)
            self.app.say(f"Saved {p}")

    def show_code(self):
        try:
            p = prepare(self.get_state(), self.app.vars())
        except (ValueError, OSError) as e:
            self.app.notify(str(e), error=True)
            return
        self.app.code_dialog(p)


# =============================================================== application
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.data = load_data()
        if self.data["theme"] not in PALETTES:
            self.data["theme"] = "dark"
        self.pal = PALETTES[self.data["theme"]]
        self.jar = http.cookiejar.CookieJar()
        self.q, self.jobs, self._polling, self._say_id = queue.Queue(), 0, False, None
        self.combos, self.texts, self.menus = [], [], []
        self._setup_fonts()
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self._build()
        self.apply_theme(self.data["theme"])
        self._set_icon()
        self.geometry(self.data.get("geometry") or "1240x780")
        self.minsize(900, 560)
        self._restore_session()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq, fn in (("<Control-Return>", lambda e: self.cur_pane().send()), ("<Control-s>", lambda e: self.save_pane(self.cur_pane())),
                        ("<Control-t>", lambda e: self.new_tab()), ("<Control-k>", lambda e: self.quick_open()),
                        ("<Control-Tab>", lambda e: self.cycle_tab(1)), ("<Control-ISO_Left_Tab>", lambda e: self.cycle_tab(-1)), ("<Control-w>", lambda e: self.close_tab()),
                        ("<Control-l>", lambda e: self.focus_url()), ("<Control-Shift-E>", lambda e: self.manage_envs()),
                        ("<Control-plus>", lambda e: self.zoom(1)), ("<Control-equal>", lambda e: self.zoom(1)),
                        ("<Control-minus>", lambda e: self.zoom(-1))):
            self.bind(seq, fn)

    # ---------- fonts & theme
    def _setup_fonts(self):
        fams = set(tkfont.families())
        ui = next((f for f in ("Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans") if f in fams), None)
        mono = next((f for f in ("JetBrains Mono", "Fira Code", "Source Code Pro", "DejaVu Sans Mono", "Liberation Mono",
                                 "Ubuntu Mono", "Noto Sans Mono") if f in fams), "TkFixedFont")
        size = int(self.data.get("zoom") or 10)
        for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(n).configure(size=size, **({"family": ui} if ui else {}))
        self.f_ui = tkfont.nametofont("TkDefaultFont")
        fam = self.f_ui.actual("family")
        self.f_mono = tkfont.Font(family=mono, size=size)
        self.f_bold = tkfont.Font(family=fam, size=size, weight="bold")
        self.f_brand = tkfont.Font(family=fam, size=size + 2, weight="bold")
        self.f_small = tkfont.Font(family=fam, size=max(8, size - 1))
        self.f_tiny = tkfont.Font(family=fam, size=max(7, size - 2), weight="bold")
        self.f_glyph = tkfont.Font(family=fam, size=size + 14)

    def zoom(self, d):
        size = max(8, min(20, int(self.f_ui.cget("size")) + d))
        for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(n).configure(size=size)
        for f, s in ((self.f_mono, size), (self.f_bold, size), (self.f_brand, size + 2),
                     (self.f_small, max(8, size - 1)), (self.f_tiny, max(7, size - 2)), (self.f_glyph, size + 14)):
            f.configure(size=s)
        self.data["zoom"] = size
        rh = self.f_ui.metrics("linespace") + 10
        for n in ("Treeview", "Side.Treeview"):
            self.style.configure(n, rowheight=rh)

    def apply_theme(self, name):
        P = self.pal = PALETTES[name]
        self.data["theme"] = name
        st = self.style
        self.configure(bg=P["bg"])
        st.configure(".", background=P["bg"], foreground=P["fg"], fieldbackground=P["panel"], bordercolor=P["border"],
                     lightcolor=P["border"], darkcolor=P["border"], troughcolor=P["bg"], focuscolor=P["bg"],
                     selectbackground=P["selbg"], selectforeground=P["fg"], insertcolor=P["accent"])
        # frames
        for n, bg in (("TFrame", P["bg"]), ("Bar.TFrame", P["bg2"]), ("Tab.TFrame", P["bg2"]), ("TabSel.TFrame", P["bg"]),
                      ("Rule.TFrame", P["border"]), ("SegLine.TFrame", P["bg"]), ("SegLineOn.TFrame", P["accent"])):
            st.configure(n, background=bg)
        # labels
        st.configure("TLabel", background=P["bg"], foreground=P["fg"])
        st.configure("Muted.TLabel", foreground=P["muted"])
        st.configure("Bold.TLabel", font=self.f_bold)
        st.configure("Brand.TLabel", font=self.f_brand, foreground=P["accent_t"])
        st.configure("Hint.TLabel", foreground=P["muted"], font=self.f_small)
        st.configure("HintWarn.TLabel", foreground=P["warn"], font=self.f_small)
        st.configure("Bar.TLabel", background=P["bg2"], foreground=P["muted"], font=self.f_small)
        st.configure("BarWarn.TLabel", background=P["bg2"], foreground=P["warn"], font=self.f_small)
        st.configure("BarOk.TLabel", background=P["bg2"], foreground=P["accent_t"], font=self.f_small)
        # section tabs
        st.configure("Seg.TLabel", foreground=P["muted"], padding=(14, 8))
        st.configure("SegHover.TLabel", foreground=P["fg"], padding=(14, 8))
        st.configure("SegSel.TLabel", foreground=P["accent_t"], padding=(14, 8))
        # request tabs
        st.configure("Doc.TLabel", background=P["bg2"], foreground=P["muted"])
        st.configure("DocSel.TLabel", background=P["bg"], foreground=P["fg"])
        st.configure("Close.TLabel", background=P["bg2"], foreground=P["muted"])
        st.configure("CloseSel.TLabel", background=P["bg"], foreground=P["muted"])
        st.configure("CloseHot.TLabel", background=P["bg2"], foreground=P["err"])
        st.configure("CloseHotSel.TLabel", background=P["bg"], foreground=P["err"])
        for m in METHODS:
            st.configure(f"{m}.Doc.TLabel", background=P["bg2"], foreground=P["m_" + m], font=self.f_tiny)
            st.configure(f"{m}.DocSel.TLabel", background=P["bg"], foreground=P["m_" + m], font=self.f_tiny)
        # status pills
        for kind, bg, fg in (("ok", P["accent"], P["accent_fg"]), ("info", P["info"], P["pill_fg"]), ("warn", P["warn"], P["pill_fg"]),
                             ("err", P["err"], P["pill_fg"]), ("muted", P["btn"], P["muted"])):
            st.configure(f"Pill.{kind}.TLabel", background=bg, foreground=fg, padding=(11, 3), font=self.f_bold)
        st.configure("PillOff.TLabel", padding=0)
        # buttons
        st.configure("TButton", background=P["btn"], foreground=P["fg"], padding=(14, 6), borderwidth=1, relief="flat",
                     focusthickness=0, bordercolor=P["border"], anchor="center", width=0)
        st.map("TButton", background=[("pressed", P["btn_h"]), ("active", P["btn_h"]), ("disabled", P["bg"])],
               foreground=[("disabled", P["muted"])], bordercolor=[("active", P["accent_t"])])
        st.configure("Tool.TButton", padding=(10, 4))
        st.configure("Ghost.TButton", background=P["bg"], bordercolor=P["bg"], padding=(10, 5))
        st.map("Ghost.TButton", background=[("active", P["btn"])], bordercolor=[("active", P["btn"])])
        st.configure("GhostBar.TButton", background=P["bg2"], bordercolor=P["bg2"], padding=(6, 3))
        st.map("GhostBar.TButton", background=[("active", P["btn"])], bordercolor=[("active", P["btn"])],
               foreground=[("active", P["accent_t"])])
        st.configure("Accent.TButton", background=P["accent"], foreground=P["accent_fg"], bordercolor=P["accent"],
                     font=self.f_bold, padding=(18, 7))
        st.map("Accent.TButton", background=[("pressed", P["accent_h"]), ("active", P["accent_h"])],
               bordercolor=[("active", P["accent_h"])], foreground=[("active", P["accent_fg"])])
        st.configure("Danger.TButton", background=P["err"], foreground=P["pill_fg"], bordercolor=P["err"],
                     font=self.f_bold, padding=(18, 7))
        st.map("Danger.TButton", background=[("active", P["err"])], foreground=[("active", P["pill_fg"])],
               bordercolor=[("active", P["err"])])
        # inputs
        for w in ("TEntry", "TCombobox", "TSpinbox"):
            st.configure(w, fieldbackground=P["panel"], foreground=P["fg"], bordercolor=P["border"], lightcolor=P["border"],
                         darkcolor=P["border"], insertcolor=P["accent"], padding=6, arrowcolor=P["muted"])
            st.map(w, bordercolor=[("focus", P["accent_t"])], lightcolor=[("focus", P["accent_t"])],
                   darkcolor=[("focus", P["accent_t"])])
        st.configure("TSpinbox", background=P["btn"])
        st.configure("TCombobox", background=P["btn"])
        st.map("TCombobox", fieldbackground=[("readonly", P["panel"])], foreground=[("readonly", P["fg"])],
               selectbackground=[("readonly", P["panel"])], selectforeground=[("readonly", P["fg"])],
               background=[("active", P["btn_h"])], arrowcolor=[("active", P["accent_t"])])
        for m in METHODS:
            sty = f"{m}.Method.TCombobox"
            st.configure(sty, foreground=P["m_" + m], font=self.f_bold)
            st.map(sty, foreground=[("readonly", P["m_" + m])], selectforeground=[("readonly", P["m_" + m])])
        # lists / tables
        rh = self.f_ui.metrics("linespace") + 10
        st.configure("Treeview", background=P["panel"], fieldbackground=P["panel"], foreground=P["fg"], borderwidth=0, rowheight=rh)
        st.configure("Side.Treeview", background=P["bg2"], fieldbackground=P["bg2"], foreground=P["fg"], borderwidth=0, rowheight=rh)
        st.map("Treeview", background=[("selected", P["selbg"])], foreground=[("selected", P["fg"])])
        st.configure("Treeview.Heading", background=P["bg2"], foreground=P["muted"], relief="flat", padding=(8, 6), font=self.f_bold)
        st.map("Treeview.Heading", background=[("active", P["btn"])])
        # toggles
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=P["bg"], foreground=P["fg"], indicatorbackground=P["panel"], indicatorforeground=P["accent_t"],
                         upperbordercolor=P["muted"], lowerbordercolor=P["muted"], focuscolor=P["bg"], padding=3)
            st.map(w, background=[("active", P["bg"])], foreground=[("active", P["accent_t"])],
                   indicatorbackground=[("active", P["panel"]), ("pressed", P["panel"])],
                   upperbordercolor=[("selected", P["accent_t"])], lowerbordercolor=[("selected", P["accent_t"])])
        # thin scrollbars without arrows
        for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
            st.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
                (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("TScrollbar", background=P["btn_h"], troughcolor=P["bg"], bordercolor=P["bg"], lightcolor=P["btn_h"],
                     darkcolor=P["btn_h"], arrowsize=11, relief="flat", gripcount=0)
        st.map("TScrollbar", background=[("pressed", P["accent_t"]), ("active", P["muted"])],
               lightcolor=[("pressed", P["accent_t"]), ("active", P["muted"])],
               darkcolor=[("pressed", P["accent_t"]), ("active", P["muted"])])
        st.configure("TPanedwindow", background=P["bg"])
        st.configure("Sash", sashthickness=6, gripcount=0, background=P["bg"])
        for k, v in (("background", P["panel"]), ("foreground", P["fg"]), ("selectBackground", P["selbg"]), ("selectForeground", P["fg"])):
            self.option_add(f"*TCombobox*Listbox.{k}", v)
        # live widgets
        self.texts = [w for w in self.texts if w.winfo_exists()]
        for w in self.texts:
            self._style_text(w)
        self.menus = [m for m in self.menus if m.winfo_exists()]
        for m in self.menus:
            self._style_menu(m)
        self.combos = [c for c in self.combos if c.winfo_exists()]
        for c in self.combos:
            self._style_combo(c)
        if hasattr(self, "tree"):
            self.tag_methods(self.tree)
            self.tag_methods(self.hist)
            self.draw_logo()
            self.theme_btn.configure(text="☀" if name == "dark" else "☾")

    def tag_methods(self, tv):
        for m in METHODS:
            tv.tag_configure("m_" + m, foreground=self.pal["m_" + m])
        tv.tag_configure("folder", font=self.f_bold)
        tv.tag_configure("hint", foreground=self.pal["muted"])

    def draw_logo(self):
        c, P = self.logo, self.pal
        c.configure(bg=P["bg"])
        c.delete("all")
        for r, t in ((12.5, 0.12), (10.8, 0.28), (9, 0.5)):
            c.create_oval(13 - r, 13 - r, 13 + r, 13 + r, fill=blend(P["bg"], P["accent"], t), outline="")
        c.create_oval(6, 6, 20, 20, fill=P["accent"], outline="")
        c.create_oval(10, 10, 16, 16, fill=P["bg"], outline="")

    def _set_icon(self):
        try:
            n, acc, dark = 64, (57, 255, 136), (7, 18, 11)
            img = tk.PhotoImage(width=n, height=n)
            c = (n - 1) / 2
            rows = []
            for y in range(n):
                row = []
                for x in range(n):
                    r = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
                    if r <= 11:
                        col = dark
                    elif r <= 22:
                        col = acc
                    elif r <= 28:
                        t = 0.55 - (r - 22) / 6 * 0.45
                        col = tuple(round(d + (a - d) * t) for a, d in zip(acc, dark))
                    else:
                        col = dark
                    row.append("#%02x%02x%02x" % col)
                rows.append("{" + " ".join(row) + "}")
            img.put(" ".join(rows))
            for cx, cy in ((0, 0), (n - 1, 0), (0, n - 1), (n - 1, n - 1)):
                for dx in range(10):
                    for dy in range(10):
                        px, py = abs(cx - dx), abs(cy - dy)
                        if (10 - dx - 0.5) ** 2 + (10 - dy - 0.5) ** 2 > 100:
                            img.transparency_set(px, py, True)
            self._icon = img
            self.iconphoto(True, img)
        except Exception:
            pass

    def toggle_theme(self):
        self.apply_theme("dark" if self.data["theme"] == "light" else "light")
        self.persist()

    def reg_text(self, w):
        self.texts.append(w)
        self._style_text(w)
        return w

    def _style_text(self, w):
        P = self.pal
        w.configure(bg=P["text_bg"], fg=P["fg"], insertbackground=P["accent"], selectbackground=P["selbg"], selectforeground=P["fg"],
                    highlightthickness=1, highlightbackground=P["border"], highlightcolor=P["accent_t"], relief="flat", bd=0,
                    padx=8, pady=6, insertwidth=2)
        for tag in ("jkey", "jstr", "jnum", "jkw"):
            w.tag_configure(tag, foreground=P[tag])
        w.tag_configure("found", background=P["found"], foreground="#000000")
        w.tag_configure("var", foreground=P["accent_t"])
        w.tag_configure("muted", foreground=P["muted"])
        w.tag_configure("ok", foreground=P["ok"])
        w.tag_configure("bad", foreground=P["err"])
        w.tag_configure("glyph", justify="center", spacing1=40, font=self.f_glyph, foreground=P["accent_t"])
        w.tag_configure("center", justify="center", spacing1=10, font=self.f_brand)
        w.tag_configure("center2", justify="center", spacing1=8, font=self.f_small)
        w.tag_raise("sel")

    def text_box(self, parent, readonly=False, height=6):
        f = ttk.Frame(parent)
        t = tk.Text(f, font=self.f_mono, wrap="none", undo=not readonly, height=height)
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=autosb(ys), xscrollcommand=autosb(xs))
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self.reg_text(t)
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end-1c"), "break")[1])
        if readonly:
            t.bind("<Button-1>", lambda e: t.focus_set())
            t.configure(state="disabled")
        return f, t

    def reg_combo(self, c):
        self.combos.append(c)

    def _style_combo(self, c):
        try:
            pd = self.tk.eval(f"ttk::combobox::PopdownWindow {c}")
            lb = self.nametowidget(pd + ".f.l")
            lb.configure(bg=self.pal["panel"], fg=self.pal["fg"], selectbackground=self.pal["selbg"], selectforeground=self.pal["fg"],
                         bd=0, highlightthickness=0)
        except Exception:
            pass

    def mk_menu(self, parent=None):
        m = tk.Menu(parent or self, tearoff=0)
        self.menus.append(m)
        self._style_menu(m)
        return m

    def _style_menu(self, m):
        P = self.pal
        m.configure(bg=P["panel"], fg=P["fg"], activebackground=P["accent"], activeforeground=P["accent_fg"],
                    disabledforeground=P["muted"], bd=1, relief="flat", activeborderwidth=0)

    def popup(self, menu, widget):
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    # ---------- layout
    def _menu_file(self):
        m = self.mk_menu()
        m.add_command(label="New tab", accelerator="Ctrl+T", command=self.new_tab)
        m.add_command(label="Close tab", accelerator="Ctrl+W", command=self.close_tab)
        m.add_command(label="Quick open…", accelerator="Ctrl+K", command=self.quick_open)
        m.add_separator()
        im = self.mk_menu(m)
        m.add_cascade(label="Import", menu=im)
        im.add_command(label="Postman collection (v2.x JSON)…", command=self.import_collection)
        im.add_command(label="Postman environment…", command=self.import_env_file)
        im.add_command(label="cURL command…", command=self.import_curl_dialog)
        m.add_command(label="Export selected collection / folder…", command=lambda: self.export_node(self.tree_sel()))
        m.add_separator()
        m.add_command(label="Settings…", command=self.settings_dialog)
        m.add_command(label="Quit", command=self.on_close)
        return m

    def _menu_tools(self):
        m = self.mk_menu()
        m.add_command(label="Environments…", accelerator="Ctrl+Shift+E", command=self.manage_envs)
        m.add_command(label="Cookies…", command=self.cookie_dialog)
        m.add_command(label="Run selected collection / folder…", command=lambda: self.run_folder(self.tree_sel()))
        m.add_separator()
        m.add_command(label="Clear history", command=self.clear_history)
        return m

    def _menu_view(self):
        m = self.mk_menu()
        m.add_command(label="Toggle dark / light theme", command=self.toggle_theme)
        m.add_command(label="Zoom in", accelerator="Ctrl++", command=lambda: self.zoom(1))
        m.add_command(label="Zoom out", accelerator="Ctrl+-", command=lambda: self.zoom(-1))
        return m

    def _menu_help(self):
        m = self.mk_menu()
        m.add_command(label="Shortcuts & tips", command=self.help_dialog)
        return m

    def _build(self):
        # --- top bar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(10, 6))
        self.logo = tk.Canvas(bar, width=26, height=26, highlightthickness=0, bd=0)
        self.logo.pack(side="left")
        ttk.Label(bar, text="PHOSPHOR", style="Brand.TLabel").pack(side="left", padx=(8, 20))
        for label, build in (("File", self._menu_file), ("Tools", self._menu_tools), ("View", self._menu_view), ("Help", self._menu_help)):
            menu = build()
            b = ttk.Button(bar, text=label, style="Ghost.TButton")
            b.configure(command=lambda m=menu, b=b: self.popup(m, b))
            b.pack(side="left")
        self.theme_btn = ttk.Button(bar, text="☾", width=3, style="Ghost.TButton", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(6, 0))
        Tip(self.theme_btn, "Switch dark / light theme", self)
        gear = ttk.Button(bar, text="⚙", width=3, style="Ghost.TButton", command=self.manage_envs)
        gear.pack(side="right", padx=(6, 0))
        Tip(gear, "Manage environments   Ctrl+Shift+E", self)
        self.env_cb = ttk.Combobox(bar, state="readonly", width=24)
        self.env_cb.pack(side="right")
        self.reg_combo(self.env_cb)
        self.env_cb.bind("<<ComboboxSelected>>", self._env_selected)
        ttk.Label(bar, text="Environment", style="Muted.TLabel").pack(side="right", padx=10)

        # --- status bar (packed first so it stays at the bottom)
        sb = ttk.Frame(self, style="Bar.TFrame")
        sb.pack(side="bottom", fill="x")
        ttk.Frame(self, style="Rule.TFrame", height=1).pack(side="bottom", fill="x")
        self.status = ttk.Label(sb, style="Bar.TLabel", anchor="w")
        self.status.pack(side="left", padx=14, pady=5)
        self.env_info = ttk.Label(sb, style="Bar.TLabel")
        self.env_info.pack(side="right", padx=14)

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        side = ttk.Frame(pw, width=300)
        side.pack_propagate(False)
        pw.add(side, weight=0)
        main = ttk.Frame(pw)
        pw.add(main, weight=1)

        sn = SegTabs(side)
        sn.pack(fill="both", expand=True)
        cf = ttk.Frame(sn, padding=(0, 10, 8, 0))
        sn.add(cf, text="Collections")
        top = ttk.Frame(cf)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="⌕", style="Muted.TLabel").pack(side="left", padx=(2, 6))
        self.flt_var = tk.StringVar()
        fe = ttk.Entry(top, textvariable=self.flt_var)
        fe.pack(side="left", fill="x", expand=True, ipady=2)
        Tip(fe, "Filter collections", self)
        self.flt_var.trace_add("write", lambda *a: self.refresh_tree())
        plus = ttk.Button(top, text="＋", width=3, style="Tool.TButton", command=self.new_collection)
        plus.pack(side="left", padx=(6, 0))
        Tip(plus, "New collection", self)
        tf = ttk.Frame(cf)
        tf.pack(fill="both", expand=True)
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", style="Side.Treeview")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=autosb(ys))
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._tree_open)
        self.tree.bind("<Return>", lambda e: self._tree_open_sel())
        self.tree.bind("<Button-3>", self._tree_menu)
        self.tree.bind("<Delete>", lambda e: self.delete_node(self.tree_sel()))
        hf = ttk.Frame(sn, padding=(0, 10, 8, 0))
        sn.add(hf, text="History")
        hf2 = ttk.Frame(hf)
        hf2.pack(fill="both", expand=True)
        hf2.rowconfigure(0, weight=1)
        hf2.columnconfigure(0, weight=1)
        self.hist = ttk.Treeview(hf2, show="tree", selectmode="browse", style="Side.Treeview")
        ys2 = ttk.Scrollbar(hf2, orient="vertical", command=self.hist.yview)
        self.hist.configure(yscrollcommand=autosb(ys2))
        self.hist.grid(row=0, column=0, sticky="nsew")
        ys2.grid(row=0, column=1, sticky="ns")
        self.hist.bind("<Double-1>", self._hist_open)
        ttk.Button(hf, text="Clear history", style="Tool.TButton", command=self.clear_history).pack(anchor="w", pady=(8, 0))

        self.tabs = DocTabs(main, self, self.new_tab, self.close_tab, self._tab_menu)
        self.tabs.pack(fill="both", expand=True)
        self.ctx = self.mk_menu()
        self.refresh_tree()
        self.refresh_history()
        self.refresh_env_combo()

    # ---------- helpers
    def say(self, msg, ms=4000, kind="info"):
        self.status.configure(text=msg, style={"warn": "BarWarn.TLabel", "ok": "BarOk.TLabel"}.get(kind, "Bar.TLabel"))
        if self._say_id:
            self.after_cancel(self._say_id)
        self._say_id = self.after(ms, lambda: self.status.configure(text=""))

    def notify(self, msg, title=None, error=False):
        win = tk.Toplevel(self)
        win.title(title or (APP + (" — error" if error else "")))
        win.resizable(False, False)
        f = ttk.Frame(win, padding=20)
        f.pack()
        ttk.Label(f, text=msg, wraplength=440, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="e", pady=(16, 0))
        b = ttk.Button(row, text="OK", style="Accent.TButton", command=win.destroy)
        b.pack()
        win.bind("<Return>", lambda e: win.destroy())
        self.modal(win)
        b.focus_set()
        win.wait_window()

    def ask_yes(self, msg, yes="Delete"):
        win = tk.Toplevel(self)
        win.title(APP)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=20)
        f.pack()
        ttk.Label(f, text=msg, wraplength=440, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="e", pady=(16, 0))
        res = [False]

        def go(v):
            res[0] = v
            win.destroy()
        ttk.Button(row, text="Cancel", command=lambda: go(False)).pack(side="right")
        b = ttk.Button(row, text=yes, style="Danger.TButton", command=lambda: go(True))
        b.pack(side="right", padx=8)
        self.modal(win)
        b.focus_set()
        win.wait_window()
        return res[0]

    def settings_dialog(self):
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        f = ttk.Frame(win, padding=20)
        f.pack()
        ttk.Label(f, text="HTTP proxy (optional)").grid(row=0, column=0, sticky="w")
        px = tk.StringVar(value=self.data.get("proxy", ""))
        e = ttk.Entry(f, textvariable=px, width=46)
        e.grid(row=1, column=0, sticky="we", pady=(6, 3), ipady=3)
        ttk.Label(f, text="e.g. http://127.0.0.1:8080  —  leave empty to use the system settings", style="Hint.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 14))
        rt = tk.BooleanVar(value=self.data.get("restore_tabs", True))
        ttk.Checkbutton(f, text="Reopen my tabs next time", variable=rt).grid(row=3, column=0, sticky="w")

        def ok(*_):
            self.data["proxy"] = px.get().strip()
            self.data["restore_tabs"] = rt.get()
            self.persist()
            win.destroy()
            self.say("Settings saved", kind="ok")
        row = ttk.Frame(f)
        row.grid(row=4, column=0, sticky="e", pady=(18, 0))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Save", style="Accent.TButton", command=ok).pack(side="right", padx=8)
        e.bind("<Return>", ok)
        self.modal(win)
        e.focus_set()

    def quick_open(self):
        entries = []

        def walk(nodes, path):
            for n in nodes:
                if n["type"] == "folder":
                    walk(n["children"], path + [n["name"]])
                else:
                    entries.append((n["req"].get("method", "GET"), n["name"], " / ".join(path), "c", n))
        walk(self.data["collections"], [])
        for it in self.data["history"][:40]:
            s = it["state"]
            entries.append((s["method"], s["url"], "history", "h", it))
        win = tk.Toplevel(self)
        win.title("Quick open")
        win.geometry("600x400")
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        var = tk.StringVar()
        e = ttk.Entry(f, textvariable=var, font=self.f_mono)
        e.pack(fill="x", ipady=5)
        ttk.Label(f, text="Type to search saved requests and history  ·  ↑ ↓ to move  ·  Enter to open", style="Hint.TLabel").pack(anchor="w", pady=(6, 6))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        tv = ttk.Treeview(box, show="tree", selectmode="browse", style="Side.Treeview")
        sc = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=autosb(sc))
        tv.grid(row=0, column=0, sticky="nsew")
        sc.grid(row=0, column=1, sticky="ns")
        self.tag_methods(tv)
        shown = []

        def fill(*_):
            q = var.get().strip().lower()
            tv.delete(*tv.get_children())
            shown.clear()
            for ent in entries:
                m, name, where, _k, _o = ent
                if q and q not in f"{m} {name} {where}".lower():
                    continue
                shown.append(ent)
                tv.insert("", "end", iid=str(len(shown) - 1), text=f"{SHORT.get(m, m):<5} {name}      {where}", tags=("m_" + m,))
                if len(shown) >= 200:
                    break
            if shown:
                tv.selection_set("0")
            else:
                tv.insert("", "end", iid="__none", text="No matches", tags=("hint",))

        def move(d):
            kids = tv.get_children()
            if not kids or kids[0] == "__none":
                return "break"
            s = tv.selection()
            i = (kids.index(s[0]) + d) if s else 0
            i = max(0, min(len(kids) - 1, i))
            tv.selection_set(kids[i])
            tv.see(kids[i])
            return "break"

        def opn(*_):
            s = tv.selection()
            if not s or not s[0].isdigit():
                return
            _m, _n, _w, kind, obj = shown[int(s[0])]
            win.destroy()
            if kind == "c":
                self.open_request_node(obj)
            else:
                self.new_tab(copy.deepcopy(obj["state"]))
        var.trace_add("write", fill)
        e.bind("<Down>", lambda ev: move(1))
        e.bind("<Up>", lambda ev: move(-1))
        e.bind("<Return>", opn)
        tv.bind("<Double-1>", opn)
        fill()
        self.modal(win)
        e.focus_set()

    def refresh_hints(self):
        for p in self.panes():
            p.update_hint()

    def update_env_info(self):
        env = self.cur_env()
        n = len(self.vars())
        self.env_info.configure(text=f"{env['name'] if env else 'No environment'}   ·   {n} variable{'s' if n != 1 else ''}")

    def _prep(self, st):
        p = prepare(st, self.vars())
        p["proxy"] = (self.data.get("proxy") or "").strip()
        return p

    def persist(self):
        save_data(self.data)

    def center(self, win):
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def modal(self, win):
        win.transient(self)
        win.configure(bg=self.pal["bg"])
        self.center(win)
        try:
            win.wait_visibility()
            win.grab_set()
        except tk.TclError:
            pass
        win.bind("<Escape>", lambda e: win.destroy())

    def ask_text(self, title, label, initial="", ok="OK"):
        win = tk.Toplevel(self)
        win.title(title)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=16)
        f.pack()
        ttk.Label(f, text=label).pack(anchor="w")
        var = tk.StringVar(value=initial)
        e = ttk.Entry(f, textvariable=var, width=46)
        e.pack(pady=10)
        res = [None]

        def okf(*_):
            res[0] = var.get().strip()
            win.destroy()
        row = ttk.Frame(f)
        row.pack(anchor="e")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text=ok, style="Accent.TButton", command=okf).pack(side="right", padx=6)
        e.bind("<Return>", okf)
        self.modal(win)
        e.focus_set()
        e.select_range(0, "end")
        win.wait_window()
        return res[0] or None

    def run_bg(self, work, done):
        """Run work() in a thread; call done(result) on the Tk thread. Polls only while jobs are active."""
        self.jobs += 1

        def target():
            try:
                r = work()
            except Exception as e:  # noqa
                r = e
            self.q.put((done, r))
        threading.Thread(target=target, daemon=True).start()
        self._start_poll()

    def post(self, fn):
        self.jobs += 1
        self.q.put((lambda _: fn(), None))
        self._start_poll()

    def _start_poll(self):
        if not self._polling:
            self._polling = True
            self.after(40, self._poll)

    def _poll(self):
        try:
            while True:
                done, r = self.q.get_nowait()
                self.jobs -= 1
                try:
                    done(r)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        if self.jobs > 0:
            self.after(60, self._poll)
        else:
            self._polling = False

    # ---------- environments
    def cur_env(self):
        for e in self.data["envs"]:
            if e["id"] == self.data["active_env"]:
                return e
        return None

    def vars(self):
        d = {}
        for on, k, v in self.data["globals"]:
            if on and k:
                d[k] = v
        env = self.cur_env()
        if env:
            for on, k, v in env["vars"]:
                if on and k:
                    d[k] = v
        return d

    def set_var(self, name, value):
        env = self.cur_env()
        rows = env["vars"] if env else self.data["globals"]
        for r in rows:
            if r[1] == name:
                r[2], r[0] = value, True
                break
        else:
            rows.append([True, name, value])
        self.persist()

    def refresh_env_combo(self):
        names = ["No Environment"] + [e["name"] for e in self.data["envs"]]
        self.env_cb.configure(values=names)
        env = self.cur_env()
        self.env_cb.current(names.index(env["name"]) if env else 0)
        self.update_env_info()

    def _env_selected(self, _=None):
        i = self.env_cb.current()
        self.data["active_env"] = self.data["envs"][i - 1]["id"] if i > 0 else ""
        self.persist()
        self.update_env_info()
        self.refresh_hints()
        self.env_cb.selection_clear()

    def manage_envs(self):
        if getattr(self, "_envwin", None) and self._envwin.winfo_exists():
            self._envwin.lift()
            return
        win = self._envwin = tk.Toplevel(self)
        win.title("Environments")
        win.geometry(f"780x470+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 70}")
        win.configure(bg=self.pal["bg"])
        win.transient(self)
        left = ttk.Frame(win, padding=10)
        left.pack(side="left", fill="y")
        lb = ttk.Treeview(left, show="tree", selectmode="browse", height=14)
        lb.pack(fill="y", expand=True)
        right = ttk.Frame(win, padding=10)
        right.pack(side="left", fill="both", expand=True)
        title = ttk.Label(right, style="Bold.TLabel")
        title.pack(anchor="w", pady=(0, 6))
        table = KVTable(right, height=10)
        table.pack(fill="both", expand=True)
        ttk.Label(right, style="Hint.TLabel", wraplength=470, justify="left",
                  text="Use {{name}} in the URL, headers, body and auth.   Built-in: {{$timestamp}}  {{$guid}}  {{$randomInt}}  {{$isoTimestamp}}").pack(anchor="w", pady=(8, 0))
        cur = {"id": "__g"}

        def env_of(i):
            return next((e for e in self.data["envs"] if e["id"] == i), None)

        def choose(*_):
            s = lb.selection()
            if not s:
                return
            cur["id"] = s[0]
            if s[0] == "__g":
                title.configure(text="Globals")
                table.set(self.data["globals"])
            else:
                title.configure(text=env_of(s[0])["name"])
                table.set(env_of(s[0])["vars"])

        def store():
            if cur["id"] == "__g":
                self.data["globals"] = table.get()
            elif env_of(cur["id"]):
                env_of(cur["id"])["vars"] = table.get()
            self.persist()
            self.refresh_hints()
            self.update_env_info()
        table.on_change = store

        def fill(select=None):
            lb.delete(*lb.get_children())
            lb.insert("", "end", iid="__g", text="Globals")
            for e in self.data["envs"]:
                lb.insert("", "end", iid=e["id"], text=e["name"])
            lb.selection_set(select if select and lb.exists(select) else "__g")
            choose()
            self.refresh_env_combo()
        lb.bind("<<TreeviewSelect>>", choose)

        def new():
            n = self.ask_text("New environment", "Name:")
            if n:
                e = {"id": uid(), "name": n, "vars": []}
                self.data["envs"].append(e)
                self.persist()
                fill(e["id"])

        def rename():
            e = env_of(cur["id"])
            n = e and self.ask_text("Rename", "Name:", e["name"])
            if n:
                e["name"] = n
                self.persist()
                fill(e["id"])

        def dup():
            e = env_of(cur["id"])
            if e:
                c = copy.deepcopy(e)
                c["id"], c["name"] = uid(), e["name"] + " copy"
                self.data["envs"].append(c)
                self.persist()
                fill(c["id"])

        def delete():
            e = env_of(cur["id"])
            if e and self.ask_yes(f"Delete environment “{e['name']}”?"):
                self.data["envs"].remove(e)
                if self.data["active_env"] == e["id"]:
                    self.data["active_env"] = ""
                self.persist()
                fill()

        def activate():
            if env_of(cur["id"]):
                self.data["active_env"] = cur["id"]
                self.persist()
                self.refresh_env_combo()
                self.say("Active environment: " + env_of(cur["id"])["name"])
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(8, 0))
        for t, c in (("New", new), ("Rename", rename), ("Copy", dup), ("Delete", delete)):
            ttk.Button(bar, text=t, style="Tool.TButton", command=c).pack(side="left", padx=(0, 4))
        ttk.Button(left, text="Set active", command=activate).pack(fill="x", pady=(8, 0))
        ttk.Button(left, text="Import…", style="Tool.TButton", command=lambda: (self.import_env_file(), fill())).pack(fill="x", pady=(6, 0))
        fill(self.data["active_env"] or None)

    def import_env_file(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                o = json.load(f)
            rows = [[v.get("enabled", True), v.get("key", ""), str(v.get("value", ""))] for v in o["values"]]
            self.data["envs"].append({"id": uid(), "name": o.get("name", "Imported"), "vars": rows})
            self.persist()
            self.refresh_env_combo()
            self.say("Environment imported")
        except Exception as e:
            self.notify(f"Can't import environment:\n{e}", error=True)

    # ---------- tabs
    def panes(self):
        return self.tabs.panes()

    def cur_pane(self):
        return self.tabs.current() or self.new_tab()

    def new_tab(self, state=None, select=True):
        p = RequestPane(self.tabs, self, state)
        self.tabs.add(p, p.method.get(), "")
        self.retitle(p)
        if select:
            self.tabs.select(p)
            p.url.focus_set()
        return p

    def close_tab(self, pane=None):
        pane = pane or self.cur_pane()
        pane.token = None
        self.tabs.forget(pane)
        pane.destroy()
        if not self.tabs.panes():
            self.new_tab()

    def retitle(self, pane):
        url = re.sub(r"^\w+://", "", pane.url_var.get().strip())
        host, _, path = url.partition("/")
        url = ("/" + path) if path else host
        self.tabs.tab(pane, method=pane.method.get(), title=pane.name or url or "New Request")

    def focus_url(self):
        p = self.cur_pane()
        p.url.focus_set()
        p.url.select_range(0, "end")

    def cycle_tab(self, d):
        ps = self.panes()
        if len(ps) > 1:
            self.tabs.select(ps[(self.tabs.current_index() + d) % len(ps)])
        return "break"

    def _tab_menu(self, p, e):
        m = self.ctx
        m.delete(0, "end")
        m.add_command(label="Duplicate", command=lambda: self.new_tab({**p.get_state(), "ref": None}))
        m.add_command(label="Close", command=lambda: self.close_tab(p))
        m.add_command(label="Close others", command=lambda: [self.close_tab(x) for x in self.panes() if x is not p])
        m.tk_popup(e.x_root, e.y_root)

    # ---------- history
    def add_history(self, st):
        h = self.data["history"]
        st = copy.deepcopy(st)
        st["ref"] = None
        if h and h[0]["state"]["method"] == st["method"] and h[0]["state"]["url"] == st["url"]:
            h.pop(0)
        h.insert(0, {"t": time.time(), "state": st})
        del h[100:]
        self.refresh_history()
        self.persist()

    def refresh_history(self):
        self.hist.delete(*self.hist.get_children())
        for i, it in enumerate(self.data["history"]):
            s = it["state"]
            hh = time.strftime("%H:%M", time.localtime(it["t"]))
            self.hist.insert("", "end", iid=str(i), text=f"{SHORT.get(s['method'], s['method'])}  {s['url']}    {hh}", tags=("m_" + s["method"],))
        if not self.data["history"]:
            self.hist.insert("", "end", iid="__e", text="Nothing sent yet", tags=("hint",))

    def _hist_open(self, e):
        s = self.hist.selection()
        if s and s[0].isdigit():
            self.new_tab(copy.deepcopy(self.data["history"][int(s[0])]["state"]))

    def clear_history(self):
        self.data["history"] = []
        self.refresh_history()
        self.persist()

    # ---------- collections
    def _walk(self, item=""):
        for c in self.tree.get_children(item):
            yield c
            yield from self._walk(c)

    def refresh_tree(self):
        flt = self.flt_var.get().strip().lower()
        prev = {i: self.tree.item(i, "open") for i in self._walk()}
        self.tree.delete(*self.tree.get_children())
        match = lambda n: flt in n["name"].lower() or (n["type"] == "folder" and any(match(c) for c in n["children"]))

        def add(parent, nodes):
            for n in nodes:
                if flt and not match(n):
                    continue
                if n["type"] == "folder":
                    self.tree.insert(parent, "end", iid=n["id"], text=n["name"], open=bool(flt) or bool(prev.get(n["id"], True)), tags=("folder",))
                    add(n["id"], n["children"])
                else:
                    m = n["req"].get("method", "GET")
                    self.tree.insert(parent, "end", iid=n["id"], text=f'{SHORT.get(m, m)}  {n["name"]}', tags=("m_" + m,))
        add("", self.data["collections"])
        if not self.data["collections"]:
            self.tree.insert("", "end", iid="__hint", text="No collections yet — press ＋", tags=("hint",))
        self.tag_methods(self.tree)

    def tree_sel(self):
        s = self.tree.selection()
        return s[0] if s else None

    def find_node(self, nid, nodes=None):
        """Return (node, siblings_list) or (None, None)."""
        nodes = self.data["collections"] if nodes is None else nodes
        for n in nodes:
            if n["id"] == nid:
                return n, nodes
            if n["type"] == "folder":
                r = self.find_node(nid, n["children"])
                if r[0]:
                    return r
        return None, None

    def folder_paths(self, nodes=None, prefix=""):
        out = []
        for n in (self.data["collections"] if nodes is None else nodes):
            if n["type"] == "folder":
                p = prefix + n["name"]
                out.append((p, n))
                out += self.folder_paths(n["children"], p + " / ")
        return out

    def new_collection(self):
        n = self.ask_text("New collection", "Name:", "My Collection")
        if n:
            self.data["collections"].append({"id": uid(), "type": "folder", "name": n, "children": []})
            self.refresh_tree()
            self.persist()

    def open_request_node(self, node):
        for p in self.panes():
            if p.ref == node["id"]:
                self.tabs.select(p)
                return
        st = copy.deepcopy(node["req"])
        st["ref"], st["name"] = node["id"], node["name"]
        self.new_tab(st)

    def _tree_open_sel(self):
        node = self.find_node(self.tree_sel())[0] if self.tree_sel() else None
        if node and node["type"] == "request":
            self.open_request_node(node)

    def _tree_open(self, e):
        iid = self.tree.identify_row(e.y)
        node = self.find_node(iid)[0] if iid else None
        if node and node["type"] == "request":
            self.open_request_node(node)

    def _tree_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
        node = self.find_node(iid)[0] if iid else None
        m = self.ctx
        m.delete(0, "end")
        if node is None:
            m.add_command(label="New collection", command=self.new_collection)
            m.add_command(label="Import Postman collection…", command=self.import_collection)
        elif node["type"] == "folder":
            m.add_command(label="New request", command=lambda: self.new_request_in(node))
            m.add_command(label="New folder", command=lambda: self.new_folder_in(node))
            m.add_command(label="Run all…", command=lambda: self.run_folder(node["id"]))
            m.add_separator()
            m.add_command(label="Rename", command=lambda: self.rename_node(node["id"]))
            m.add_command(label="Duplicate", command=lambda: self.duplicate_node(node["id"]))
            m.add_command(label="Move…", command=lambda: self.move_node(node["id"]))
            m.add_command(label="Export…", command=lambda: self.export_node(node["id"]))
            m.add_separator()
            m.add_command(label="Delete", command=lambda: self.delete_node(node["id"]))
        else:
            m.add_command(label="Open", command=lambda: self._tree_open(e))
            m.add_command(label="Rename", command=lambda: self.rename_node(node["id"]))
            m.add_command(label="Duplicate", command=lambda: self.duplicate_node(node["id"]))
            m.add_command(label="Move…", command=lambda: self.move_node(node["id"]))
            m.add_command(label="Delete", command=lambda: self.delete_node(node["id"]))
        m.tk_popup(e.x_root, e.y_root)

    def new_request_in(self, folder):
        n = self.ask_text("New request", "Name:", "New Request")
        if n:
            st = new_state()
            node = {"id": uid(), "type": "request", "name": n, "req": st}
            folder["children"].append(node)
            self.refresh_tree()
            self.persist()
            st = copy.deepcopy(st)
            st["ref"], st["name"] = node["id"], n
            self.new_tab(st)

    def new_folder_in(self, folder):
        n = self.ask_text("New folder", "Name:", "New Folder")
        if n:
            folder["children"].append({"id": uid(), "type": "folder", "name": n, "children": []})
            self.refresh_tree()
            self.persist()

    def rename_node(self, nid):
        node = self.find_node(nid)[0]
        n = node and self.ask_text("Rename", "Name:", node["name"])
        if n:
            node["name"] = n
            for p in self.panes():
                if p.ref == nid:
                    p.name = n
                    self.retitle(p)
            self.refresh_tree()
            self.persist()

    def _clone(self, node):
        c = copy.deepcopy(node)

        def reid(n):
            n["id"] = uid()
            for ch in n.get("children", []):
                reid(ch)
        reid(c)
        return c

    def duplicate_node(self, nid):
        node, sib = self.find_node(nid)
        if node:
            c = self._clone(node)
            c["name"] += " copy"
            sib.insert(sib.index(node) + 1, c)
            self.refresh_tree()
            self.persist()

    def delete_node(self, nid):
        node, sib = self.find_node(nid) if nid else (None, None)
        if node and self.ask_yes(f"Delete “{node['name']}”?"):
            sib.remove(node)
            self.refresh_tree()
            self.persist()

    def move_node(self, nid):
        node, sib = self.find_node(nid)
        if not node:
            return
        paths =[(p, f) for p, f in self.folder_paths() if f is not node and (node["type"] != "folder" or not self._inside(node, f))]
        choice = self.choose_folder("Move to…", [p for p, _ in paths] + ["(top level)"] if node["type"] == "folder" else [p for p, _ in paths])
        if choice is None:
            return
        target = dict(paths).get(choice)
        sib.remove(node)
        (target["children"] if target else self.data["collections"]).append(node)
        self.refresh_tree()
        self.persist()

    def _inside(self, ancestor, f):
        return f is ancestor or any(c["type"] == "folder" and self._inside(c, f) for c in ancestor["children"])

    def choose_folder(self, title, options, default=None):
        win = tk.Toplevel(self)
        win.title(title)
        f = ttk.Frame(win, padding=16)
        f.pack()
        cb = ttk.Combobox(f, values=options, state="readonly", width=44)
        cb.pack(pady=(0, 12))
        self.reg_combo(cb)
        cb.set(default if default in options else (options[0] if options else ""))
        res = [None]

        def ok():
            res[0] = cb.get()
            win.destroy()
        row = ttk.Frame(f)
        row.pack(anchor="e")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=6)
        self.modal(win)
        win.wait_window()
        return res[0]

    def save_pane(self, pane):
        st = pane.get_state()
        node = self.find_node(pane.ref)[0] if pane.ref else None
        if node is None:
            name = self.ask_text("Save request", "Request name:", pane.name or self._default_name(st), ok="Save")
            if not name:
                return
            if not self.data["collections"]:
                self.data["collections"].append({"id": uid(), "type": "folder", "name": "My Collection", "children": []})
            paths = self.folder_paths()
            sel = self.find_node(self.tree_sel())[0] if self.tree_sel() else None
            default = next((p for p, f in paths if f is sel), None)
            choice = self.choose_folder("Save to…", [p for p, _ in paths], default)
            if choice is None:
                return
            node = {"id": uid(), "type": "request", "name": name, "req": st}
            dict(paths)[choice]["children"].append(node)
            pane.ref, pane.name = node["id"], name
        node["req"] = st
        st["ref"], st["name"] = node["id"], node["name"]
        self.retitle(pane)
        self.refresh_tree()
        self.persist()
        self.say(f"Saved “{node['name']}”")

    def _default_name(self, st):
        u = urllib.parse.urlparse(st["url"] if "://" in st["url"] else "http://" + st["url"])
        return (u.path if len(u.path) > 1 else u.netloc)[:50] or "Request"

    # ---------- import / export
    def import_collection(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                o = json.load(f)
            if "item" not in o:
                raise ValueError("This doesn't look like a Postman collection")
            self.data["collections"].append(pm_import(o))
            self.refresh_tree()
            self.persist()
            self.say("Collection imported")
        except Exception as e:
            self.notify(f"Can't import collection:\n{e}", error=True)

    def export_node(self, nid):
        node = self.find_node(nid)[0] if nid else None
        if not node:
            self.notify("Select a collection or folder in the sidebar first.")
            return
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile=node["name"] + ".postman_collection.json")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(pm_export(node), f, indent=2, ensure_ascii=False)
            self.say(f"Exported to {p}")

    def import_curl_dialog(self):
        win = tk.Toplevel(self)
        win.title("Import cURL")
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Paste a cURL command:").pack(anchor="w", pady=(0, 6))
        box, t = self.text_box(f, height=10)
        box.pack(fill="both", expand=True)

        def go():
            try:
                st = parse_curl(t.get("1.0", "end"))
            except ValueError as e:
                self.notify(str(e), error=True)
                return
            win.destroy()
            self.new_tab(norm_state(st))
        row = ttk.Frame(f)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Import", style="Accent.TButton", command=go).pack(side="right", padx=6)
        win.geometry("640x340")
        self.modal(win)
        t.focus_set()

    # ---------- code / cookies / runner / help
    def code_dialog(self, p):
        win = tk.Toplevel(self)
        win.title("Code snippet")
        win.geometry("760x480")
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        cb = ttk.Combobox(top, values=CODE_KINDS, state="readonly", width=22)
        cb.set(CODE_KINDS[0])
        cb.pack(side="left")
        self.reg_combo(cb)
        box, t = self.text_box(f, readonly=True, height=12)
        box.pack(fill="both", expand=True)

        def show(*_):
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.insert("1.0", gen_code(cb.get(), p))
            t.configure(state="disabled")
        cb.bind("<<ComboboxSelected>>", show)

        def copy_():
            self.clipboard_clear()
            self.clipboard_append(gen_code(cb.get(), p))
            self.say("Code copied")
        ttk.Button(top, text="Copy", style="Accent.TButton", command=copy_).pack(side="right")
        show()
        win.transient(self)
        win.configure(bg=self.pal["bg"])
        self.center(win)

    def cookie_dialog(self):
        win = tk.Toplevel(self)
        win.title("Cookies")
        win.geometry("720x380")
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        tv = ttk.Treeview(f, columns=("domain", "name", "value", "path"), show="headings")
        for c, w in (("domain", 180), ("name", 140), ("value", 260), ("path", 80)):
            tv.heading(c, text=c.title(), anchor="w")
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True)

        def fill():
            tv.delete(*tv.get_children())
            for c in self.jar:
                tv.insert("", "end", values=(c.domain, c.name, c.value, c.path))

        def clear():
            self.jar.clear()
            fill()
        ttk.Button(f, text="Delete all cookies", command=clear).pack(anchor="w", pady=(8, 0))
        fill()
        win.transient(self)
        win.configure(bg=self.pal["bg"])
        self.center(win)

    def run_folder(self, nid):
        node = self.find_node(nid)[0] if nid else None
        if not node:
            self.notify("Select a collection or folder in the sidebar first.")
            return

        def collect(n):
            return [n] if n["type"] == "request" else [r for c in n["children"] for r in collect(c)]
        reqs = collect(node)
        win = tk.Toplevel(self)
        win.title(f"Run — {node['name']}")
        win.geometry("780x420")
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        tv = ttk.Treeview(f, columns=("name", "status", "time", "tests"), show="headings")
        for c, t, w in (("name", "Request", 320), ("status", "Status", 80), ("time", "Time", 90), ("tests", "Tests / error", 240)):
            tv.heading(c, text=t, anchor="w")
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True)
        summary = ttk.Label(f, style="Muted.TLabel", text=f"{len(reqs)} request(s)")
        summary.pack(anchor="w", pady=(8, 0))
        stop = {"v": False}
        stats = {"ok": 0, "fail": 0}

        def add_row(row, good):
            tv.insert("", "end", values=row)
            stats["ok" if good else "fail"] += 1
            summary.configure(text=f"{stats['ok']} passed, {stats['fail']} failed of {len(reqs)}")

        def work():
            for n in reqs:
                if stop["v"]:
                    break
                try:
                    res = execute(self._prep(norm_state(n["req"])), self.jar)
                    tr = run_tests(n["req"].get("tests", ""), res, self.set_var)
                    good = all(ok for ok, _ in tr) and res["status"] < 400
                    row = (n["name"], res["status"], f"{res['time'] * 1000:.0f} ms", f"{sum(1 for ok, _ in tr if ok)}/{len(tr)}")
                except Exception as e:
                    good, row = False, (n["name"], "ERR", "", str(getattr(e, "reason", e))[:70])
                self.post(lambda r=row, g=good: add_row(r, g))
        btn = ttk.Button(f, text="Stop", command=lambda: stop.update(v=True))
        btn.pack(anchor="e")
        win.transient(self)
        win.configure(bg=self.pal["bg"])
        self.center(win)
        win.protocol("WM_DELETE_WINDOW", lambda: (stop.update(v=True), win.destroy()))
        self.run_bg(work, lambda r: summary.winfo_exists() and summary.configure(text=summary.cget("text") + "  — done"))

    def help_dialog(self):
        self.notify("Ctrl+Enter   send\n"
                    "Ctrl+S   save to a collection\n"
                    "Ctrl+K   quick open (saved requests + history)\n"
                    "Ctrl+T / Ctrl+W   new / close tab      Ctrl+Tab   next tab\n"
                    "Ctrl+L   focus the URL bar\n"
                    "Ctrl+Shift+E   environments      Ctrl +/-   zoom\n\n"
                    "•  Paste a cURL command into the URL bar to import it.\n"
                    "•  Variables: {{name}}, plus {{$timestamp}} {{$guid}} {{$randomInt}} {{$isoTimestamp}}.\n"
                    "•  Tests tab: 'status == 200'   or   'set authToken = json.token' saves a value to the active environment.\n"
                    "•  Middle-click a tab to close it, right-click for more.\n"
                    "•  Right-click a collection for new / rename / move / run / export.", title="Shortcuts & tips")

    # ---------- session
    def _restore_session(self):
        s = self.data.get("session") or {}
        for st in (s.get("tabs", []) if self.data.get("restore_tabs", True) else []):
            try:
                self.new_tab(norm_state(st), select=False)
            except Exception:
                traceback.print_exc()
        if not self.tabs.tabs():
            self.new_tab()
        else:
            try:
                self.tabs.select_index(int(s.get("active", 0)))
            except (tk.TclError, ValueError):
                pass

    def on_close(self):
        try:
            self.data["session"] = {"tabs": [p.get_state() for p in self.panes()], "active": self.tabs.current_index()}
            self.data["geometry"] = self.geometry()
        except Exception:
            pass
        self.persist()
        self.destroy()


def main():
    try:
        app = App()
    except tk.TclError as e:
        raise SystemExit(f"Can't open a display: {e}")
    app.mainloop()


if __name__ == "__main__":
    main()
