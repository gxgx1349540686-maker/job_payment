"""持久化SQLite向量库：本地字符TF-IDF稀疏向量，余弦检索；无需额外Embedding API。"""
import collections
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path

RULE_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "rules.json"


def tokens(text):
    text = re.sub(r"\s+", "", text.lower())
    return collections.Counter(text[i:i+n] for n in (1, 2, 3) for i in range(len(text)-n+1))


class RuleStore:
    def __init__(self, path, rules_file=RULE_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.documents = json.loads(Path(rules_file).read_text(encoding="utf-8"))
        digest = hashlib.sha256(Path(rules_file).read_bytes()).hexdigest()
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, document TEXT, vector TEXT)")
            old = c.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()
            if not old or old[0] != digest:
                counts = [tokens(d["title"] + " " + d["text"]) for d in self.documents]
                df = collections.Counter(t for counter in counts for t in counter)
                idf = {t: math.log((len(counts)+1)/(freq+1))+1 for t, freq in df.items()}
                c.execute("DELETE FROM documents")
                for d, counter in zip(self.documents, counts):
                    vector = self.vector(counter, idf)
                    c.execute("INSERT INTO documents VALUES (?,?,?)", (d["id"], json.dumps(d, ensure_ascii=False), json.dumps(vector)))
                c.execute("INSERT OR REPLACE INTO meta VALUES ('idf',?)", (json.dumps(idf),))
                c.execute("INSERT OR REPLACE INTO meta VALUES ('source_sha256',?)", (digest,))
            self.idf = json.loads(c.execute("SELECT value FROM meta WHERE key='idf'").fetchone()[0])
            self.index = [(json.loads(d), json.loads(v)) for d, v in c.execute("SELECT document,vector FROM documents")]
        self.digest = digest

    @staticmethod
    def vector(counter, idf):
        v = {t: (1+math.log(n))*idf[t] for t, n in counter.items() if t in idf}
        length = math.sqrt(sum(n*n for n in v.values())) or 1
        return {t:n/length for t,n in v.items()}

    def search(self, question, intent="", limit=6):
        q = self.vector(tokens(question), self.idf)
        ranked = sorted(((sum(q.get(t,0)*v for t,v in vec.items()), d) for d,vec in self.index), key=lambda x:x[0], reverse=True)
        mandatory = [d for d,_ in self.index if d.get("mandatory") or intent in d.get("intents", [])]
        found = {d["id"]: dict(d, score=None) for d in mandatory}
        for score,d in ranked[:limit]:
            found[d["id"]] = dict(d, score=round(score,4))
        return list(found.values())
