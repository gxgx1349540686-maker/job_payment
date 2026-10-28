"""水电板块工作流：在已确认的租金流程基础上生成含水电对账单。

产品经理常用命令：
python .\水电板块工作流.py describe
python .\水电板块工作流.py generate --project-name "项目名" --building-name "楼栋名"

本文件负责解释流程和接收命令；Excel取数、凭证OCR和模板输出位于
“水电Excel接入.py”。现有租金计算规则不在本文件中重复实现。
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


WORKFLOW_VERSION = "2026-09-18.1"
OCR_TOLERANCE = Decimal("0.10")


def amount(value) -> Decimal:
    """金额统一保留两位小数；空白不在这里静默转为0。"""
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("缺少有效金额")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"金额无法读取：{value}") from exc
    if not number.is_finite():
        raise ValueError("金额必须是有限数值")
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def reserve_to_record(actual_paid, source_reserve) -> Decimal:
    """实付大于0时保留源预留（包括0和负数）；否则记0。"""
    paid = amount(actual_paid)
    if paid > 0:
        return amount(source_reserve)
    return Decimal("0.00")


def describe() -> dict:
    """给非技术人员阅读的流程任务简述。"""
    return {
        "版本": WORKFLOW_VERSION,
        "目标": "输入项目名和楼栋名，复用租金结果，输出含水电的Excel对账单。",
        "步骤": [
            "1. 先执行租金工作流，取得已经确认的租赁收入和赔偿金明细。",
            "2. 读取退出月X；从2月至X月普通房间明细提取拆分水费、电费，并分别归属前一月收入。",
            "3. 从最新尾期水电文件提取X月房间水电；查无明细时金额记0并备注。",
            "4. 逐月读取付款申请实付。实付>0时记录月账单预留，否则预留记0。",
            "5. 读取申请表水电划扣金额并OCR同一行图片，差额超过0.1元写入备注。",
            "6. 计算需多退少补，并按F6=-需多退少补回填楼栋总账单。",
            "7. 保留租金与水电房间明细、源文件位置、OCR文字和异常说明。",
        ],
        "关键口径": {
            "租金基准": "默认重新运行租金流程；也可用--rent-output复用已确认的租金账单。",
            "普通水电收入": "N月账单普通房间明细的拆分水费、电费归属N-1月；不读取延迟支付、最新调整或账单调整工作表。",
            "1月合并水电费": "规则作废，不读取1月账单的合并水电费；1月水电收入取2月账单拆分字段。",
            "水电预留": "取1月至X月；实付>0才使用源预留，源预留即使为0或负数也照录。",
            "5月后实付": "没有填写列且没有可识别凭证时，填写金额与凭证金额均按0。",
            "尾期缺失": "查无X月尾期房间水电时按0，并备注“未取得尾期水电”。",
            "最终公式": "需多退少补=水电预留-实际水电支出+水电收入；F6=-需多退少补。",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="含水电尾期对账单工作流")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe", help="阅读水电工作流任务和操作说明")

    generate = sub.add_parser("generate", help="按项目名和楼栋名输出含水电Excel对账单")
    generate.add_argument("--root", default=None, help="基准文件根目录，默认使用脚本所在目录")
    generate.add_argument("--project-name", help="项目名")
    generate.add_argument("--building-name", help="楼栋名或项目名_楼栋名")
    generate.add_argument("--building-id", help="唯一楼栋ID")
    generate.add_argument("--year", type=int, help="退出年份，默认从申请反馈时间推导")
    generate.add_argument("--application-row", type=int, help="同日申请冲突时明确指定申请行")
    generate.add_argument("--detail-sheet", action="append", help="租金流程明细覆盖：月份=工作表名")
    generate.add_argument("--claim-type", choices=["none", "top_up", "enterprise"], help="租金流程诉求分类")
    generate.add_argument("--claim-details", help="租金流程人工确认差额明细JSON")
    generate.add_argument("--output", help="输出xlsx路径，默认保存到“水电输出”文件夹")
    generate.add_argument("--rent-output", help="复用已经成功生成的租金对账单；不再重新执行租金取数")
    generate.add_argument("--skip-ocr", action="store_true", help="只嵌入凭证图片，不运行OCR；会写明未核验")
    generate.add_argument('--tail-water-input', help='本次尾期水电明细Excel，与基准文件共同按日期选择')
    return parser


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "describe":
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
        return
    try:
        from 水电Excel接入 import generate

        print(json.dumps(generate(args, sys.modules[__name__]), ensure_ascii=False, indent=2))
    except Exception as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
