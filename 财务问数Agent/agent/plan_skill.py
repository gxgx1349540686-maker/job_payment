"""Application-owned skill registry and local, read-only payment-plan checks."""
import contextlib
import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    building_id: str = Field(min_length=1, max_length=100)
    payment_month: str
    settlement_month: str
    settlement_kind: str = Field(default='tail', pattern='^tail$')
    contract_id: str = Field(default='', max_length=100)

    @field_validator('payment_month', 'settlement_month')
    @classmethod
    def month(cls, value):
        if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', value):
            raise ValueError('月份请填写YYYY-MM；支付月份与结算核对月份分开填写。')
        return value


class PlanSkills:
    def __init__(self, root, db, index):
        self.root = Path(root)
        self.db, self.index = db, index
        self.folder = self.root / 'runtime/payment_plan_tasks'
        self.manifest_path = self.root / 'skills/payment-plan-reconcile/skill.json'
        self._load()

    def _load(self):
        raw = self.manifest_path.read_bytes()
        spec = json.loads(raw)
        if spec['name'] != 'payment-plan-reconcile' or spec['handler'] != 'local_plan_check_v1':
            raise ValueError('计划Skill注册无效。')
        expected = ['list_plans', 'get_contract_candidates', 'get_failed_documents', 'build_review_packet']
        if spec['tools'] != expected or not spec.get('rules', {}).get('tail_description'):
            raise ValueError('计划Skill工具或规则未通过加载校验。')
        instruction = (self.manifest_path.parent / 'SKILL.md').read_bytes()
        self.spec = spec
        self.rule_hash = hashlib.sha256(raw + instruction).hexdigest()

    def catalog(self):
        self._load()
        return {'name': self.spec['name'], 'version': self.spec['version'],
                'rule_sha256': self.rule_hash, 'label': '制定计划',
                'mode': '本地只读核对；尾期；待人工复核', 'tools': self.spec['tools']}

    def buildings(self):
        return [{'building_id': d['building_id'], 'project': d['project'], 'building': d['building']}
                for d in self.index.by_dimension.values() if d['building_id']]

    def check(self, payload):
        self._load()
        skill_stamp = self.catalog()
        description_template = self.spec['rules']['tail_description']
        args = PlanInput.model_validate(payload)
        binding = self.db.verify_binding()
        matches = [d for d in self.index.by_dimension.values() if d['building_id'] == args.building_id]
        if len(matches) != 1:
            raise ValueError('楼栋ID不存在或不唯一，请从已登记楼栋中选择。')
        building = matches[0]
        month = args.settlement_month.replace('-', '')
        refs, steps = [], []
        deadline = time.monotonic() + self.db.settings.timeout
        with contextlib.closing(self.db.connect()) as c:
            c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

            def query(tool, sql, params, table, id_field):
                rows = [dict(r) for r in c.execute(sql, params)]
                if len(rows) > 200:
                    raise ValueError('核对结果超过200条，未保存不完整结果；请缩小范围后核对。')
                steps.append({'tool': tool, 'row_count': len(rows), 'complete': True})
                refs.extend({'table': table, 'record_id': str(r[id_field]),
                             'database_sha256': binding['database_sha256']} for r in rows)
                return rows

            plans = query('list_plans',
                'SELECT plan_id,contract_id,building_id,month_id,payment_plan,review_note FROM T07 WHERE building_id=? AND month_id=? LIMIT 201',
                [args.building_id, month], 'T07', 'plan_id')
            contracts = query('get_contract_candidates',
                'SELECT DISTINCT contract_id,contract_name,contract_type,contract_start_date,contract_end_date,payee_name,bank_name,bank_account_number FROM T01 WHERE building_id=? LIMIT 201',
                [args.building_id], 'T01', 'contract_id')
            # Bank details stay in the backend; ordinary responses retain only missingness and last four digits.
            for row in contracts:
                account = str(row.pop('bank_account_number') or '').strip()
                row['account_present'] = bool(account)
                row['account_masked'] = '****' + account[-4:] if len(account) >= 4 else ('已登记（待核验）' if account else '未登记')
            ids = sorted({r['contract_id'] for r in contracts})
            failures = []
            if ids:
                marks = ','.join('?' for _ in ids)
                failures = query('get_failed_documents',
                    f'SELECT "单据号" AS document_id,"对应新模式合同ID" AS contract_id,"来源单据月份" AS month_id,"付款状态（备注）" AS status FROM "T08-2_失败单据" WHERE "对应新模式合同ID" IN ({marks}) LIMIT 201',
                    ids, 'T08-2_失败单据', 'document_id')
        issues = [
            '尚未取得本次尾期结算金额及人工核验的有效结果引用，申请金额保持未知。',
            '合同和本版收益明细截图的齐全性、内容及版本仍需核验。',
            '当前执行合同的采用时点及申请归属起止规则待确认；支付月份不替代结算期间。',
        ]
        if not plans:
            issues.append('核对月份无历史计划；不能认定无需支付，也不自动补计划。')
        if len(plans) > 1:
            issues.append('同楼栋同月有多条计划，保留全部记录，不自动取首条或覆盖。')
        chosen = [r for r in contracts if r['contract_id'] == args.contract_id] if args.contract_id else []
        if args.contract_id and len(chosen) != 1:
            raise ValueError('所选合同不属于该楼栋或合同信息不唯一，请重新核对。')
        if not chosen:
            issues.append('尚未人工选定结算采用合同；下方合同仅为历史候选，不等于当前执行合同。')
        if failures:
            issues.append('关联历史失败单据需人工检查是否退票及是否影响本次收款关系；失败不等同退票。')
        description = None
        if chosen:
            row = chosen[0]
            if not all([row['payee_name'], row['bank_name'], row['account_present'], row['contract_type']]):
                issues.append('所选合同收款三要素或合同类型缺失，不能完成说明及收款核对。')
            else:
                description = description_template.format(
                    project=building['project'], building=building['building'], payee=row['payee_name'],
                    contract_type=row['contract_type'], payment_month=int(args.payment_month[-2:]))
                issues.append('说明仅按所选合同生成草稿；尚未确认合同采用及收款关系一致性。')
        if self.db.verify_binding()['database_sha256'] != binding['database_sha256']:
            raise ValueError('核对中数据版本变化，请重新开始。')
        if self.catalog()['rule_sha256'] != skill_stamp['rule_sha256']:
            raise ValueError('核对中Skill规则变化，请重新开始。')
        token = uuid.uuid4().hex
        steps.append({'tool': 'build_review_packet', 'complete': True})
        result = {'task_id': token, 'status': 'needs_review', 'skill': skill_stamp,
                  'created_at': datetime.now().astimezone().isoformat(), 'input': args.model_dump(),
                  'database_sha256': binding['database_sha256'],
                  'scope': {k: building[k] for k in ['project', 'building', 'building_id']},
                  'description_draft': description, 'application_amount_cents': None,
                  'plans': plans, 'contract_candidates': contracts, 'failed_documents': failures,
                  'issues': issues, 'source_refs': refs, 'steps': steps,
                  'human_review': '未完成', 'payment_authorized': False,
                  'answer': '已完成本地资料核对，结果待人工复核。未生成可执行支付指令，申请金额未知。'}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / (token + '.json')
        # New task IDs never overwrite a previous result. No business database is modified.
        with path.open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    def restore(self, token):
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('无效任务编号。')
        self._load()
        result = json.loads((self.folder / (token + '.json')).read_text('utf-8'))
        binding = self.db.verify_binding()
        result['stale'] = (result['database_sha256'] != binding['database_sha256'] or
                           result['skill']['rule_sha256'] != self.rule_hash)
        if result['stale']:
            result['status'] = 'stale'
            result['issues'] = ['数据或规则已变化，旧结果仅供追溯，请重新核对。'] + result['issues']
        return result
