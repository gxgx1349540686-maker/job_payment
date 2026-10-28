import contextlib
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time

import sqlglot
from sqlglot import exp
from .database import sha256
from .settings import ROOT


def cents(value):
    if value is None or str(value).strip()=='':return None
    d=Decimal(str(value))
    if not d.is_finite() or d*100!=(d*100).to_integral_value():raise ValueError('金额非法或超过分精度，停止查询。')
    return int(d*100)


def yuan(value):return format(Decimal(value)/100,'.2f') if value is not None else None


@dataclass
class Statement:
    name:str
    sql:str
    params:list


ALLOWED={'T08','T08_月度金额分','T08_执行合同展示','T08_楼栋维度','T08_应付构成明细','T08_实收基数差异','T08_水电调整6笔待修改','T08_水电疑点不计入','T01','T08-2','clean__T01','clean__T08','clean__T08-2','clean__T08_应付构成明细','clean_row_status','T09_核销单据','T09','clean__T09','T09_金额分','T10','T10_单据核销余额','T09_V1_重复支付逐单核对','T09_V3_负责人处理','T09_V4_处理落实','T09_分成反算_V3_全量单据','QA_当前楼栋完成情况','QA_楼栋处理状态','QA_当前处理版本','QA_当前楼栋合同状态','QA_楼栋合同版本'}


def validate_statement(sql,expected):
    try:trees=sqlglot.parse(sql,read='sqlite');target=sqlglot.parse_one(expected,read='sqlite')
    except Exception:raise ValueError('SQL语法不正确。') from None
    if len(trees)!=1 or not isinstance(trees[0],exp.Select) or trees[0]!=target:raise ValueError('SQL不符合审核后的参数化查询计划。')
    if any(t.name not in ALLOWED or t.db or t.catalog for t in trees[0].find_all(exp.Table)):raise ValueError('SQL访问了非允许表。')
    return sql


class R36Database:
    def __init__(self,settings):
        self.settings=settings;self.stamp=None
        self.binding_spec=json.loads((ROOT/'knowledge/database_binding.json').read_text(encoding='utf-8'))

    def connect(self):
        c=sqlite3.connect(self.settings.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        c.row_factory=sqlite3.Row;c.enable_load_extension(False);c.execute('PRAGMA query_only=ON')
        return c

    def verify_binding(self):
        spec=self.binding_spec
        pointer=json.loads((self.settings.project_root/'数据表/当前版本.json').read_text(encoding='utf-8-sig'))
        if (pointer.get('version')!=spec['database_version'] or
            Path(pointer['database_file']).resolve()!=self.settings.database.resolve() or
            self.settings.database.resolve()!=Path(spec['database_file']).resolve() or
            self.settings.validation.resolve()!=Path(spec['validation_file']).resolve() or
            Path(pointer['validation']).resolve()!=self.settings.validation.resolve()):
            raise ValueError('Agent已验收绑定为'+spec['query_label']+'；正式入口或配置已变化，请先核对新版本。')
        excel=json.loads((self.settings.project_root/'数据表/当前正式Excel版本.json').read_text(encoding='utf-8-sig'))
        structure=json.loads((self.settings.project_root/'数据库结构/当前结构版本.json').read_text(encoding='utf-8-sig'))
        if (excel.get('formal_database_version')!=spec['database_version'] or
            excel.get('version')!=spec['excel_release'] or
            structure.get('version')!=spec['database_version'] or
            structure.get('data_version')!=spec['data_version'] or
            pointer.get('financial_rules_version')!=spec['financial_rules_version'] or
            pointer.get('workbook_version')!=spec['core_workbook_version'] or
            any(Path(p['database_file']).resolve()!=self.settings.database.resolve() for p in [excel,structure])):
            raise ValueError('正式数据库、Excel和结构入口的版本或数据库路径不一致。')
        validation=json.loads(self.settings.validation.read_text(encoding='utf-8-sig'))
        if (validation.get('version')!=spec['database_version'] or
            validation.get('T09_revision')!=spec['t09_revision'] or
            validation.get('T10_revision')!=spec['t10_revision'] or
            validation.get('state')!='published' or
            validation.get('data_validation',{}).get('state')!='passed' or
            validation.get('workbook_validation',{}).get('state')!='passed'):
            raise ValueError('当前正式发布验收未通过或T09/T10修订版本不一致。')
        if self.settings.database.with_name(self.settings.database.name+'-wal').exists():
            raise ValueError('发现数据库WAL，请先验收一致快照。')
        stat=self.settings.database.stat();stamp=(stat.st_size,stat.st_mtime_ns)
        if stamp!=self.stamp:
            self.hash=sha256(self.settings.database);self.stamp=stamp
        if not all(d==self.hash for d in [spec['database_sha256'],pointer.get('sha256'),validation.get('database_sha256'),validation['database_sha256']]):
            raise ValueError('数据库指纹与已验收绑定、正式入口或验收记录不一致，停止查询。')
        qa_pointer=json.loads((self.settings.project_root/'数据表/当前测试状态登记版本.json').read_text(encoding='utf-8-sig'))
        if (qa_pointer.get('version')!=spec['database_version'] or
            qa_pointer.get('database_sha256')!=self.hash or
            Path(qa_pointer['database_file']).resolve()!=self.settings.database.resolve() or
            qa_pointer.get('rule_version')!=spec['qa_rule_version']):
            raise ValueError('QA楼栋状态登记版本与当前财务数据库不一致，停止查询。')
        with contextlib.closing(self.connect()) as c:
            current_qa=c.execute('SELECT 版本 FROM QA_当前处理版本 WHERE 单例=1').fetchone()
        if not current_qa or current_qa[0]!=spec['database_version']:
            raise ValueError('数据库内QA当前版本与正式版本不一致。')
        return {'query_version':spec['query_label'],'query_state':'正式版本；业务待核对项保留','formal_version':pointer['version'],
                'formal_workbook':pointer['workbook_version'],'excel_release':excel['version'],'t09_revision':spec['t09_revision'],
                't10_revision':spec['t10_revision'],'financial_rules_version':spec['financial_rules_version'],'payment_basis':spec['payment_basis'],'qa_rule_version':spec['qa_rule_version'],'database_sha256':self.hash,'database_file':str(self.settings.database),
                'validation_file':str(self.settings.validation)}

    def indexes(self):
        with contextlib.closing(self.connect()) as c:
            dims=[dict(r) for r in c.execute('SELECT "楼栋维度键" AS dimension_key,"楼栋ID" AS building_id,"项目名" AS project,"楼栋名" AS building,"关联合同ID" AS related_contracts FROM "T08_楼栋维度"')]
            contracts=[dict(r) for r in c.execute('SELECT DISTINCT contract_id,contract_name,project,building,building_id,contract_type,contract_start_date,contract_end_date FROM "clean__T01"')]
            documents=[dict(r) for r in c.execute("""SELECT p."单据号" AS document_id,p."合同ID" AS contract_id,p."归属月份" AS month_id,p."金额分" AS amount_cents,'支付成功' AS status,p."实际付款时间" AS actual_date,COALESCE(c.clean_status,'pending') AS clean_status,c.clean_reason,c.clean_checked_at,c.clean_rule_version FROM "T09_核销单据" p LEFT JOIN "clean__T08-2" c ON c."单据号"=p."单据号" WHERE p."CSV一致"=1""")]
            for row in documents:row['amount_yuan']=yuan(row.pop('amount_cents'))
            documents += [dict(r) for r in c.execute('SELECT "单据号" AS document_id,"对应新模式合同ID" AS contract_id,"来源单据月份" AS month_id,"付款金额（元）" AS amount_yuan,"付款状态（备注）" AS status,"实际付款时间" AS actual_date,clean_status,clean_reason,clean_checked_at,clean_rule_version FROM "clean__T08-2_失败单据"')]
        return dims,contracts,documents

    def run(self,statement,cancelled=None):
        validate_statement(statement.sql,statement.sql)
        with contextlib.closing(self.connect()) as c:
            deadline=time.monotonic()+self.settings.timeout
            c.set_progress_handler(lambda:int(time.monotonic()>deadline or bool(cancelled and cancelled.is_set())),1000)
            def authorize(action,a,b,db,source):
                if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
                if action==sqlite3.SQLITE_READ:return sqlite3.SQLITE_OK if a in ALLOWED else sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_FUNCTION:return sqlite3.SQLITE_OK if (b or '').lower() in ('count','min','max','coalesce','sum','abs') else sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_DENY
            c.set_authorizer(authorize)
            c.execute('EXPLAIN QUERY PLAN '+statement.sql,statement.params).fetchall()
            rows=[dict(r) for r in c.execute(statement.sql,statement.params)]
            for row in rows:
                if 'amount_cents' in row:row['amount_yuan']=yuan(row.pop('amount_cents'))
            return rows


def conditions(keys,start,end,prefix='t'):
    if not keys:raise ValueError('查询对象没有已确认楼栋关系，不能猜测金额归属。')
    where=f'{prefix}."楼栋维度键" IN ('+','.join('?' for _ in keys)+')';params=list(keys)
    if start:where+=f' AND {prefix}."归属月份">=?';params.append(start)
    if end:where+=f' AND {prefix}."归属月份"<=?';params.append(end)
    return where,params


def compile_plan(scope,start,end,index):
    keys=scope['dimension_keys'];where,params=conditions(keys,start,end)
    monthly=Statement('楼栋月度已确认金额',f'''SELECT t."应付记录ID" AS record_id,t."楼栋维度键" AS dimension_key,t."楼栋ID" AS building_id,t."项目名" AS project,t."楼栋名" AS building,t."归属月份" AS month_id,
 m."我方实收分" AS receipts_cents,m."应收业主费用分" AS receivable_cents,m."调整净额分" AS adjustment_cents,m."水电预留分" AS reserve_cents,m."水费划扣分" AS water_cents,m."电费划扣分" AS electricity_cents,m."我方应付分" AS payable_cents,t."计算状态" AS calculation_status,t."数据提示" AS data_note,t."实收计算状态" AS receipts_status,t."水电收入账期" AS utility_period,
 m."当月租赁分" AS rent_cents,m."当月赔偿分" AS compensation_cents,m."上账期水费分" AS previous_water_cents,m."上账期电费分" AS previous_electricity_cents,t.clean_status,t.clean_reason,t.clean_checked_at,t.clean_rule_version
 FROM "clean__T08" AS t JOIN "T08_月度金额分" AS m ON m."应付记录ID"=t."应付记录ID" WHERE {where} ORDER BY t."项目名",t."楼栋名",t."归属月份"''',params)
    executions=Statement('执行合同（独立查询，不展开金额）',f'''SELECT t."楼栋维度键" AS dimension_key,t."归属月份" AS month_id,t."合同ID" AS contract_id,t."合同类型" AS contract_type,t."合同开始日期" AS contract_start_date,t."合同结束日期" AS contract_end_date,t."用途" AS usage,
 (SELECT MIN(c.contract_name) FROM "T01" c WHERE c.contract_id=t."合同ID") AS contract_name,
 (SELECT COUNT(*) FROM "clean__T01" c WHERE c.contract_id=t."合同ID" AND c.clean_status!='passed') AS contract_review_row_count FROM "T08_执行合同展示" AS t WHERE {where} ORDER BY t."归属月份",t."合同ID"''',params)
    # 先取可能相关合同的成功单据，归属集合由程序严格校验，不能连接T01展示行放大金额。
    related=[cid for cid,c in index.contracts.items() if c['dimensions']&set(keys)]
    if scope.get('contract_id'):related=[scope['contract_id']]
    if not related:related=['__no_contract__']
    pay_where='p."合同ID" IN ('+','.join('?' for _ in related)+') AND p."CSV一致"=?';pay_params=related+[1]
    if start:pay_where+=' AND p."归属月份">=?';pay_params.append(start)
    if end:pay_where+=' AND p."归属月份"<=?';pay_params.append(end)
    payments=Statement('成功实付单据（单据号唯一）',f'''SELECT p."单据号" AS document_id,p."合同ID" AS contract_id,p."归属月份" AS month_id,p."金额分" AS amount_cents,'支付成功' AS status,p."实际付款时间" AS actual_date,
 COALESCE(c.clean_status,'pending') AS clean_status,c.clean_reason,c.clean_checked_at,c.clean_rule_version,'T09_核销单据' AS payment_source_table,p."CSV文件" AS payment_source_file,p."CSV来源行" AS payment_source_row
 FROM "T09_核销单据" p LEFT JOIN "clean__T08-2" c ON c."单据号"=p."单据号" WHERE {pay_where} ORDER BY p."归属月份",p."单据号"''',pay_params)
    ledger=Statement('采用来源预览',f'''SELECT t."应付记录ID" AS payable_record_id,t."来源表" AS source_table,t."来源rowid" AS source_rowid,t."来源费用ID" AS source_expense_id,t."归属月份" AS month_id,t."科目" AS category,t."原金额元" AS source_amount_yuan,t."采用状态" AS adoption_status,t."处理原因" AS reason,t.clean_status,t.clean_reason,t.clean_checked_at,t.clean_rule_version FROM "clean__T08_应付构成明细" AS t WHERE {where} ORDER BY t."归属月份",t."明细ID" LIMIT 51''',params)
    settlement_where=where
    settlement_params=list(params)
    if scope.get('contract_id'):
        settlement_where+=' AND t."新模式合同ID"=?';settlement_params.append(scope['contract_id'])
    settlements=Statement('T09楼栋月度实付汇总',f'''SELECT t."明细ID" AS detail_id,t."楼栋维度键" AS dimension_key,t."楼栋ID" AS building_id,t."项目名" AS project,t."楼栋名" AS building,t."归属月份" AS month_id,t."新模式合同ID" AS contract_id,
 m."分摊实付分" AS paid_cents,m."账单收益分" AS settlement_income_cents,t."特殊待核对" AS special_review,t."合同月核对状态" AS allocation_status,t."实付登记依据" AS basis,t."是否计入成功实付金额" AS cash_included,t."原始汇总行" AS source_row,t."说明" AS note,t.clean_status AS clean_status,t.clean_reason AS clean_reason
 FROM "clean__T09" t JOIN "T09_金额分" m ON m."明细ID"=t."明细ID" WHERE {settlement_where} ORDER BY t."归属月份",t."明细ID"''',settlement_params)
    writeoffs=Statement('T10有效核销（账单月份）',f'''SELECT h."核销ID" AS writeoff_id,h."单据号" AS document_id,h."明细ID" AS detail_id,t."楼栋维度键" AS dimension_key,t."归属月份" AS month_id,t."新模式合同ID" AS contract_id,
 h."单据归属月份" AS document_month,h."有效核销分" AS effective_cents,h."采用方式" AS adoption,h."核销依据" AS basis,h.clean_status,h.clean_reason
 FROM T10 h JOIN T09 t ON t."明细ID"=h."明细ID" WHERE {settlement_where} ORDER BY t."归属月份",h."核销ID"''',settlement_params)
    balance_where='b."合同ID" IN ('+','.join('?' for _ in related)+')';balance_params=list(related)
    if start:balance_where+=' AND b."单据归属月份">=?';balance_params.append(start)
    if end:balance_where+=' AND b."单据归属月份"<=?';balance_params.append(end)
    balances=Statement('核销单据余额（单据登记月份，唯一单据）',f'''SELECT b."单据号" AS document_id,b."合同ID" AS contract_id,b."单据归属月份" AS month_id,b."单据实付分" AS cash_cents,b."已核销分" AS written_off_cents,b."未核销余额分" AS unmatched_cents,b."核销状态" AS reconciliation_status,b."待确认关系数" AS pending_relations
 FROM "T10_单据核销余额" b WHERE {balance_where} ORDER BY b."单据归属月份",b."单据号"''',balance_params)
    extra_where='t."楼栋维度键" IS NULL AND t."新模式合同ID" IN ('+','.join('?' for _ in related)+')'
    extra_params=list(related)
    if start:extra_where+=' AND t."归属月份">=?';extra_params.append(start)
    if end:extra_where+=' AND t."归属月份"<=?';extra_params.append(end)
    contract_writeoffs=Statement('合同级核销汇总（未分栋，不计月账实付）',f'''SELECT h."核销ID" AS writeoff_id,h."单据号" AS document_id,t."明细ID" AS detail_id,t."新模式合同ID" AS contract_id,t."归属月份" AS month_id,h."单据归属月份" AS document_month,h."有效核销分" AS effective_cents,h."采用方式" AS adoption,h."核销依据" AS basis,t."说明" AS note
 FROM T10 h JOIN T09 t ON t."明细ID"=h."明细ID" WHERE {extra_where} ORDER BY t."归属月份",h."核销ID"''',extra_params)
    qa_where='q."楼栋维度键" IN ('+','.join('?' for _ in keys)+')'
    qa=Statement('楼栋信息完整性（当前QA登记，非逐月结清）',f'''SELECT q."版本" AS registry_version,q."楼栋维度键" AS dimension_key,q."楼栋ID" AS building_id,q."项目" AS project,q."楼栋" AS building,q."测试状态" AS test_status,q."完成状态" AS completion_status,q."标签" AS completeness_label,q."原因" AS completeness_reason,q."覆盖范围" AS coverage,q."退出日期" AS exit_date
 FROM "QA_当前楼栋完成情况" q WHERE {qa_where} ORDER BY q."项目",q."楼栋",q."楼栋维度键"''',list(keys))
    contract_qa_where=qa_where;contract_qa_params=list(keys)
    if scope.get('contract_id'):
        contract_qa_where+=' AND q."合同ID"=?';contract_qa_params.append(scope['contract_id'])
    contract_qa=Statement('合同在楼栋范围内的QA状态',f'''SELECT q."版本" AS registry_version,q."楼栋维度键" AS dimension_key,q."合同ID" AS contract_id,q."完成状态" AS completion_status,q."标签" AS completeness_label
 FROM "QA_当前楼栋合同状态" q WHERE {contract_qa_where} ORDER BY q."楼栋维度键",q."合同ID"''',contract_qa_params)
    return [monthly,executions,payments,ledger,settlements,writeoffs,balances,contract_writeoffs,qa,contract_qa]

# Backward-compatible import for the existing test and extension interfaces.
R35Database = R36Database
