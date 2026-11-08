"""Explicit local issue reports. Unmarked input is kept only in bounded memory."""
from collections import OrderedDict
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class FeedbackStore:
    def __init__(self, clock=time.time):
        self.clock=clock
        self.path=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))/'ginput/feedback.sqlite3'
        self.last=OrderedDict()
        self.db=None

    def observe(self,event):
        if event.get('sensitive') or event.get('event')!='commit':return
        if not event.get('window') or not event.get('preedit','').strip() or not event.get('text'):return
        snapshot=json.loads(json.dumps(event,ensure_ascii=False))
        snapshot.update(id=uuid.uuid4().hex[:12],at=self.clock())
        key=str(event['window'])
        self.last[key]=snapshot;self.last.move_to_end(key)
        while len(self.last)>64:self.last.popitem(last=False)

    def connect(self):
        if self.db:return
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(self.path,0o600)
        self.db=sqlite3.connect(self.path,timeout=.2)
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('''CREATE TABLE IF NOT EXISTS feedback(
            id TEXT PRIMARY KEY,at REAL,window TEXT,snapshot TEXT,
            status TEXT DEFAULT 'open',expected TEXT DEFAULT '',note TEXT DEFAULT '')''')
        self.db.commit()

    def mark(self,window):
        snapshot=self.last.get(str(window))
        if not snapshot or self.clock()-snapshot['at']>1800:
            return {'message':'当前窗口没有近期可反馈的上屏输入','saved':False}
        self.connect()
        cursor=self.db.execute('INSERT OR IGNORE INTO feedback(id,at,window,snapshot) VALUES(?,?,?,?)',
            (snapshot['id'],snapshot['at'],str(window),json.dumps(snapshot,ensure_ascii=False)))
        self.db.commit()
        message='已记录反馈' if cursor.rowcount else '这笔输入已记录反馈'
        return {'id':snapshot['id'],'saved':True,'message':message+' #'+snapshot['id']}

    def reports(self,status='open',limit=100):
        if not self.path.exists():return []
        self.connect()
        query='SELECT id,at,snapshot,status,expected,note FROM feedback'
        params=[]
        if status!='all':query+=' WHERE status=?';params.append(status)
        query+=' ORDER BY at DESC LIMIT ?';params.append(max(1,min(limit,1000)))
        return [dict(id=row[0],at=row[1],input=json.loads(row[2]),status=row[3],expected=row[4],note=row[5])
                for row in self.db.execute(query,params)]

    def update(self,identifier,*,expected=None,note=None,status=None):
        self.connect()
        fields=[];values=[]
        for name,value in [('expected',expected),('note',note),('status',status)]:
            if value is not None:fields.append(name+'=?');values.append(value)
        if not fields:raise ValueError('请填写期望结果或说明。')
        values.append(identifier)
        cursor=self.db.execute('UPDATE feedback SET '+','.join(fields)+' WHERE id=?',values)
        if not cursor.rowcount:raise ValueError('找不到该反馈编号。')
        self.db.commit()

    def clear(self):
        if self.path.exists():
            self.connect();self.db.execute('DELETE FROM feedback');self.db.commit();self.db.execute('VACUUM')
        self.last.clear()

    def close(self):
        if self.db:self.db.close();self.db=None
