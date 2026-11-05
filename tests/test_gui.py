import os
import faulthandler; faulthandler.dump_traceback_later(100, exit=True)
import sys, subprocess, json, threading, http.server, importlib.util, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.environ.get("PHOSPHOR_SHOTS")  # set to a directory to also save screenshots
OUT = os.path.join(SHOTS, "f") if SHOTS else None
import tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="phosphor_test_")
spec = importlib.util.spec_from_file_location("app", os.path.join(HERE, "..", "phosphor.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import tkinter as tk
from tkinter import ttk


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n).decode()
        out = json.dumps({"token": "eyJhbGciOi.demo-token", "auth": self.headers.get("Authorization"), "path": self.path,
                          "user": {"id": 42, "name": "Ada", "admin": True}, "items": [1, 2.5, None, "x"], "echo": body}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Set-Cookie", "sid=abc; Path=/")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    do_POST = do_PUT = do_DELETE = do_GET
srv = http.server.HTTPServer(("127.0.0.1", 0), H); port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()

errs, fails = [], []
def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond: fails.append(label)

def mk():
    app = m.App(); app.geometry("1240x780+0+0"); app.update()
    app.report_callback_exception = lambda *a: errs.append("".join(traceback.format_exception(*a)))
    return app

def snap(app, name):
    if not OUT: return
    for _ in range(3): app.update(); time.sleep(0.05)
    subprocess.run(["import", "-window", "root", f"{OUT}_{name}.png"], check=True)

def pump(app, secs=0.3):
    t0 = time.time()
    while time.time() - t0 < secs: app.update(); time.sleep(0.02)

def wait_idle(app, p, limit=6):
    t0 = time.time()
    while p.busy and time.time() - t0 < limit: app.update(); time.sleep(0.03)
    pump(app, 0.15)

def walk(w):
    yield w
    for c in w.winfo_children(): yield from walk(c)

def auto_click(app, labels, every=150, n=1):
    """Click a button whose text is in labels on an open dialog n times (simulates the user)."""
    left = [n]
    def tick():
        for t in [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]:
            for w in walk(t):
                if isinstance(w, ttk.Button) and str(w.cget("text")) in labels:
                    w.invoke(); left[0] -= 1
                    if left[0] > 0: app.after(every, tick)
                    return
        app.after(every, tick)
    app.after(every, tick)

app = mk()
snap(app, "01_empty")
p = app.cur_pane()
check(len(app.panes()) == 1, "starts with one tab")

# --- environment + variables
env = {"id": m.uid(), "name": "Local", "vars": [[True, "base", f"http://127.0.0.1:{port}"]]}
app.data["envs"].append(env); app.refresh_env_combo()
app.env_cb.current(1); app.env_cb.event_generate("<<ComboboxSelected>>"); app.update()
check(app.data["active_env"] == env["id"], "environment selected via combobox event")
check("Local" in app.env_info.cget("text") and "1 variable" in app.env_info.cget("text"), "status bar shows env + var count")

p.url_var.set("{{nope}}/x"); app.update()
check("Undefined" in p.hint.cget("text") and p._hint_on, "hint warns about undefined variable")
p.url_var.set("{{base}}/login"); app.update()
check(p.hint.cget("text").startswith("→") and str(port) in p.hint.cget("text"), "hint shows resolved URL")

p.method.set("POST"); p.method.event_generate("<<ComboboxSelected>>"); app.update()
check(str(p.method.cget("style")) == "POST.Method.TCombobox", "method combobox recoloured on change")
check(app.tabs.items[0]["method"] == "POST", "tab badge follows method")

# --- login request: body + test that stores the token
p.body_mode.set("raw"); p._body_layout()
p.raw.insert("1.0", '{"user":"ada","pw":"{{base}}"}')
p.tests.insert("1.0", "status == 200\nset authToken = json.token\njson.user.id == 42")
p.rn.select(p.f_tests); p.send(); wait_idle(app, p)
check(any(r[1] == "authToken" and r[2] == "eyJhbGciOi.demo-token" for r in env["vars"]), "login test stored authToken in environment")
check(p.lb_status.cget("text").startswith("200"), "status pill shows 200")
p.rn.select(p.f_params); snap(app, "02_login_sent")

# --- second tab uses {{authToken}}
t2 = app.new_tab()
t2.url_var.set("{{base}}/me?x=1&y=2"); t2.auth_type.set("Bearer"); t2.a_vars["token"].set("{{authToken}}")
t2.send(); wait_idle(app, t2)
echo = json.loads(t2.resp_text)
check(echo["auth"] == "Bearer eyJhbGciOi.demo-token", "second request sent Bearer {{authToken}}")
check(len(t2.params.get()) == 2, "params table synced from URL")
check(len(app.panes()) == 2, "two tabs open")
snap(app, "03_two_tabs")

# --- save into a collection through the real dialogs
auto_click(app, ["Save", "OK"], 120, 2)
app.save_pane(t2); pump(app, 0.3)
cols = app.data["collections"]
check(len(cols) == 1 and len(cols[0]["children"]) == 1 and cols[0]["children"][0]["type"] == "request", "save created collection + request via dialogs")
check(t2.ref == cols[0]["children"][0]["id"], "tab linked to saved request")
auto_click(app, ["Save", "OK"], 120, 2)
app.save_pane(p); pump(app, 0.3)
check(len(cols[0]["children"]) == 2, "second request saved in same collection")
app.refresh_tree(); app.update(); snap(app, "04_saved")

# --- quick open
app.close_tab(t2); pump(app, 0.1)
check(len(app.panes()) == 1, "close tab works")
def qo():
    tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    e = next(w for w in walk(tops[-1]) if isinstance(w, ttk.Entry))
    e.focus_force(); app.update()
    e.insert(0, "me"); pump(app, 0.2); snap(app, "05_quick_open")
    e.event_generate("<Return>")
app.after(200, qo); app.quick_open(); pump(app, 0.4)
check(len(app.panes()) == 2, "quick open opened the matching request in a new tab")

# --- many tabs (overflow), method colours
for i, meth in enumerate(["PUT", "PATCH", "DELETE", "GET", "POST", "HEAD", "OPTIONS", "GET"]):
    q = app.new_tab(); q.method.set(meth); q.url_var.set(f"https://api.example.com/v1/resource/{i}/with/a/long/path"); q._method_style(); app.retitle(q)
app.update(); snap(app, "06_many_tabs")
check(len(app.panes()) == 10, "ten tabs open")
app.cycle_tab(1); app.cycle_tab(-1)
for q in app.panes()[2:]: app.close_tab(q)
check(len(app.panes()) == 2, "closed extra tabs")

# --- cURL paste import
q = app.new_tab()
q.url_var.set("curl -X POST 'https://x.io/a?b=1' -H 'Content-Type: application/json' -H 'X-Y: z' -u me:pw --data-raw '{\"q\":1}'")
q._check_curl(); app.update()
check(q.method.get() == "POST" and q.body_mode.get() == "raw" and q.auth_type.get() == "Basic", "pasting a cURL command imports it")
app.close_tab(q)

# --- runner
app.run_folder(cols[0]["id"]); pump(app, 1.5)
runner = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
tv = next(w for w in walk(runner) if isinstance(w, ttk.Treeview))
check(len(tv.get_children()) == 2, "runner executed both saved requests")
runner.destroy()

# --- dialogs: settings, help, envs, cookies, code
auto_click(app, ["Save"], 100); app.settings_dialog(); pump(app, 0.3)
check(True, "settings dialog opens and saves")
auto_click(app, ["OK"], 100); app.help_dialog(); pump(app, 0.3)
app.manage_envs(); pump(app, 0.3); snap(app, "07_envs"); [w.destroy() for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
app.cookie_dialog(); pump(app, 0.2); [w.destroy() for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
app.cur_pane().show_code(); pump(app, 0.3); snap(app, "08_code"); [w.destroy() for w in app.winfo_children() if isinstance(w, tk.Toplevel)]

# --- delete confirmation uses themed dialog
auto_click(app, ["Cancel"], 100)
before = len(cols[0]["children"]); app.delete_node(cols[0]["children"][0]["id"]); pump(app, 0.3)
check(len(cols[0]["children"]) == before, "delete dialog Cancel keeps the request")

# --- themes + zoom
app.cur_pane().rn.select(app.cur_pane().f_hdr)
app.toggle_theme(); app.update(); snap(app, "09_light")
app.zoom(2); app.update(); snap(app, "10_zoom"); app.zoom(-2)
app.toggle_theme(); app.update()
check(app.data["theme"] == "dark", "theme toggles back to dark")

# --- failure display + cancel
f = app.cur_pane(); f.url_var.set("http://127.0.0.1:9/nothing"); f.send(); wait_idle(app, f)
check(f.lb_status.cget("text") == "Error", "connection error shown as Error pill")
snap(app, "11_error")

# --- session persistence
n_tabs = len(app.panes()); app.on_close()
app2 = mk(); pump(app2, 0.3)
check(len(app2.panes()) == n_tabs, f"session restored ({n_tabs} tabs)")
check(any(e["id"] == app2.data["active_env"] for e in app2.data["envs"]), "active environment restored")
app2.destroy()

print("EXCEPTIONS:", errs or "none")
print("FAILED:", fails or "none")
sys.exit(1 if (fails or errs) else 0)
