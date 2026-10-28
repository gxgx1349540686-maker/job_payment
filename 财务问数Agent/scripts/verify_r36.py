"""Read-only R36 binding, lineage and financial regression checks; no model or Key access."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.settings import Settings, ROOT
from agent.r35_database import R36Database


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def verify():
    settings = Settings.load()
    binding = R36Database(settings).verify_binding()
    pointers = {name: digest(settings.project_root / name) for name in (
        '数据表/当前版本.json', '数据表/当前正式Excel版本.json', '数据库结构/当前结构版本.json')}
    pointer = json.loads((settings.project_root / '数据表/当前版本.json').read_text(encoding='utf-8-sig'))
    validation = json.loads(settings.validation.read_text(encoding='utf-8-sig'))
    current = connect(settings.database)
    previous = connect(pointer['prior_database_file'])
    equal = {}
    for name in ['T08', 'T08_月度金额分', 'T08_楼栋维度', 'T08_执行合同展示', 'T08_应付构成明细', 'T08-2', 'T07']:
        query = 'SELECT * FROM "' + name + '"'
        equal[name] = Counter(current.execute(query).fetchall()) == Counter(previous.execute(query).fetchall())
    rows = current.execute('SELECT "我方实收分","调整净额分","应收业主费用分","水电预留分","水费划扣分","电费划扣分","我方应付分" FROM T08_月度金额分').fetchall()
    formula = all(r[6] == r[0]+r[1]-r[2]-r[3]-r[4]-r[5] for r in rows if r[6] is not None)
    payable = sum(r[6] for r in rows if r[6] is not None)
    paid = sum((Decimal(r[0]) for r in current.execute('SELECT "付款金额（元）" FROM "T08-2" WHERE "付款状态（备注）"=?', ('支付成功',))), Decimal(0))
    statuses = current.execute('SELECT source_table,clean_status,COUNT(*) FROM clean_row_status GROUP BY source_table,clean_status ORDER BY source_table,clean_status').fetchall()
    integrity = current.execute('PRAGMA integrity_check').fetchone()[0]
    fk = current.execute('PRAGMA foreign_key_check').fetchall()
    workbook = digest(pointer['workbook_file']) == validation['validation']['core_workbook_sha256']
    current.close()
    previous.close()
    unchanged = all(digest(settings.project_root / name) == value for name, value in pointers.items())
    result = dict(checked_at=datetime.now(timezone(timedelta(hours=8))).isoformat(), binding=binding,
                  database_sha256=digest(settings.database), financial_tables_equal_r35=equal,
                  integrity_check=integrity, foreign_key_check=fk, monthly_rows=len(rows),
                  unknown_payable_rows=sum(r[6] is None for r in rows), payable_cents=payable,
                  paid_yuan=str(paid), all_known_formulas_valid=formula, clean_status_counts=statuses,
                  core_workbook_fingerprint_valid=workbook, pointer_hashes=pointers,
                  pointers_unchanged=unchanged, real_api_calls=0, key_read=False, business_database_modified=False)
    result['passed'] = (all(equal.values()) and integrity == 'ok' and not fk and len(rows) == 9576
                        and payable == 11691136905 and paid == Decimal('112529007.69')
                        and formula and workbook and unchanged)
    (ROOT / 'runtime').mkdir(exist_ok=True)
    (ROOT / 'runtime/r36_agent_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('binding','pointer_hashes','clean_status_counts')}, ensure_ascii=True, indent=2))
    return result


if __name__ == '__main__':
    sys.exit(0 if verify()['passed'] else 1)
