from dataclasses import replace
from pathlib import Path
import json
import pytest
from agent.r35_database import R36Database,compile_plan,cents
from agent.settings import Settings
from agent.engine import Agent

class Local:
    enabled=False


def cloned_pointers(tmp_path):
    settings=Settings.load()
    for name in ['数据表/当前版本.json','数据表/当前正式Excel版本.json','数据库结构/当前结构版本.json']:
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((settings.project_root/name).read_bytes())
    return replace(settings,project_root=tmp_path)


def test_latest_binding_distinguishes_db_excel_and_core():
    binding=R36Database(Settings.load()).verify_binding()
    assert binding['formal_version']=='OBS-V31'
    assert binding['excel_release']=='Excel-R38'
    assert binding['formal_workbook']=='OBS-R36'
    assert binding['t09_revision']=='V8'


def test_future_formal_version_is_not_automatically_accepted(tmp_path):
    settings=cloned_pointers(tmp_path);p=tmp_path/'数据表/当前版本.json'
    d=json.loads(p.read_text(encoding='utf-8-sig'));d['version']='OBS-V999';p.write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError,match='入口或配置'):R36Database(settings).verify_binding()


def test_hash_checked_even_after_database_stamp_is_cached(tmp_path):
    settings=cloned_pointers(tmp_path);db=R36Database(settings);db.verify_binding()
    p=tmp_path/'数据表/当前版本.json';d=json.loads(p.read_text(encoding='utf-8-sig'));d['sha256']='0'*64;p.write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError,match='指纹'):db.verify_binding()


def test_cross_pointer_database_path_mismatch_rejected(tmp_path):
    settings=cloned_pointers(tmp_path);p=tmp_path/'数据库结构/当前结构版本.json'
    d=json.loads(p.read_text(encoding='utf-8-sig'));d['database_file']='E:/wrong.sqlite';p.write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError,match='路径不一致'):R36Database(settings).verify_binding()


def test_cash_source_includes_new_success_without_double_counting():
    a=Agent(Settings.load(),model=Local())
    scope={'kind':'project','label':'全范围核验','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    payments=a.db.run(compile_plan(scope,None,None,a.index)[2])
    assert len(payments)==3861 and len({p['document_id'] for p in payments})==3861
    assert sum(cents(p['amount_yuan']) for p in payments)==11256567073
    new=next(p for p in payments if p['document_id']=='NPSZFS202601130001')
    assert new['amount_yuan']=='36663.04' and new['month_id']=='202601'
    assert new['payment_source_table']=='T09_核销单据'


def test_new_document_can_be_queried_without_model():
    a=Agent(Settings.load(),model=Local())
    r=a.chat({'message':'单据NPSZFS202601130001已付多少钱'})
    assert r['status']=='answered'
    assert r['data']['selected_document']['amount_yuan']=='36663.04'
    assert 'OBS-V31' in r['answer'] and 'R36 / OBS-V21正式版' not in r['answer']
