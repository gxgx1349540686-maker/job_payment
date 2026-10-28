import contextlib
import hashlib
import json
import re
import sqlite3
import time
from decimal import Decimal, InvalidOperation

import sqlglot
from sqlglot import exp

from .catalog import METRICS, canonical_sql


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class DecimalSum:
    def __init__(self):
        self.total = Decimal(0)
        self.known = False

    def step(self, value):
        if value is not None and str(value).strip():
            number = Decimal(str(value))
            if not number.is_finite() or number != number.quantize(Decimal("0.01")):
                raise ValueError("金额非有限数或超过分精度，停止汇总")
            self.total += number
            self.known = True

    def finalize(self):
        return format(self.total, ".2f") if self.known else None


def validate_sql(sql, intent):
    if not isinstance(sql, str) or len(sql) > 12000:
        raise ValueError("SQL格式或长度不符合要求")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as exc:
        raise ValueError("SQL语法校验未通过") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("仅允许单条只读SELECT")
    tree = statements[0]
    if any(t.name != "q_data" or t.db or t.catalog for t in tree.find_all(exp.Table)):
        raise ValueError("SQL只能访问已按楼栋和账期限定的q_data视图")
    expected = sqlglot.parse_one(canonical_sql(intent), read="sqlite")
    if tree != expected:
        raise ValueError("SQL未通过业务口径校验：必须使用该指标审核后的查询结构")
    return tree.sql(dialect="sqlite")


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.database
        self.stamp = None

    def connect(self):
        c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        c.row_factory = sqlite3.Row
        c.enable_load_extension(False)
        return c

    def verify_binding(self, force=False):
        stat = self.path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self.path.with_name(self.path.name + "-wal").exists():
            raise ValueError("数据库存在WAL，需先验收一致快照后再接入")
        if force or self.stamp != stamp:
            record = json.loads(self.settings.validation.read_text(encoding="utf-8-sig"))
            actual = sha256(self.path)
            if actual != record["database_sha256"]:
                raise ValueError("R33数据库指纹与验收不一致，请先核查数据变更，已停止查询")
            self.hash = actual
            self.stamp = stamp
        pointer = json.loads((self.settings.project_root / "数据表/当前版本.json").read_text(encoding="utf-8-sig"))
        return {"query_version": "R33 / OBS-V18", "query_state": "交付副本",
                "formal_version": pointer["version"], "formal_workbook": pointer["workbook_version"],
                "database_sha256": self.hash, "database_file": str(self.path)}

    def buildings(self):
        with contextlib.closing(self.connect()) as c:
            return [dict(row) for row in c.execute("SELECT building_id,project,building FROM T03 ORDER BY project,building")]

    def _scoped_view(self, c, intent, building_id, start, end, fee_filter=None):
        metric = METRICS[intent]
        if intent == "actual_payment":
            where = ('"付款状态（备注）"=\'支付成功\' AND EXISTS '
                     '(SELECT 1 FROM main.T01 AS t WHERE t.contract_id="T08-2"."对应新模式合同ID" '
                     f'AND t.building_id={quote(building_id)}) AND '
                     '(SELECT COUNT(DISTINCT t.building_id) FROM main.T01 AS t '
                     'WHERE t.contract_id="T08-2"."对应新模式合同ID")=1')
        else:
            where = f'"{metric.building_col}"={quote(building_id)}'
        if fee_filter:
            if intent != "water" or fee_filter not in ("水费", "电费"):
                raise ValueError("科目过滤不在支持范围")
            where += f' AND "科目"={quote(fee_filter)}'
        if start or end:
            if metric.month_col:
                if start:
                    where += f' AND "{metric.month_col}">={quote(start)}'
                if end:
                    where += f' AND "{metric.month_col}"<={quote(end)}'
            elif intent == "contracts":
                import calendar
                if end:
                    last = f"{end[:4]}-{end[4:]}-{calendar.monthrange(int(end[:4]),int(end[4:]))[1]}"
                    where += f" AND contract_start_date<={quote(last)}"
                if start:
                    where += f" AND contract_end_date>={quote(start[:4]+'-'+start[4:]+'-01')}"
            elif intent in ("water", "exits"):
                # 未知退出月份不能作为明确命中，相关问题仍单独展示。
                c.create_function("SOURCE_MONTH", 1, self.source_month)
                col = "备注" if intent == "water" else "退出月份"
                if start:
                    where += f' AND SOURCE_MONTH("{col}")>={quote(start)}'
                if end:
                    where += f' AND SOURCE_MONTH("{col}")<={quote(end)}'
        c.execute(f'CREATE TEMP VIEW q_data AS SELECT {metric.projection} FROM main."{metric.table}" WHERE {where}')

    def source_month(self, text):
        match = re.search(r"(?<!\d)(1[0-2]|[1-9])月", str(text or ""))
        return f"{self.settings.business_year}{int(match[1]):02d}" if match else None

    def provenance(self, intent, building_id):
        metric = METRICS[intent]
        result = {"physical_table": metric.table, "business_note": metric.note,
                  "validation_file": str(self.settings.validation), "issues": [], "source_rows": []}
        with contextlib.closing(self.connect()) as c:
            if intent in ("water", "exits"):
                result["issues"] = [dict(r) for r in c.execute(
                    'SELECT * FROM "尾期衍生__问题清单" WHERE "楼栋id"=? AND "表名"=?', (building_id,metric.table))]
                result["source_rows"] = [dict(r) for r in c.execute(
                    'SELECT * FROM "尾期衍生__来源记录对照" WHERE "楼栋id"=? ORDER BY CAST("来源记录号" AS INTEGER)',(building_id,))]
                # 将每个问题中的统一索引展开成真实原表行号，保留未知楼栋问题为全局待办。
                for issue in result["issues"]:
                    ids = [s.strip() for s in str(issue["来源记录号"] or "").split(",") if s.strip()]
                    issue["实际来源位置"] = [dict(r) for ident in ids for r in c.execute(
                        'SELECT "来源工作表","原工作表行号","来源记录号" FROM "尾期衍生__来源记录对照" WHERE "来源记录号"=?',(ident,))]
                if intent == "water":
                    result["lineage"] = [dict(r) for r in c.execute('SELECT * FROM "T06-4__逐笔来源" WHERE "楼栋id"=?',(building_id,))]
                else:
                    result["lineage"] = [dict(r) for r in c.execute('SELECT * FROM "T05-3__逐行来源" WHERE "原楼栋id"=?',(building_id,))]
                result["source_workbook"] = str(self.settings.project_root / "数据表/尾期结算迁移_R31_20261017/尾期结算数据表_R31_20261017.xlsx")
        return result

    def query(self, intent, building_id, start=None, end=None, sql=None, fee_filter=None):
        binding = self.verify_binding()
        sql = validate_sql(sql or canonical_sql(intent), intent)
        with contextlib.closing(self.connect()) as c:
            c.create_aggregate("DECIMAL_SUM", 1, DecimalSum)
            self._scoped_view(c, intent, building_id, start, end, fee_filter)
            deadline = time.monotonic()+self.settings.timeout
            c.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
            # SQLite authorizer是第二道强制边界：仅允许来自视图的物理表读取。
            def authorize(action, arg1, arg2, db, source):
                if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_READ:
                    return sqlite3.SQLITE_OK if arg1 == "q_data" or source == "q_data" else sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_FUNCTION:
                    return sqlite3.SQLITE_OK if (arg2 or "").lower() in ("decimal_sum","count","source_month") else sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_DENY
            c.set_authorizer(authorize)
            c.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            cursor = c.execute(sql)
            rows = [dict(row) for row in cursor.fetchmany(self.settings.max_rows+1)]
            if len(rows)>self.settings.max_rows:
                raise ValueError("结果超过显示上限，请缩小月份范围；未对截断数据计算总额")
            c.set_authorizer(None)
            detail = [dict(row) for row in c.execute("SELECT * FROM q_data LIMIT 51")]
            row_count = c.execute("SELECT COUNT(*) FROM q_data").fetchone()[0]
        amounts = [Decimal(row["amount_yuan"]) for row in rows if row.get("amount_yuan") is not None]
        total = format(sum(amounts, Decimal(0)), ".2f") if amounts else None
        if METRICS[intent].amount:
            unknown = sum(row["record_count"]-row["known_amount_count"] for row in rows)
        else:
            unknown = None
        return {"sql":sql,"rows":rows,"record_count":row_count,"known_amount_total":total,"unknown_amount_count":unknown,
                "detail_preview":detail[:50],"detail_truncated":len(detail)>50,
                "period":{"start":start,"end":end,"label":f"{start or '最早'}—{end or '最晚'}" if start or end else "全部已存日期"},
                "fee_filter":fee_filter,"binding":binding,"provenance":self.provenance(intent,building_id)}
