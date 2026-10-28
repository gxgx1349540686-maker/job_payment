"""Local aggregate diagnostics only; no transcript, financial value or credential output."""
import argparse
import json
import math
import sqlite3
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'runtime/session_memory.sqlite')
options=parser.parse_args()
if not options.db.is_file():raise SystemExit('尚无会话诊断记录。')
c=sqlite3.connect(options.db.resolve().as_uri()+'?mode=ro',uri=True)
counts=dict(c.execute('SELECT status,COUNT(*) FROM turns GROUP BY status').fetchall())
results=[json.loads(r[0]) for r in c.execute("SELECT response FROM turns WHERE status='done' AND response IS NOT NULL")]
c.close()
elapsed=sorted(r.get('diagnostics',{}).get('elapsed_ms',0) for r in results)
usages=[r['diagnostics']['token_usage'] for r in results if r.get('diagnostics',{}).get('token_usage')]
print(json.dumps({'receipt_status_counts':counts,'completed_receipts':len(results),
                  'p95_backend_until_response_commit_ms':elapsed[max(0,math.ceil(len(elapsed)*.95)-1)] if elapsed else None,
                  'model_parse_calls':sum(r.get('diagnostics',{}).get('model_calls',0) for r in results),
                  'max_model_context_chars':max((r.get('diagnostics',{}).get('context_chars',0) for r in results),default=0),
                  'reported_api_total_tokens':sum(u.get('total_tokens',0) for u in usages) if usages else None,
                  'token_note':'无API usage记录时为null，不用字符数伪装真实token。',
                  'semantic_acceptance':'需求遗漏、重复提问和结论正确率须以标注用例核验，不从HTTP成功率推断。'},ensure_ascii=False,indent=2))
