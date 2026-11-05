import sys, json, threading, http.server, http.cookiejar, tempfile, os
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
import phosphor as m

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _go(self):
        n=int(self.headers.get("Content-Length") or 0); body=self.rfile.read(n)
        if self.path.startswith("/redir"):
            self.send_response(302); self.send_header("Location","/json"); self.end_headers(); return
        out=json.dumps({"method":self.command,"path":self.path,"ct":self.headers.get("Content-Type"),
                        "auth":self.headers.get("Authorization"),"ua":self.headers.get("User-Agent"),
                        "cookie":self.headers.get("Cookie"),"body":body.decode("latin1"),"data":{"id":42,"items":[{"name":"x"}]}}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Set-Cookie","sid=abc123; Path=/; HttpOnly"); self.send_header("Content-Length",str(len(out))); self.end_headers()
        if self.command!="HEAD": self.wfile.write(out)
    do_GET=do_POST=do_PUT=do_DELETE=_go
srv=http.server.HTTPServer(("127.0.0.1",0),H); port=srv.server_port
threading.Thread(target=srv.serve_forever,daemon=True).start()
B=f"http://127.0.0.1:{port}"
jar=http.cookiejar.CookieJar()
def run(st, vars_=None):
    st=m.norm_state(st); p=m.prepare(st, vars_ or {}); return p, m.execute(p, jar)

# 1 GET + vars + bearer + params in URL
st=m.new_state(); st.update(method="GET", url="{{base}}/a?x=1&y={{v}}"); st["auth"].update(type="Bearer", token="{{tok}}")
p,r=run(st,{"base":B,"v":"two words","tok":"T0K"}); j=json.loads(r["text"])
assert r["status"]==200 and j["path"]=="/a?x=1&y=two%20words", j["path"]
assert j["auth"]=="Bearer T0K" and j["ua"]=="Phosphor/1.0"; assert r["kind"]=="json" and r["pretty"].startswith("{\n")
# cookie jar round trip
p,r=run(st,{"base":B,"v":"1","tok":"t"}); assert json.loads(r["text"])["cookie"]=="sid=abc123"
assert m.parse_set_cookie(r["cookies"][0])[0][0]=="sid"
# 2 POST raw json
st=m.new_state(); st.update(method="POST", url=B+"/p"); st["body"].update(mode="raw", raw='{"a":"{{v}}"}', raw_type="JSON")
p,r=run(st,{"v":"Z"}); j=json.loads(r["text"]); assert j["ct"]=="application/json" and j["body"]=='{"a":"Z"}'
# 3 urlencoded + basic auth
st=m.new_state(); st.update(method="POST", url=B+"/f"); st["body"].update(mode="urlenc", urlenc=[[True,"k","v 1"],[False,"off","x"]]); st["auth"].update(type="Basic",user="u",**{"pass":"p"})
p,r=run(st); j=json.loads(r["text"]); assert j["body"]=="k=v+1" and j["auth"]=="Basic dTpw", j
# 4 multipart with file
UP=os.path.join(tempfile.mkdtemp(),"up.txt"); open(UP,"w").write("hello")
st=m.new_state(); st.update(method="POST", url=B+"/m"); st["body"].update(mode="form-data", form=[[True,"a","1"],[True,"f","@"+UP]])
p,r=run(st); j=json.loads(r["text"]); assert j["ct"].startswith("multipart/form-data; boundary=") and 'filename="up.txt"' in j["body"] and "hello" in j["body"]
# 5 API key in query, redirect on/off, missing file error
st=m.new_state(); st.update(method="GET", url=B+"/q"); st["auth"].update(type="API Key",key="k",value="v",**{"in":"Query"})
p,r=run(st); assert json.loads(r["text"])["path"]=="/q?k=v"
st=m.new_state(); st.update(url=B+"/redir"); p,r=run(st); assert r["status"]==200
st["opts"]["follow"]=False; p,r=run(st); assert r["status"]==302, r["status"]
st=m.new_state(); st.update(method="POST", url=B); st["body"].update(mode="binary", file="/nope"); 
try: m.prepare(st,{}); assert 0
except ValueError: pass
try: m.prepare(m.new_state(),{}); assert 0
except ValueError: pass
# 6 tests language
st=m.new_state(); st.update(url=B+"/t"); p,r=run(st)
store={}
res=m.run_tests('status == 200\ntime < 5000\nheader Content-Type contains json\njson.data.id == 42\njson.data.items[0].name == "x"\n'
  'json.nope exists\nbody contains path\nstatus == 404\nset tok = json.data.id\nset bad = json.zzz\nfoo bar', r, store.__setitem__)
oks=[o for o,_ in res]; assert oks==[True,True,True,True,True,False,True,False,True,False,False], oks; assert store=={"tok":"42"}
# 7 curl import + codegen round trip
c=m.parse_curl("""curl -X POST 'http://x.io/a?b=1' \\
  -H 'Content-Type: application/json' -H "X-Y: z" -u me:pw --data-raw '{"q":1}' -k""")
assert c["method"]=="POST" and c["body"]["mode"]=="raw" and c["body"]["raw_type"]=="JSON" and c["auth"]["type"]=="Basic" and c["opts"]["verify"] is False
c2=m.parse_curl("curl https://a.b/c -d 'k=v&z=1'"); assert c2["method"]=="POST" and c2["body"]["mode"]=="urlenc" and c2["body"]["urlenc"][1]==["True" and True,"z","1"]
c3=m.parse_curl("curl -F a=1 -F f=@/tmp/x.png;type=image/png https://h/u"); assert c3["body"]["form"][1][2]=="@/tmp/x.png"
for kind in m.CODE_KINDS:
    out=m.gen_code(kind, m.prepare(m.norm_state(c),{})); assert "x.io" in out, kind
    compile(out, "snippet","exec") if kind.startswith("Python") else None
print(m.gen_code("cURL", m.prepare(m.norm_state(c),{})))
# 8 postman export -> import round trip
node={"id":"1","type":"folder","name":"Col","children":[{"id":"2","type":"request","name":"R1","req":m.norm_state(c)},
      {"id":"3","type":"folder","name":"Sub","children":[{"id":"4","type":"request","name":"R2","req":st}]}]}
pm=m.pm_export(node); back=m.pm_import(json.loads(json.dumps(pm)))
r1=back["children"][0]["req"]; assert r1["url"]==c["url"] and r1["body"]["raw"]=='{"q":1}' and r1["auth"]["user"]=="me" and r1["method"]=="POST", r1
assert back["children"][1]["children"][0]["name"]=="R2"
# 9 dynamic vars + norm_state tolerates old data
assert len(m.subst("{{$guid}}",{}))==36 and m.subst("{{a}}{{zz}}",{"a":"1"})=="1{{zz}}"
assert m.norm_state({"method":"PUT"})["opts"]["verify"] is True
# 10 new: unresolved vars + proxy
p0=m.prepare(m.norm_state({"url":"http://a/{{zz}}?q={{ok}}"}),{"ok":"1"}); assert p0["unresolved"]==["zz"], p0["unresolved"]
st=m.new_state(); st["url"]="http://example.invalid/via-proxy"; p1=m.prepare(st,{}); p1["proxy"]=B
r=m.execute(p1, jar); assert json.loads(r["text"])["path"]=="http://example.invalid/via-proxy", r["text"][:80]
print("ALL LOGIC TESTS PASSED")
