"""Read-only release/QA verification; never instantiate the API provider."""
import sys,json,contextlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.settings import Settings,ROOT
from agent.r35_database import R36Database
from agent.database import sha256

def verify():
    db=R36Database(Settings.load());binding=db.verify_binding();project=db.settings.project_root
    pointer=json.loads((project/'数据表/当前版本.json').read_text(encoding='utf-8-sig'))
    qa=json.loads((project/'数据表/当前测试状态登记版本.json').read_text(encoding='utf-8-sig'))
    strict=json.loads((project/'数据表/当前楼栋财务对应清单.json').read_text(encoding='utf-8-sig'))
    validation=json.loads(db.settings.validation.read_text(encoding='utf-8-sig'))
    books=[]
    for r in validation['workbook_validation']['books']:
        file=db.settings.validation.parent/Path(r['file']).name
        books.append({'file':str(file),'expected':r['sha256'],'actual':sha256(file)})
    with contextlib.closing(db.connect()) as c:
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0];fk=c.execute('PRAGMA foreign_key_check').fetchall()
        statuses=[dict(r) for r in c.execute('SELECT 测试状态,完成状态,COUNT(*) AS count FROM QA_当前楼栋完成情况 GROUP BY 测试状态,完成状态')]
        counts=c.execute("SELECT COUNT(*),COUNT(DISTINCT 楼栋维度键),SUM(完成状态='已处理完成'),SUM(标签 IS NOT NULL AND 标签!='') FROM QA_当前楼栋完成情况").fetchone()
        monthly=c.execute('SELECT COUNT(*),SUM(我方实收分),SUM(我方应付分) FROM T08_月度金额分').fetchone()
        controls=list(c.execute('SELECT * FROM T10_核销控制').fetchone())
        missing_contract_building=c.execute('SELECT COUNT(*) FROM QA_当前楼栋合同状态 q LEFT JOIN QA_当前楼栋完成情况 b ON b.楼栋维度键=q.楼栋维度键 WHERE b.楼栋维度键 IS NULL').fetchone()[0]
    passed=(integrity=='ok' and not fk and counts[0]==counts[1] and counts[2]==qa['completed_buildings'] and counts[3]==qa['active_incomplete_labels'] and not missing_contract_building and all(r['actual']==r['expected'] for r in books) and strict['database_sha256']==binding['database_sha256'] and sha256(Path(strict['file']))==strict['sha256'] and controls[0]==controls[1]+controls[2] and controls[1]==controls[3])
    result={'passed':passed,'binding':binding,'integrity':integrity,'foreign_key_errors':len(fk),'registry_counts':statuses,'building_count':counts[0],'complete_count':counts[2],'incomplete_count':counts[3],'monthly_count':monthly[0],'known_receipts_cents':monthly[1],'known_payable_cents':monthly[2],'writeoff_control_cents':controls,'strict_complete_buildings':strict['fully_qualified_buildings'],'strict_list':strict['file'],'qa_list':qa['file'],'workbook_hashes':books,'real_api_calls':0,'business_database_modified':False}
    (ROOT/'runtime').mkdir(exist_ok=True);(ROOT/'runtime/qa46_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2));return passed
if __name__=='__main__':sys.exit(0 if verify() else 1)
