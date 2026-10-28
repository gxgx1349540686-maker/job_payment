"""Check the approved current binding and cash total; no Key or model calls."""
import contextlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.settings import Settings,ROOT
from agent.r35_database import R36Database

def verify():
    db=R36Database(Settings.load());binding=db.verify_binding()
    with contextlib.closing(db.connect()) as c:
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=c.execute('PRAGMA foreign_key_check').fetchall()
        count,total,unique=c.execute('SELECT COUNT(*),SUM("金额分"),COUNT(DISTINCT "单据号") FROM "T09_核销单据" WHERE "CSV一致"=1').fetchone()
        rows=c.execute('SELECT 我方实收分,我方应付分,当月租赁分,当月赔偿分,上账期水费分,上账期电费分,水电预留分,调整净额分 FROM T08_月度金额分').fetchall()
        formula_errors=[]
        for n,r in enumerate(rows):
            receipt,payable,rent,comp,water,electric,reserve,adjust=r
            if receipt is not None and (None in (rent,comp,water,electric) or receipt!=rent+comp+water+electric):formula_errors.append(['receipts',n])
            if payable is not None and (None in (rent,comp,reserve,adjust) or payable!=rent+comp-reserve+adjust):formula_errors.append(['payable',n])
        control=list(c.execute('SELECT * FROM T10_核销控制').fetchone())
        unmatched_count=c.execute('SELECT COUNT(*) FROM T10_单据核销余额 WHERE 未核销余额分!=0').fetchone()[0]
        paid=c.execute('SELECT SUM(分摊实付分) FROM T09_金额分').fetchone()[0]
    pointer=json.loads((db.settings.project_root/'数据表/当前版本.json').read_text(encoding='utf-8-sig'))
    validation=json.loads(db.settings.validation.read_text(encoding='utf-8-sig'))
    from agent.database import sha256
    books=[]
    for item in validation['workbook_validation']['books']:
        actual=next((Path(v) for v in pointer['files'].values() if Path(v).name==Path(item['file']).name), db.settings.validation.parent/Path(item['file']).name)
        books.append({'file':str(actual),'expected':item['sha256'],'actual':sha256(actual)})
    result={'binding':binding,'integrity_check':integrity,'foreign_key_check':foreign_keys,'successful_documents':count,'cash_cents':total,'unique_documents':unique,'real_api_calls':0,'key_read':False}
    result.update(formula_errors=formula_errors,monthly_count=len(rows),receipts_unknown=sum(r[0] is None for r in rows),known_receipts_cents=sum(r[0] or 0 for r in rows),known_payable_cents=sum(r[1] or 0 for r in rows),T09_registered_paid_cents=paid,T10_control_cents=control,unmatched_documents=unmatched_count,workbook_fingerprints=books)
    result['passed']=not formula_errors and control==validation['data_validation']['control_cents'] and control[0]==control[1]+control[2] and control[1]==control[3] and all(b['expected']==b['actual'] for b in books) and integrity=='ok' and not foreign_keys and count==unique==3861 and total==11256567073
    (ROOT/'runtime').mkdir(exist_ok=True)
    (ROOT/'runtime/current_binding_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return result
if __name__=='__main__':sys.exit(0 if verify()['passed'] else 1)
