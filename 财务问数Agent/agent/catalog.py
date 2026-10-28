"""一项指标对应一个受控视图，不允许模型自行拼接财务主表。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    label: str
    table: str
    building_col: str
    month_col: str | None
    projection: str
    group_cols: tuple
    note: str
    amount: bool = True


METRICS = {
    "contracts": Metric("合同清单", "T01", "building_id", None,
        "contract_id, contract_name, contract_type, contract_start_date, contract_end_date, building_id",
        (), "按T01合同ID去重展示；包含声明函，类型单列。指定月份时筛选与月份相交的法律起止日期。", False),
    "company_receivable": Metric("我方应收费用（登记基准）", "T06-1", "building_id", "expense_month_id",
        'expense_id AS record_id, expense_month_id AS month_id, company_fee_yuan AS amount_yuan, review_status AS category',
        ("month_id", "category"), "仅T06-1登记基准，按核对状态分组；不含T06-3调整，不推算净应收，待核对状态单列。"),
    "tenant_receivable": Metric("租客账单应收（费用登记口径）", "T05-1", "楼栋ID", "归属月份",
        '"账单ID（沿用费用ID）" AS record_id, "归属月份" AS month_id, "费用金额（元）" AS amount_yuan, "费用科目" AS category',
        ("month_id", "category"), "T05-1房间账单费用登记金额。是否等同待收款未确认；不减实收，不作为未收余额。"),
    "income_booked": Metric("合同范围收入登记", "T05", "building_id", "month_id",
        'expense_id AS record_id, month_id, amount_yuan, fee_type AS category',
        ("month_id", "category"), "T05当前合同范围的收入登记，不能与T05-1、T05-2再次相加。"),
    "actual_receipts": Metric("租客实收登记", "T05-2", "楼栋ID", "实际费用归属月份",
        '"账单ID" AS record_id, "实际费用归属月份" AS month_id, "金额（元）" AS amount_yuan, "费用类型" AS category',
        ("month_id", "category"), "T05-2实收登记，按实际费用归属月份查询；不与T05累计，不自动计算核销。"),
    "owner_payable": Metric("业主应付（支出登记基准）", "T06", "building_id", "month_id",
        'expense_id AS record_id, month_id, amount_yuan, fee_type AS category',
        ("month_id", "category"), "仅T06支出登记基准；预留、调整及实际释放尚未形成完整净应付，不宣称最终应付余额。"),
    "actual_payment": Metric("业主成功实付", "T08-2", "", "来源单据月份",
        '"单据号" AS record_id, "来源单据月份" AS month_id, "付款金额（元）" AS amount_yuan, "付款状态（备注）" AS category',
        ("month_id", "category"), "只计支付成功；按关联T01合同唯一楼栋归属，使用EXISTS避免展示拆行放大。月份为来源单据月份；非付款日期。多楼栋合同不强行分摊，收款关系暂缓28笔仍待业务核对。"),
    "water": Metric("尾期水电划扣流水", "T06-4", "楼栋id", None,
        'rowid AS record_id, "科目" AS category, "备注" AS description, "金额" AS amount_yuan',
        ("category", "description"), "T06-4已合并重复来源，保留正负号及明确0，不判断收支方向。8组待核对未纳入。备注只有月份，年份采用2026来源背景。"),
    "reserve": Metric("预留登记", "T06-5", "楼栋ID", "归属月份",
        '"账单ID" AS record_id, "归属月份" AS month_id, "金额（元）" AS amount_yuan, "科目" AS category',
        ("month_id", "category"), "预留登记不等于预留池余额或实际释放。"),
    "adjustments": Metric("调整登记", "T06-3", "building_id", "month_id",
        'expense_id AS record_id, month_id, adjustment_amount_yuan AS amount_yuan, fee_type AS category',
        ("month_id", "category"), "调整单独查询，保留正负号；不擅自与不一致口径主表合并。"),
    "payment_plan": Metric("支付计划状态", "T07", "building_id", "month_id",
        'plan_id AS record_id, month_id, payment_plan AS category',
        ("month_id", "category"), "当前计划状态，计划不是实际付款；不补缺失26个合同月份，不恢复已剔除计划。", False),
    "exits": Metric("楼栋退出记录", "T05-3", "楼栋id", None,
        '"新模式合同id" AS contract_id, "楼栋id" AS building_id, "退出月份" AS exit_month, "退出形式" AS exit_form',
        (), "退出月份保留源文本；冲突字段为空，并附待核对记录。合同ID空白不可猜填。", False),
}

RECEIVABLE_CHOICES = [
    {"id": "company_receivable", "label": "我方应收费用（登记基准）"},
    {"id": "tenant_receivable", "label": "租客账单应收（费用登记口径）"},
    {"id": "owner_payable", "label": "业主应付（支出登记基准）"},
    {"id": "unsettled", "label": "未收／未付余额（先检查核销依据）"},
]


def canonical_sql(intent):
    metric = METRICS[intent]
    if metric.amount:
        cols = ", ".join(metric.group_cols)
        return (f"SELECT {cols}, COUNT(*) AS record_count, COUNT(amount_yuan) AS known_amount_count, "
                f"DECIMAL_SUM(amount_yuan) AS amount_yuan FROM q_data GROUP BY {cols} ORDER BY {cols}")
    if intent == "contracts":
        return "SELECT DISTINCT contract_id, contract_name, contract_type, contract_start_date, contract_end_date, building_id FROM q_data ORDER BY contract_start_date, contract_id"
    if intent == "exits":
        return "SELECT contract_id, building_id, exit_month, exit_form FROM q_data"
    return "SELECT month_id, category, COUNT(*) AS record_count FROM q_data GROUP BY month_id, category ORDER BY month_id, category"
