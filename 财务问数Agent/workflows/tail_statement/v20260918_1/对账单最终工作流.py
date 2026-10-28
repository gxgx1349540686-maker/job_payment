"""租金 → 水电 → 汇总表计算，输出（机处理）对账单最终版。"""
import json
from pathlib import Path
from datetime import datetime
from tempfile import TemporaryDirectory
from copy import copy
import 水电板块工作流 as water_rules
import 水电Excel接入 as water
import 汇总表计算 as summary

WORKFLOW_VERSION='2026-09-18.1'


def generate(args):
    root=Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    output=Path(args.output).resolve() if args.output else None
    with TemporaryDirectory(prefix='final_bill_') as directory:
        middle=copy(args);middle.root=str(root);middle.output=str(Path(directory)/'租金水电中间结果.xlsx')
        result=water.generate(middle,water_rules)
        if output is None:
            from openpyxl import load_workbook
            book=load_workbook(result['output'],read_only=True)
            sheet=book.worksheets[0]
            name=water.safe_name(f"{sheet['A6'].value}_{sheet['B6'].value}")
            book.close()
            output=root/'最终输出'/f'（机处理）{name}_{datetime.now():%Y%m%d_%H%M%S}_对账单最终版.xlsx'
        return summary.finish(root,args,Path(result['output']),output,result)


def main():
    parser=water_rules.build_parser()
    parser.description=__doc__
    args=parser.parse_args()
    if args.command=='describe':
        print('最终工作流 '+WORKFLOW_VERSION+'：租金→水电→汇总→高风险提示→合同模式→历史单据→数据来源。')
        return
    try:
        print(json.dumps(generate(args),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    main()
