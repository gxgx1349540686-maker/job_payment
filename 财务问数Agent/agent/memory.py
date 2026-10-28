"""Small, owner-isolated working memory. Never stores credentials or financial facts."""
import contextlib
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path


def fresh(owner, fingerprint):
    now=time.time()
    return dict(session_id=uuid.uuid4().hex,user_id=owner,slots={},constraints={},assumptions=[],
                pending_questions=[],confirmed_items={},output_spec={'mode':'summary','details':False,'breakdown':False},
                version=0,last_updated=now,created_at=now,status='draft',recent_messages=[],summary='',
                database_sha256=fingerprint,needs_confirmation=False,choices=[],tool_refs=[])


class SessionStore:
    ttl=86400
    archive_ttl=7*86400

    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with contextlib.closing(self.connect()) as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS sessions(
              user_id TEXT,session_id TEXT,version INTEGER,updated REAL,state TEXT,
              PRIMARY KEY(user_id,session_id));
              CREATE TABLE IF NOT EXISTS turns(user_id TEXT,session_id TEXT,request_id TEXT,
              payload_hash TEXT,status TEXT,created REAL,response TEXT,
              PRIMARY KEY(user_id,session_id,request_id));''');c.commit()

    def connect(self):return sqlite3.connect(self.path,timeout=5)

    def load(self,owner,sid,fingerprint):
        if not sid:return fresh(owner,fingerprint)
        with contextlib.closing(self.connect()) as c:
            r=c.execute('SELECT state,updated FROM sessions WHERE user_id=? AND session_id=?',(owner,sid)).fetchone()
        if not r or time.time()-r[1]>self.ttl:
            raise ValueError('会话不存在或已过期，请新建对话；不会猜测上次条件。')
        return json.loads(r[0])

    def claim(self,state,request_id,payload,expected_version=None):
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        key=(state['user_id'],state['session_id'],request_id)
        with contextlib.closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT payload_hash,status,response FROM turns WHERE user_id=? AND session_id=? AND request_id=?',key).fetchone()
            if old:
                if old[0]!=digest:raise ValueError('同一请求编号不能用于不同内容。')
                if old[1]=='done':return json.loads(old[2])
                raise ValueError('该请求处理中或曾被中断，请核对状态后使用新请求编号；没有自动重试。')
            c.execute("UPDATE turns SET status='interrupted' WHERE user_id=? AND session_id=? AND status='running' AND created<?",(*key[:2],time.time()-120))
            if c.execute("SELECT 1 FROM turns WHERE user_id=? AND session_id=? AND status='running'",key[:2]).fetchone():raise ValueError('上一条消息仍在处理中，请等完成后再补充。')
            current=c.execute('SELECT version FROM sessions WHERE user_id=? AND session_id=?',key[:2]).fetchone()
            if current and current[0]!=state['version']:raise ValueError('会话已更新，请刷新后重试。')
            if expected_version is not None and expected_version!=state['version']:
                raise ValueError('这条消息基于旧版需求，请使用最新对话状态。')
            c.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,NULL)',(*key,digest,'running',time.time()));c.commit()
        return None

    def save(self,state,response,request_id):
        expected=state['version'];state['version']+=1;state['last_updated']=time.time()
        response['state_version']=state['version']
        public={k:state[k] for k in ('session_id','slots','constraints','assumptions','pending_questions','confirmed_items','output_spec','version','status','last_updated')}
        response['requirement_state']=public
        with contextlib.closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT version FROM sessions WHERE user_id=? AND session_id=?',(state['user_id'],state['session_id'])).fetchone()
            if old and old[0]!=expected:raise ValueError('会话版本冲突，没有覆盖其他请求。')
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(user_id,session_id) DO UPDATE SET version=excluded.version,updated=excluded.updated,state=excluded.state',
                      (state['user_id'],state['session_id'],state['version'],state['last_updated'],json.dumps(state,ensure_ascii=False)))
            c.execute('UPDATE turns SET status=?,response=? WHERE user_id=? AND session_id=? AND request_id=?',
                      ('done',json.dumps(response,ensure_ascii=False),state['user_id'],state['session_id'],request_id))
            c.execute('DELETE FROM turns WHERE created<?',(time.time()-self.ttl,))
            c.execute('DELETE FROM sessions WHERE updated<?',(time.time()-self.archive_ttl,));c.commit()

    def fail(self,state,request_id):
        with contextlib.closing(self.connect()) as c:
            c.execute('UPDATE turns SET status=? WHERE user_id=? AND session_id=? AND request_id=?',
                      ('interrupted',state['user_id'],state['session_id'],request_id));c.commit()

    @staticmethod
    def remember(state,question,response):
        # Only dialogue requirements are summarized; tool amounts stay in short-lived receipts/audit.
        state['recent_messages'].extend([{'role':'user','content':question[:600]},
                                         {'role':'assistant','content':response.get('answer','')[:600] if response['status']!='answered' else '已按确认需求完成查询；金额须从当前数据库重新读取。'}])
        if len(state['recent_messages'])>12:
            state['summary']=json.dumps({'slots':state['slots'],'constraints':state['constraints'],
                                         'output_spec':state['output_spec']},ensure_ascii=False)[:2400]
            state['recent_messages']=state['recent_messages'][-12:]
        state['tool_refs']=state.get('tool_refs',[])[-3:]
