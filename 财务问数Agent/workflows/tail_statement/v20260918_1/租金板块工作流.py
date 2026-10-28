"""租金板块判断工作流：依据输出指引，输出判断结论及取数任务。

产品经理阅读：先看 describe 输出，再看 decide 中的五个业务模块。
本版支持Excel自动取数及租金模板输出；数据异常输出具体来源诊断。
金额核对结果需要来自实际月度账单和付款材料，不读取旧第7批目标金额。
运行：python 租金板块工作流.py describe
运行：python 租金板块工作流.py plan --input 楼栋判断输入.json
输入字段说明见“租金板块工作流使用说明.md”。
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re


# 已由业务确认的输入来源。历史核对页按名称中的日期选择最新版本。
SOURCE_MAPPING = {
    "tail_paid": {"sheet": "（新）尾期账单支付申请", "field": "公式 3"},
    "new_claim": {"sheet": "（新）尾期账单支付申请", "field": "备注内容"},
    "delayed_months": {"sheet_pattern": "历史付款情况YYYYMMDD(本次核对)",
                       "fields": "同一月份的所有x月计划列，任一为延迟支付即判为延迟"},
    "monthly_paid": {"sheet_pattern": "历史付款情况YYYYMMDD(本次核对)",
                     "fields": {1: "1月总金额", 2: "2月总金额", 3: "3月总金额",
                                4: "4月金额", 5: "5月金额", 6: "6月金额", 7: "7月金额", 8: "8月金额"}},
    "monthly_due": {"field": "楼栋分成收益", "march_sheet": "更新水电汇总表20260310v1"},
    "output": {"sheet": "xx(项目名)_xx(楼栋名)",
               "visible_amounts": ["租赁收入", "赔偿金"],
               "blank_hidden": ["水电费预留费用", "我方应收费用", "楼栋分成费用", "水费收入", "电费收入"]},
}

# 工作流版本：按延迟状态选择明细，并按需校验延迟汇总。
WORKFLOW_VERSION = "2026-09-13.3"


def is_draft_header(value):
    """历史核对页中，列名含test（不区分大小写）即为草稿列。"""
    return "test" in str(value or "").lower()


def amount(value):
    """按元转换为两位小数；缺失、公式未计算或异常数值均不当作0。"""
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("缺少有效金额")
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise ValueError("金额必须是有限数值")
        return parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"金额无法读取：{value}") from exc


def blank_or_zero(value):
    """识别单元格空白、数字0或文本0；布尔值和错误值不算空白。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value).strip())
        return number.is_finite() and number == 0
    except InvalidOperation:
        return False


def history_payment(headers, row, month_number, formulas=None):
    """任务：读付款申请的历史实付，同时解释空白的业务含义。

    操作：按列名定位金额。金额和同月全部单据都为空/0，按未付0处理。
    1、2月有两列单据，需要一起看；不能拿其他月份的单据作为依据。
    有单据却缺金额、缺少列、公式没有计算结果，仍提示核实。
    只在付款申请取数时使用；不放宽月账单金额及其他通用金额校验。
    """
    field = SOURCE_MAPPING["monthly_paid"]["fields"].get(month_number)
    if field is None:
        raise ValueError(f"尚无{month_number}月金额列映射")
    indexes = [i for i, name in enumerate(headers)
               if not is_draft_header(name) and str(name).strip() == field]
    if len(indexes) != 1:
        raise ValueError(f"实付字段缺失或重复：{field}")
    i = indexes[0]
    raw = row[i] if i < len(row) else None
    formulas = formulas or {}
    if raw is None and i in formulas:
        raise ValueError(f'{field}公式缺少计算结果，请在Excel重算保存')
    blank = raw is None or (isinstance(raw, str) and not raw.strip())
    parsed = None if blank else amount(raw)
    documents = [j for j, name in enumerate(headers)
                 if not is_draft_header(name) and re.fullmatch(rf'0?{month_number}月单据(?:号)?(?:\(\d+\))?',
                                 re.sub(r'\s+', '', str(name)).replace('（', '(').replace('）', ')'))]
    note = f'{field}原值={"空白" if blank else str(raw)}'
    # 非零金额沿用原核对规则；已明确填写的0也保留原来的未付处理。
    if parsed is not None and parsed != 0:
        return {'amount': parsed, 'note': note, 'amount_column': i, 'document_columns': []}
    if not documents:
        if blank:
            raise ValueError(f'{field}为空，且缺少{month_number}月单据字段，不能确认未支付')
        return {'amount': parsed, 'note': note, 'amount_column': i, 'document_columns': []}
    values = [row[j] if j < len(row) else None for j in documents]
    for j, value in zip(documents, values):
        if value is None and j in formulas:
            raise ValueError(f'{headers[j]}公式缺少计算结果，请在Excel重算保存')
    note += '；' + '；'.join(f'{headers[j]}原值={"空白" if v is None or str(v).strip() == "" else str(v)}'
                           for j, v in zip(documents, values))
    if all(blank_or_zero(v) for v in values):
        parsed = Decimal('0.00')
        note += '；金额和同月全部单据均为空/0，按未支付0处理'
    elif blank:
        raise ValueError(f'{field}为空，但同月单据有非空非0内容：{note}，需核实金额')
    return {'amount': parsed, 'note': note, 'amount_column': i, 'document_columns': documents}


def history_amount(headers, row, month_number, formulas=None):
    """仅返回实付金额；完整取数依据由history_payment提供。"""
    return history_payment(headers, row, month_number, formulas)['amount']


HISTORY_ROUNDING_TOLERANCE = Decimal("1.00")


def history_difference_allowed(paid, due):
    """历史非零实付核对：差额绝对值<=1元按四舍五入漏损放行，不改金额。"""
    return abs(amount(paid) - amount(due)) <= HISTORY_ROUNDING_TOLERANCE


def check_history(records):
    """逐月核对；非零实付允许1元内漏损，实际0仍收集为待追加月份。"""
    zero, differences, missing, rounding = [], [], [], []
    seen = set()
    for record in records:
        month = record.get("month")
        if month in seen:
            raise ValueError(f"历史月份重复：{month}")
        seen.add(month)
        try:
            date.fromisoformat(month + "-01")
            paid = amount(record.get("paid"))
            if paid == 0:
                zero.append(month)
                continue
            due = amount(record.get("owner_income"))
        except (ValueError, TypeError):
            missing.append(month)
            continue
        if paid != due:
            target = rounding if history_difference_allowed(paid, due) else differences
            target.append({"month": month, "paid": str(paid), "owner_income": str(due), "difference": str(paid - due)})
    return {"history_check": "mismatch" if differences else "unknown" if missing else "consistent",
            "zero_paid_months": zero, "differences": differences, "missing_months": missing,
            "rounding_differences": rounding}


def select_followup_months(records, exit_month):
    """赔偿退出：每月租赁收入+赔偿金合计>0排除，<=0保留；不跨月抵消。"""
    cutoff = date.fromisoformat(exit_month + "-01")
    kept, excluded = [], []
    seen = set()
    for record in records:
        month = record["month"]
        if month in seen:
            raise ValueError(f"后续月份重复：{month}")
        seen.add(month)
        if date.fromisoformat(month + "-01") <= cutoff:
            raise ValueError(f"{month}不是退出月之后的月份")
        total = amount(record.get("lease_income")) + amount(record.get("compensation"))
        item = {"month": month, "rent_total": str(total)}
        (excluded if total > 0 else kept).append(item)
    return {"保留月份": kept, "排除月份": excluded}


def select_history_sheet(sheet_names):
    """任务：适应业务更新页名日期。操作：识别同类核对页并返回最新日期页。

    支持中英文括号及空格；不按工作表排列顺序选取，不合并不同版本。
    """
    candidates = []
    for name in sheet_names:
        normalized = re.sub(r"\s+", "", name).replace("（", "(").replace("）", ")")
        match = re.fullmatch(r"历史付款情况(\d{8})\(本次核对\)", normalized)
        if not match:
            continue
        stamp = match.group(1)
        try:
            version = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        except ValueError as exc:
            raise ValueError(f"历史核对页日期无效：{name}") from exc
        candidates.append((version, name))
    if not candidates:
        raise ValueError("未找到历史付款情况YYYYMMDD(本次核对)工作表")
    latest = max(version for version, _ in candidates)
    matches = [name for version, name in candidates if version == latest]
    if len(matches) != 1:
        raise ValueError(f"同一最新日期存在多个历史核对页：{matches}")
    return matches[0]


def parse_tail_paid(value):
    """公式3：兼容Excel布尔值和导出文本；缺失或异常不等于未支付。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in ("TRUE", "FALSE"):
            return normalized == "TRUE"
    return None


def month_delayed(headers, row, month_number):
    """遍历所有同月计划列，保留重复列；不使用会覆盖同名列的字典。"""
    columns = [i for i, header in enumerate(headers) if not is_draft_header(header)
               and re.fullmatch(rf"0?{month_number}月计划", re.sub(r"\s+", "", str(header)))]
    values = [str(row[i]).strip() if i < len(row) and row[i] is not None else "" for i in columns]
    if "延迟支付" in values:
        return True
    # 未找到列或全部为空为未知，交由取数层处理。
    return False if any(values) else None


def result(status, explanation, tasks=None, missing=None):
    return {"状态": status, "说明": explanation, "取数任务": tasks or [],
            "待确认": missing or [], "范围": "租赁收入和赔偿金；不含水电结算和代管费用"}


def decide(data):
    """只按明确输入选择分支。未知、未提供与实际0分别处理。"""
    data = dict(data)
    # 模块1：接收楼栋并确认尾期是否已付。
    # 任务：决定是否需要继续出账。操作：读取已核实的支付状态。
    if not data.get("building_id") and not data.get("building_name"):
        return result("待确认", "缺少楼栋信息", missing=["building_id或building_name"])
    paid = data.get("tail_paid")
    if not isinstance(paid, bool):
        return result("待确认", "尾期是否已付尚未明确", missing=["tail_paid"])
    if paid:
        claim = data.get("new_claim")
        if claim == "none":
            return result("已完成尾期支付", "已付且无新提报，无需重复支付")
        if claim == "top_up":
            return result("待取数", "尾期金额补付", [
                "逐月读取历史实付和月度账单，保留调整公式与来源单元格",
                "确认差异中租赁收入及赔偿金部分；不能把全额付款差异直接当作租金差额",
                "输出差额租赁收入及赔偿金账单"])
        if claim == "enterprise":
            if not data.get("claim_content"):
                return result("待确认", "企业账单缺少填报内容", missing=["claim_content"])
            return result("待取数", "企业账单支付", ["按填报内容提取对应租赁收入及赔偿金", "输出差额租赁收入及赔偿金账单"])
        return result("待确认", "新提报内容未分类", missing=["new_claim：none/top_up/enterprise；需人工确认语义"])

    # 模块2：读取退出月份及退出方式。
    # 任务：确定收入归属范围。操作：免租保留实际前月，赔偿走末月分支。
    try:
        month = date.fromisoformat(data["exit_month"] + "-01")
    except (KeyError, ValueError, TypeError):
        return result("待确认", "退出月份须为YYYY-MM", missing=["exit_month"])
    previous = date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)
    mode = data.get("exit_type")
    if mode not in ("compensation", "rent_free"):
        return result("待确认", "退出方式未明确", missing=["exit_type：compensation/rent_free"])

    # 模块3：核对历史非零付款；零付款留待后续补取明细。
    # 任务：识别真实历史付款异常。操作：以原账单含调整金额比较历史实付。
    if "history_records" in data:
        try:
            if not isinstance(data["history_records"], list):
                raise ValueError("history_records须为月份记录列表")
            historical = check_history(data["history_records"])
        except (ValueError, TypeError, AttributeError) as exc:
            return result("待确认", str(exc), missing=["history_records"])
        data.update({key: historical[key] for key in ("history_check", "zero_paid_months")})
        if historical["differences"]:
            output = result("历史支付数据异常，需人工核对", "历史实付与楼栋分成收益不一致")
            output["差异明细"] = historical["differences"]
            return output
    check = data.get("history_check")
    if check == "mismatch":
        return result("历史支付数据异常，需人工核对", "历史非零实付与对应月度账单不一致")
    if check != "consistent":
        return result("待确认", "需逐月核对历史非零实付；实付0跳过比较，未知不能按0处理", missing=["history_check"])

    # 模块4：前月延迟参与明细选择，更早的延迟月份仅确认是否已支付。
    # 任务：避免重复支付。操作：按实际延迟月份选择专用明细页。
    delays = data.get("delayed_months")
    zero_months = data.get("zero_paid_months")
    if not isinstance(delays, list) or not isinstance(zero_months, list):
        return result("待确认", "需明确延迟月份及历史实付为0月份；空列表表示确认没有", missing=["delayed_months", "zero_paid_months"])
    for value in delays + zero_months:
        try:
            parsed = date.fromisoformat(value + "-01")
        except (TypeError, ValueError):
            return result("待确认", "月份列表含无效月份", missing=[str(value)])
        if parsed >= month:
            return result("待确认", "历史月份必须早于退出月，以免重复选取末月", missing=[value])
    prior = previous.strftime("%Y-%m")
    older_delays = sorted({value for value in delays if value != prior})
    payment_status = data.get("delayed_paid", {})
    if not isinstance(payment_status, dict):
        return result("待确认", "更早延迟月份付款状态必须按月份填写", missing=["delayed_paid"])
    for value in older_delays:
        if value in zero_months or payment_status.get(value) is False:
            return result("历史支付数据异常，需人工核对", f"{value}为X-2或更早的延迟月份，尚未支付")
        if payment_status.get(value) is not True:
            return result("待确认", f"需核实更早延迟月份{value}是否已支付", missing=[f"delayed_paid.{value}"])
    prior_delayed = prior in delays

    # 模块5：输出房间明细选择任务；不把任务清单冒充实际取数结果。
    # 任务：按分支组合历史未付和末月明细。操作：附月份、筛选和来源要求。
    tasks = [f"提取{value}实付为0月份的房间租赁收入及赔偿金明细" for value in sorted(set(zero_months))]
    if mode == "rent_free" and prior_delayed:
        if not tasks:
            return result("输出为0的账单", "免租、前月延迟且没有历史实付为0月份")
    elif mode == "rent_free":
        tasks.append(f"从{month:%Y-%m}账单按支付日期早于{prior}-23筛选，不含23日；沿用源账单起始范围，负数与正数同样处理；支付日期缺失时按业务确认口径不再进行日期筛选")
    elif prior_delayed:
        tasks.append(f"从{month:%Y-%m}账单剔除实际归属{prior}的租赁收入及赔偿金，保留新末月明细；归属需读取延迟明细或已确认日期口径")
    else:
        tasks.append(f"提取{month:%Y-%m}房间租赁收入及赔偿金明细")
    if mode == "compensation":
        tasks.append("逐月读取X+1月至最新月：租赁收入＋赔偿金合计>0排除，合计<=0保留；保留月份提取对应房间明细")
        if "followup_records" in data:
            try:
                selection = select_followup_months(data["followup_records"], data["exit_month"])
            except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                return result("待确认", f"后续月份数据异常：{exc}", tasks, ["followup_records"])
            output = result("待取数", "已逐月确定后续月份，按结果提取房间明细", tasks)
            output["后续月份选择"] = selection
            return output
    return result("待取数", "按所列月份和筛选要求提取明细后，汇总租赁收入及赔偿金", tasks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe", help="阅读产品经理版流程及限制")
    plan = sub.add_parser("plan", help="根据人工已核实的输入输出判断路径；不生成Excel")
    plan.add_argument("--input", type=Path, required=True)
    generate = sub.add_parser("generate", help="自动读取申请、付款历史、跨月明细，按模板输出租金Excel")
    generate.add_argument("--root", default=str(Path(__file__).resolve().parent), help="基准文件根目录")
    generate.add_argument("--project-name", help="项目名，可与楼栋名分别填写")
    generate.add_argument("--building-name", help="楼栋名或项目名_楼栋名")
    generate.add_argument("--building-id", help="唯一楼栋ID")
    generate.add_argument("--year", type=int, help="年份，默认从申请反馈时间推导")
    generate.add_argument("--application-row", type=int, help="同日申请冲突时明确指定源行")
    generate.add_argument("--detail-sheet", action="append", help="多个明细版本时指定，例如4=0608延迟支付账单；可重复")
    generate.add_argument("--claim-type", choices=['none','top_up','enterprise'], help="已付尾期的备注语义确认：无新诉求/补付/企业")
    generate.add_argument("--claim-details", help="补付或企业账单已确认租金差额明细JSON")
    generate.add_argument("--output", help="输出xlsx路径，默认保存到租金输出")
    args = parser.parse_args()
    if args.command == "describe":
        print(Path(__file__).with_name("租金板块流程解读.md").read_text(encoding="utf-8"))
        return 0
    try:
        if args.command == "generate":
            from 租金Excel接入 import generate as generate_excel
            import sys
            print(json.dumps(generate_excel(args, sys.modules[__name__]), ensure_ascii=False, indent=2))
            return 0
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("输入须为JSON对象")
        print(json.dumps(decide(data), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
