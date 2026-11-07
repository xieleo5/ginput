"""Opt-in, local input history and conservative personal vocabulary learning."""
from collections import OrderedDict
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time


def paths():
    home=Path.home()
    config=Path(os.environ.get('XDG_CONFIG_HOME',home/'.config'))/'ginput/learning.json'
    state=Path(os.environ.get('XDG_STATE_HOME',home/'.local/state'))/'ginput'
    data=Path(os.environ.get('XDG_DATA_HOME',home/'.local/share'))/'ginput'
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR',home/'.cache'))/'ginput'
    rime=Path(os.environ.get('XDG_CONFIG_HOME',home/'.config'))/'fcitx/rime'
    return config,state,data,runtime,rime


def private_write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name+'.')
    try:
        with os.fdopen(fd,'w') as stream: stream.write(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def configure(enabled, retention_days=7):
    config,_,_,runtime,_=paths()
    if retention_days < 0: raise ValueError('保留天数不能为负数；0 表示长期保留。')
    private_write(config,json.dumps({'enabled':bool(enabled),'retention_days':retention_days,'reset':time.time_ns()},indent=2)+'\n')
    if not enabled: (runtime/'context.tsv').unlink(missing_ok=True)


class LearningStore:
    def __init__(self, clock=time.time):
        self.clock=clock
        self.config,self.state,self.data,self.runtime,self.rime=paths()
        self.db=None
        self.windows=OrderedDict()
        self.last_export=0
        self.last_prune=0
        self.lexicon_stamp=None
        self.by_text={}
        self.char_codes={}
        self.config_stamp=None
        self.enabled=False
        self.retention_days=7
        self.reload_config()

    def reload_config(self):
        stamp=self.config.stat().st_mtime_ns if self.config.exists() else 0
        if stamp==self.config_stamp:return
        self.config_stamp=stamp
        try: config=json.loads(self.config.read_text())
        except (OSError,ValueError):config={}
        if not isinstance(config,dict):config={}
        self.enabled=config.get('enabled') is True
        try:self.retention_days=max(0,int(config.get('retention_days',7)))
        except (TypeError,ValueError):self.retention_days=7
        self.windows.clear()
        (self.runtime/'context.tsv').unlink(missing_ok=True)
        if not self.enabled:
            self.windows.clear()
            if self.db:self.db.close();self.db=None
            (self.runtime/'context.tsv').unlink(missing_ok=True)

    def connect(self):
        if self.db:return
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.state/'learning.sqlite3'
        fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(path,0o600)
        self.db=sqlite3.connect(path,timeout=.2)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL,window TEXT,kind TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS terms(text TEXT,code TEXT,accepted INTEGER DEFAULT 0,deleted INTEGER DEFAULT 0,last_used REAL,PRIMARY KEY(text,code));
          CREATE TABLE IF NOT EXISTS pairs(prefix TEXT,text TEXT,weight INTEGER DEFAULT 0,PRIMARY KEY(prefix,text));
        ''')
        self.db.commit()

    def load_lexicon(self):
        path=self.rime/'ginput.lexicon.tsv'
        stamp=path.stat().st_mtime_ns if path.exists() else 0
        if stamp==self.lexicon_stamp:return
        self.lexicon_stamp=stamp;self.by_text={};self.char_codes={}
        if not stamp:return
        with path.open() as stream:
            for line in stream:
                parts=line.rstrip('\n').split('\t')
                if len(parts)!=3:continue
                text,code,weight=parts
                self.by_text.setdefault(text,[]).append((code,int(weight)))
                if len(text)==1:self.char_codes.setdefault(text,set()).add(code)

    @staticmethod
    def spellings(code):
        options=[code]
        if code.startswith('yu'):options.append('v'+code[2:])
        if re.fullmatch('[nl]ue',code):options.append(code[0]+'ve')
        full=list(options)
        for spelling in full:
            options.append(spelling[0])
            if spelling[:2] in ('zh','ch','sh'):options.append(spelling[:2])
        return tuple(dict.fromkeys(options))

    def pronunciation(self,text,raw):
        self.load_lexicon()
        if not text or not all('\u3400'<=c<='\u9fff' for c in text):return None
        raw=re.sub("[ '\u00fc]",lambda m:'v' if m[0]=='ü' else '',raw.lower())
        known=self.by_text.get(text,[])
        if known:
            exact=[entry for entry in known if entry[0].replace(' ','')==raw]
            return max(exact or known,key=lambda e:e[1])[0]
        if not raw or len(text)>16:return None
        @lru_cache(None)
        def match(index,pos):
            if index==len(text):return () if pos==len(raw) else None
            for code in sorted(self.char_codes.get(text[index],()),key=lambda s:(-len(s),s)):
                for spelling in self.spellings(code):
                    if raw.startswith(spelling,pos):
                        rest=match(index+1,pos+len(spelling))
                        if rest is not None:return (code,)+rest
            return None
        result=match(0,0)
        return ' '.join(result) if result else None

    def window(self,key):
        now=self.clock()
        for old in list(self.windows):
            if now-self.windows[old]['time']>1800:del self.windows[old]
        if key not in self.windows:self.windows[key]={'text':'','commits':[],'time':now,'select_all':False,'separator':''}
        self.windows.move_to_end(key)
        while len(self.windows)>64:self.windows.popitem(last=False)
        self.windows[key]['time']=now
        return self.windows[key]

    def event(self,event):
        self.reload_config()
        if not self.enabled or event.get('sensitive'):return
        self.connect()
        now=self.clock();key=str(event.get('window',''))
        if not key:return
        kind=event.get('event','')
        if kind not in ('key','commit','focus'):return
        window=self.window(key)
        # Store only data delivered by the input method, never surrounding text.
        self.db.execute('INSERT INTO events(at,window,kind,payload) VALUES(?,?,?,?)',
                        (now,key,kind,json.dumps(event,ensure_ascii=False,separators=(',',':'))))
        if kind=='commit':
            text=event.get('text','')[:512]
            raw=event.get('preedit','')[:512]
            code=self.pronunciation(text,raw)
            previous=window['commits'][-1] if window['commits'] else None
            prefix=previous['text'] if previous and not previous['deleted'] and previous['remaining']==len(previous['text']) else ''
            if window['select_all']:
                self.retract(window,len(window['text']),now)
                window['text']='';window['select_all']=False;prefix=''
            window['text']=(window['text']+text)[-512:]
            if code:
                self.db.execute('INSERT INTO terms VALUES(?,?,1,0,?) ON CONFLICT(text,code) DO UPDATE SET accepted=accepted+1,last_used=excluded.last_used',(text,code,now))
                if prefix:self.db.execute('INSERT INTO pairs VALUES(?,?,1) ON CONFLICT(prefix,text) DO UPDATE SET weight=weight+1',(prefix,text))
            window['commits'].append({'text':text,'code':code,'remaining':len(text),'deleted':False,'prefix':prefix,'at':now})
            if window['separator']:
                separator=window['separator'];window['separator']=''
                window['text']+=separator
                window['commits'].append({'text':separator,'code':None,'remaining':len(separator),'deleted':False,'prefix':'','at':now})
            window['commits']=window['commits'][-64:]
            # Retain only complete chunks that fit the same 512-character window.
            retained=sum(record['remaining'] for record in window['commits'])
            while len(window['commits'])>1 and retained>512:
                retained-=window['commits'].pop(0)['remaining']
            window['text']=window['text'][-512:]
        elif kind=='key':
            keycode=int(event.get('key',0));state=int(event.get('state',0));composing=event.get('composing',False)
            if state & 4 and keycode in (ord('a'),ord('A')):
                window['select_all']=True
            elif not composing and keycode in (0xff08,0xffff):
                selected=window['select_all']
                window['select_all']=False
                if selected or (keycode==0xff08 and not(state & 4)):
                    amount=len(window['text']) if selected else 1
                    self.retract(window,amount,now)
                    window['text']=window['text'][:-amount] if amount else window['text']
                else:
                    # Application-owned forward deletion/word boundaries are uncertain.
                    window['text']='';window['commits']=[]
            elif not composing and keycode in (0xff51,0xff52,0xff53,0xff54,0xff50,0xff57):
                # The application owns caret/selection behavior; invalidate uncertain context.
                window['text']='';window['commits']=[];window['select_all']=False
            elif state & (4|8|64) or (not composing and keycode==0xff0d):
                window['text']='';window['commits']=[];window['select_all']=False
            elif 33<=keycode<=126 and chr(keycode) not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
                # ASCII punctuation is forwarded by the engine. Preserve a boundary
                # so deleting it cannot falsely reject the preceding Chinese word.
                char=chr(keycode)
                if composing:window['separator']=char
                else:
                    window['text']=(window['text']+char)[-512:]
                    window['commits'].append({'text':char,'code':None,'remaining':1,'deleted':False,'prefix':'','at':now})
                    window['commits']=window['commits'][-64:]
        self.prune(now)
        self.db.commit()
        self.publish_context(key,window)
        if kind=='commit' or (kind=='key' and event.get('key') in (0xff08,0xffff)):self.export()

    def retract(self,window,amount,now):
        for record in reversed(window['commits']):
            if amount<=0:break
            removed=min(amount,record['remaining']);record['remaining']-=removed;amount-=removed
            if removed and not record['deleted'] and now-record['at']<=10:
                record['deleted']=True
                if record['code']:
                    self.db.execute('UPDATE terms SET deleted=deleted+1 WHERE text=? AND code=?',(record['text'],record['code']))
                    if record['prefix']:self.db.execute('UPDATE pairs SET weight=MAX(0,weight-1) WHERE prefix=? AND text=?',(record['prefix'],record['text']))

    def publish_context(self,key,window):
        scores={}
        for record in window['commits']:
            if record['code'] and not record['deleted'] and record['remaining']==len(record['text']):
                age=max(0,self.clock()-record['at'])
                scores[record['text']]=max(scores.get(record['text'],0),.6*math.exp(-age/600))
            elif record['code'] and record['deleted']:
                scores[record['text']]=-.6
        if window['commits'] and window['commits'][-1]['remaining'] and not window['commits'][-1]['deleted']:
            prefix=window['commits'][-1]['text']
            for text,weight in self.db.execute('SELECT text,weight FROM pairs WHERE prefix=? AND weight>0 ORDER BY weight DESC LIMIT 64',(prefix,)):
                scores[text]=scores.get(text,0)+min(.4,.08*weight)
        rows=[f'{key}\t{int(self.clock())}']+[f'{text}\t{min(score,1):.4f}' for text,score in sorted(scores.items(),key=lambda x:-x[1])[:64] if '\t' not in text and '\n' not in text]
        private_write(self.runtime/'context.tsv','\n'.join(rows)+'\n')

    def prune(self,now=None):
        now=self.clock() if now is None else now
        if now-self.last_prune<60:return
        self.last_prune=now
        if self.retention_days:self.db.execute('DELETE FROM events WHERE at < ?',(now-self.retention_days*86400,))

    def export(self):
        rows=['# Automatically learned; edit words.tsv for permanent manual entries.']
        for text,code,accepted,deleted in self.db.execute('SELECT text,code,accepted,deleted FROM terms ORDER BY text,code'):
            confidence=accepted-deleted
            if confidence>0:rows.append(f'{text}\t{code}\t{min(50000,1000+confidence*2000)}')
        private_write(self.data/'learned.tsv','\n'.join(rows)+'\n')

    def status(self):
        self.reload_config()
        result={'enabled':self.enabled,'retention_days':self.retention_days,'history':str(self.state/'learning.sqlite3'),'learned_words':str(self.data/'learned.tsv')}
        if self.enabled:
            self.connect();self.prune();self.db.commit()
            result['events']=self.db.execute('SELECT count(*) FROM events').fetchone()[0]
            result['terms']=self.db.execute('SELECT count(*) FROM terms WHERE accepted>deleted').fetchone()[0]
        return result

    def clear(self,include_learned=False):
        self.connect()
        self.db.execute('DELETE FROM events')
        if include_learned:
            self.db.execute('DELETE FROM terms');self.db.execute('DELETE FROM pairs')
        self.db.commit()
        self.db.execute('VACUUM')
        if include_learned:self.export()
        configure(self.enabled,self.retention_days)
        self.reload_config()

    def close(self):
        if self.db:self.db.close();self.db=None
