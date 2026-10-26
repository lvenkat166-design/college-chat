import os, sqlite3, hashlib, hmac, secrets
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
DB = ROOT / "chat.db"
app = FastAPI(title="Open Chat")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("CHAT_SESSION_SECRET", "demo-secret-change-before-deployment"), same_site="lax", https_only=os.getenv("CHAT_HTTPS_ONLY", "0") == "1")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
sockets = {}

def connect():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c

with connect() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, student_id TEXT UNIQUE COLLATE NOCASE, name TEXT, salt TEXT, pwhash TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS groups_chat(id INTEGER PRIMARY KEY, name TEXT, created_by INTEGER REFERENCES users(id), created TEXT);
    CREATE TABLE IF NOT EXISTS members(group_id INTEGER REFERENCES groups_chat(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id TEXT, sender_id INTEGER REFERENCES users(id), body TEXT, created TEXT);
    CREATE INDEX IF NOT EXISTS ix_messages ON messages(chat_id,id);
    """)

def stamp(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def password_hash(pw, salt=None):
    salt = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    return salt.hex(), hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310000).hex()
def get_user(uid):
    with connect() as c:
        r=c.execute("SELECT id,student_id,name FROM users WHERE id=?",(uid,)).fetchone()
    return dict(r) if r else None
def me(req):
    u=get_user(req.session.get("uid"))
    if not u: raise HTTPException(401,"Please log in.")
    return u
def dm(a,b):
    x,y=sorted((int(a),int(b))); return f"dm:{x}:{y}"
def dm_parts(cid):
    p=cid.split(":")
    try:
        if len(p)==3 and p[0]=="dm": return int(p[1]),int(p[2])
    except ValueError: pass
    return None
def allowed(uid,cid):
    pair=dm_parts(cid)
    if pair: return uid in pair and pair[0]!=pair[1]
    if cid.startswith("group:"):
        try: gid=int(cid.split(":")[1])
        except ValueError: return False
        with connect() as c: return c.execute("SELECT 1 FROM members WHERE group_id=? AND user_id=?",(gid,uid)).fetchone() is not None
    return False
def recipients(cid):
    pair=dm_parts(cid)
    if pair: return list(pair)
    if cid.startswith("group:"):
        try: gid=int(cid.split(":")[1])
        except ValueError: return []
        with connect() as c: return [r[0] for r in c.execute("SELECT user_id FROM members WHERE group_id=?",(gid,)).fetchall()]
    return []
async def send_safe(ws,obj):
    try: await ws.send_json(obj)
    except Exception: pass
async def broadcast(ids,obj):
    for uid in set(ids):
        for ws in list(sockets.get(str(uid),set())): await send_safe(ws,obj)
async def presence():
    with connect() as c: rows=c.execute("SELECT id,student_id FROM users").fetchall()
    online={int(k) for k,v in sockets.items() if v}
    await broadcast([r["id"] for r in rows],{"type":"presence","online_student_ids":[r["student_id"] for r in rows if r["id"] in online]})

@app.get("/")
def home(): return FileResponse(ROOT/"static"/"index.html")
@app.get("/api/health")
def health(): return {"ok":True}
@app.post("/api/register")
async def register(req:Request):
    d=await req.json(); sid=str(d.get("student_id","")).strip(); name=str(d.get("name","")).strip(); pw=str(d.get("password",""))
    if not (3<=len(sid)<=30) or not sid.replace("_","").replace("-","").isalnum(): raise HTTPException(400,"Student ID must be 3–30 letters/numbers, underscore or hyphen.")
    if not 2<=len(name)<=60: raise HTTPException(400,"Name must be 2–60 characters.")
    if len(pw)<8: raise HTTPException(400,"Password must be at least 8 characters.")
    salt,digest=password_hash(pw)
    try:
        with connect() as c:
            c.execute("INSERT INTO users(student_id,name,salt,pwhash,created) VALUES(?,?,?,?,?)",(sid,name,salt,digest,stamp()))
            row=c.execute("SELECT id,student_id,name FROM users WHERE student_id=?",(sid,)).fetchone()
    except sqlite3.IntegrityError: raise HTTPException(409,"That Student ID is already registered.")
    req.session.clear(); req.session["uid"]=row["id"]; return {"user":dict(row)}
@app.post("/api/login")
async def login(req:Request):
    d=await req.json(); sid=str(d.get("student_id","")).strip(); pw=str(d.get("password",""))
    with connect() as c: r=c.execute("SELECT * FROM users WHERE student_id=? COLLATE NOCASE",(sid,)).fetchone()
    if not r: raise HTTPException(401,"Incorrect Student ID or password.")
    _,digest=password_hash(pw,r["salt"])
    if not hmac.compare_digest(digest,r["pwhash"]): raise HTTPException(401,"Incorrect Student ID or password.")
    req.session.clear(); req.session["uid"]=r["id"]; return {"user":{"id":r["id"],"student_id":r["student_id"],"name":r["name"]}}
@app.post("/api/logout")
def logout(req:Request): req.session.clear(); return {"ok":True}
@app.get("/api/me")
def current(req:Request): return {"user":me(req)}
@app.get("/api/users")
def users(req:Request):
    u=me(req)
    with connect() as c: rows=c.execute("SELECT id,student_id,name FROM users WHERE id!=? ORDER BY name",(u["id"],)).fetchall()
    online={int(k) for k,v in sockets.items() if v}
    return {"users":[{**dict(r),"online":r["id"] in online,"chat_id":dm(u["id"],r["id"])} for r in rows]}
@app.get("/api/groups")
def groups(req:Request):
    u=me(req)
    with connect() as c:
        rows=c.execute("SELECT g.id,g.name,(SELECT COUNT(*) FROM members m2 WHERE m2.group_id=g.id) n FROM groups_chat g JOIN members m ON m.group_id=g.id WHERE m.user_id=? ORDER BY g.name",(u["id"],)).fetchall()
    return {"groups":[{"id":r["id"],"name":r["name"],"member_count":r["n"],"chat_id":f"group:{r['id']}"} for r in rows]}
@app.post("/api/groups")
async def create_group(req:Request):
    u=me(req); d=await req.json(); name=str(d.get("name","")).strip(); ids=d.get("member_ids",[])
    if not 2<=len(name)<=60: raise HTTPException(400,"Group name must be 2–60 characters.")
    if not isinstance(ids,list): raise HTTPException(400,"Choose group members.")
    try: ids={int(x) for x in ids}|{u["id"]}
    except (ValueError,TypeError): raise HTTPException(400,"Invalid member selection.")
    with connect() as c:
        marks=",".join("?" for _ in ids)
        found={r["id"] for r in c.execute(f"SELECT id FROM users WHERE id IN ({marks})",tuple(ids)).fetchall()}
        if found!=ids: raise HTTPException(400,"A selected user does not exist.")
        cur=c.execute("INSERT INTO groups_chat(name,created_by,created) VALUES(?,?,?)",(name,u["id"],stamp())); gid=cur.lastrowid
        c.executemany("INSERT INTO members(group_id,user_id) VALUES(?,?)",[(gid,x) for x in ids])
    return {"group":{"id":gid,"name":name,"chat_id":f"group:{gid}","member_count":len(ids)}}
@app.get("/api/history")
def history(req:Request,chat_id:str,limit:int=100):
    u=me(req)
    if not allowed(u["id"],chat_id): raise HTTPException(403,"You are not a member of this chat.")
    with connect() as c: rows=c.execute("SELECT m.id,m.chat_id,m.body,m.created,m.sender_id,u.student_id sender_student_id,u.name sender_name FROM messages m JOIN users u ON u.id=m.sender_id WHERE chat_id=? ORDER BY m.id DESC LIMIT ?",(chat_id,max(1,min(limit,200)))).fetchall()
    out=[]
    for r in reversed(rows):
        x=dict(r); x["mine"]=x["sender_id"]==u["id"]; x["created_at"]=x.pop("created"); out.append(x)
    return {"messages":out}
@app.websocket("/ws")
async def ws_endpoint(ws:WebSocket):
    await ws.accept(); uid=ws.scope.get("session",{}).get("uid")
    if not uid or not get_user(uid): await ws.close(code=4401); return
    key=str(uid); sockets.setdefault(key,set()).add(ws); await ws.send_json({"type":"connected"}); await presence()
    try:
        while True:
            d=await ws.receive_json()
            if not isinstance(d,dict) or d.get("type")!="message": continue
            cid=str(d.get("chat_id","")); body=str(d.get("text","")).strip()
            if not body: continue
            if len(body)>4000: await ws.send_json({"type":"error","message":"Messages must be 4,000 characters or fewer."}); continue
            if not allowed(uid,cid): await ws.send_json({"type":"error","message":"You cannot send messages to this chat."}); continue
            created=stamp()
            with connect() as c:
                cur=c.execute("INSERT INTO messages(chat_id,sender_id,body,created) VALUES(?,?,?,?)",(cid,uid,body,created))
                r=c.execute("SELECT student_id,name FROM users WHERE id=?",(uid,)).fetchone()
            msg={"id":cur.lastrowid,"chat_id":cid,"body":body,"created_at":created,"sender_id":uid,"sender_student_id":r["student_id"],"sender_name":r["name"],"mine":False}
            await broadcast(recipients(cid),{"type":"message","message":msg})
    except WebSocketDisconnect: pass
    except Exception: pass
    finally:
        sockets.get(key,set()).discard(ws)
        if not sockets.get(key): sockets.pop(key,None)
        await presence()
