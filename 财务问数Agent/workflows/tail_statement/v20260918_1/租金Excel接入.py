"""Excel接入层：只读原始文件、按楼栋与月份取数、按新版模板输出。

入口仍为“租金板块工作流.py generate”，产品经理无需单独运行本文件。
保留字段出处与源公式；数据缺失或冲突输出诊断，避免生成似乎正确的金额。
"""
from __future__ import annotations

import json
import re
import posixpath
import zipfile
from collections import Counter
from copy import copy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
ROOT = Path(__file__).resolve().parent
DATABASE_FOLDER = '每次付款后修改_持续更新数据库'
INPUT_FOLDER = '每次付款前修改_输入'
MONTHLY_FOLDER = '月度修改_月度账单'
SUMMARY_NAMES = {1: '楼栋收入底表', 2: '楼栋收入_更新水电260210',
                 3: '更新水电汇总表20260310v1', 4: '202604汇总表',
                 5: '分成收益表', 6: '汇总表', 7: '汇总表', 8: '汇总表'}
DETAIL_NAMES = {1: '1月9号楼栋实收房间明细表', 2: '对账单明细', 3: '房间明细表'}
ALIASES = {
    'id': ['楼栋ID', 'building_id', '楼栋号'],
    'project': ['项目名', '项目名称', '项目', 'project_name'],
    'building': ['楼栋名', '楼栋名称', 'building_name'],
    'room': ['房间号', '项目名2', 'room_number'],
    'begin': ['合同开始时间', '合同开始日期', '开始时间', 'begin_date'],
    'end': ['合同结束时间', '合同结束日期', '合同结束', '结束时间', 'end_date'],
    'lease': ['租赁收入', 'lease_income'], 'comp': ['赔偿金', 'compensation_fee'],
    'due': ['楼栋分成收益', '楼栋业主收入'],
    'paid_at': ['最晚支付时间', '支付日期', '支付时间', 'latest_payment_time'],
    'remark': ['赔偿金备注', 'compensation_remark', '备注'],
}


class DataIssue(ValueError):
    """有明确来源的数据缺口或冲突。"""


class MonthlyRentUnavailable(DataIssue):
    """月度租金未取得，允许带缺失说明出账；不用于金额冲突。"""


def clean(value):
    return re.sub(r'\s+', '', str(value or '')).lower().replace('（', '(').replace('）', ')')


def name_key(value):
    return clean(value).replace('_', '')


class SourceBook:
    """只读取XLSX数据XML，兼容损坏样式及错误的工作表尺寸标记。"""
    def __init__(self, path):
        self.path = Path(path)
        self.zip = zipfile.ZipFile(path)
        self.strings = []
        if 'xl/sharedStrings.xml' in self.zip.namelist():
            xml = ET.fromstring(self.zip.read('xl/sharedStrings.xml'))
            self.strings = [''.join(t.text or '' for t in si.iter(NS + 't')) for si in xml]
        links = ET.fromstring(self.zip.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in links}
        xml = ET.fromstring(self.zip.read('xl/workbook.xml'))
        self.sheets = {}
        for s in xml.find(NS + 'sheets'):
            target = targets[s.attrib[REL + 'id']]
            self.sheets[s.attrib['name']] = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        self.cache = {}

    def rows(self, sheet):
        if sheet not in self.sheets:
            raise MonthlyRentUnavailable(f'{self.path.name}：缺少工作表“{sheet}”')
        if sheet not in self.cache:
            parsed = []
            with self.zip.open(self.sheets[sheet]) as stream:
                for _, row in ET.iterparse(stream, events=['end']):
                    if row.tag != NS + 'row':
                        continue
                    values, formulas = {}, {}
                    for c in row:
                        coord = c.attrib.get('r', '')
                        letters = re.match('[A-Z]+', coord)
                        if not letters:
                            continue
                        col = 0
                        for ch in letters[0]: col = col * 26 + ord(ch) - 64
                        v, f = c.find(NS + 'v'), c.find(NS + 'f')
                        kind, value = c.attrib.get('t'), None
                        if kind == 'inlineStr':
                            value = ''.join(t.text or '' for t in c.iter(NS + 't'))
                        elif v is not None and v.text is not None:
                            value = v.text
                            if kind == 's': value = self.strings[int(value)]
                            elif kind == 'b': value = value == '1'
                            elif kind not in ('str', 'e'): value = Decimal(value)
                        values[col - 1] = value
                        if f is not None: formulas[col - 1] = '=' + (f.text or '')
                    if values:
                        parsed.append((int(row.attrib['r']), [values.get(i) for i in range(max(values) + 1)], formulas))
                    row.clear()
            self.cache[sheet] = parsed
        return self.cache[sheet]

    def close(self):
        self.zip.close()


def index(headers, names, required=True):
    hits = [i for i, h in enumerate(headers) if clean(h) in {clean(n) for n in names}]
    if len(hits) != 1:
        if not hits and not required: return None
        raise DataIssue(f'字段缺失或不唯一：{names}，匹配{len(hits)}列')
    return hits[0]


def cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def table(book, sheet, required):
    rows = book.rows(sheet)
    for j, (n, h, _) in enumerate(rows[:40]):
        if all(any(clean(v) in {clean(a) for a in ALIASES[k]} for v in h) for k in required):
            return h, rows[j+1:]
    raise MonthlyRentUnavailable(f'{book.path.name}/{sheet}：未找到字段{required}')


def as_date(value):
    if value is None or str(value).strip() == '': return None
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    if isinstance(value, (Decimal, int, float)):
        try: return from_excel(float(value)).date()
        except (ValueError, OverflowError, AttributeError): pass
    text = str(value).strip()
    match = re.match(r'(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})', text)
    if match:
        try: return date(*map(int, match.groups()))
        except ValueError: pass
    raise DataIssue(f'日期无法识别：{value}')


def discover(root):
    database = root / DATABASE_FOLDER
    def unique(token):
        hits = [p for p in database.glob('*.xlsx') if token in p.name and not p.name.startswith('~$')]
        if len(hits) != 1: raise DataIssue(f'基准文件匹配不唯一：{token}，{[p.name for p in hits]}')
        return hits[0]
    months = {}
    for p in (root / MONTHLY_FOLDER).glob('*.xlsx'):
        m = re.match(r'(\d{1,2})月账单_', p.name)
        if not m: continue
        no = int(m[1])
        if no in months: raise DataIssue(f'{no}月存在多个文件，请在{MONTHLY_FOLDER}目录仅保留当期使用的版本')
        months[no] = p
    return unique('（新表）万村尾期支付申请'), unique('付款申请'), months


def normalize_id(value):
    """统一Excel文本ID的大小写、首尾空白及不可见字符。"""
    return str(value or '').replace('\u200b', '').replace('\ufeff', '').strip().strip('{}').lower()


def same_id(value, expected):
    return bool(normalize_id(value)) and normalize_id(value) == normalize_id(expected)


def building_aliases(book):
    path = Path(book.path).parent.parent / '输出指引' / '楼栋别名映射.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def resolve_application(book, args, rules):
    """任务：精确确定楼栋和申请。操作：先关联表，再按反馈时间取最新记录。"""
    h, rows = table(book, '关联表', ['id', 'building'])
    id_i, p_i, b_i = [index(h, ALIASES[k]) for k in ['id', 'project', 'building']]
    wanted = args.building_name
    aliases = building_aliases(book)
    candidates = {}
    for _, r, _ in rows:
        bid, project, building = map(str, [cell(r, id_i) or '', cell(r, p_i) or '', cell(r, b_i) or ''])
        bid = normalize_id(bid)
        if not bid: continue
        if args.building_id:
            if not same_id(bid, args.building_id): continue
        else:
            if args.project_name and name_key(project) != name_key(args.project_name): continue
            names = [building, project + building] + aliases.get(bid, {}).get('names', [])
            if wanted and name_key(wanted) not in [name_key(x) for x in names]: continue
        if args.building_id or wanted: candidates[bid] = (project, building)
    if len(candidates) != 1:
        raise DataIssue(f'楼栋匹配{len(candidates)}条，请填写唯一楼栋ID或完整项目名和楼栋名')
    bid, (project, building) = next(iter(candidates.items()))
    alias = aliases.get(bid, {})
    building = alias.get('canonical_name', building)
    known_names = {name_key(project + building)}
    known_names.update(name_key(project + name) for name in alias.get('names', []))
    sheet = '（新）尾期账单支付申请'
    rows = book.rows(sheet)
    h = rows[0][1]
    ii, ni = index(h, ['楼栋号']), index(h, ['楼栋名 （填小泊系统楼栋名）'])
    hits = []
    for n, r, _ in rows[1:]:
        if same_id(cell(r, ii), bid) or (not normalize_id(cell(r, ii)) and name_key(cell(r, ni)) in known_names):
            feedback = as_date(cell(r, index(h, ['反馈时间'])))
            hits.append((feedback or date.min, n, r))
    if args.application_row:
        hits = [x for x in hits if x[1] == args.application_row]
    if not hits: raise DataIssue(f'{project} {building}：新尾期申请中没有匹配记录')
    newest = max(x[0] for x in hits)
    latest = [x for x in hits if x[0] == newest]
    keys = ['楼栋退出月份', '退出形式', '公式 3']
    if len({tuple(str(cell(x[2], index(h, [k]))) for k in keys) for x in latest}) > 1:
        raise DataIssue('同日最新申请的退出/付款信息冲突，请使用--application-row指定申请行')
    _, row_no, r = latest[-1]
    paid = rules.parse_tail_paid(cell(r, index(h, ['公式 3'])))
    if paid is None: raise DataIssue(f'{sheet}!Z{row_no}：公式3为空或异常')
    exit_value = str(cell(r, index(h, ['楼栋退出月份'])) or '')
    m = re.search(r'(\d{1,2})月', exit_value)
    if not m: raise DataIssue(f'退出月份无法读取：{exit_value}')
    exit_no = int(m[1])
    year = args.year or (newest.year if newest != date.min else None)
    if not year: raise DataIssue('缺少年份，请使用--year')
    if not args.year and exit_no > newest.month:
        year -= 1
    exit_month = date(year, exit_no, 1)
    mode = str(cell(r, index(h, ['退出形式'])) or '')
    if mode not in ('免租', '赔偿'): raise DataIssue(f'退出形式无法确定：{mode}')
    notes = '\n'.join(dict.fromkeys(str(cell(x[2], index(h, ['备注内容'])) or '') for x in latest))
    if any(not normalize_id(cell(x[2], ii)) for x in latest):
        notes += '\n楼栋识别：申请行ID为空，按同项目名称/已确认别名关联至ID '+bid
    return dict(building_id=bid, project=project, building=building, tail_paid=paid,
                exit_date=exit_month, exit_type='rent_free' if mode == '免租' else 'compensation',
                remarks=notes, building_aliases=alias.get('names', [building]), application_rows=[x[1] for x in latest], application_sheet=sheet)


def get_summary(book, month, bid, rules, use_delayed=False):
    name = SUMMARY_NAMES.get(month, '汇总表')
    h, rows = table(book, name, ['id', 'lease', 'comp'])
    ii = index(h, ALIASES['id'])
    hits = [(n, r, f) for n, r, f in rows if same_id(cell(r, ii), bid)]
    if not hits: raise MonthlyRentUnavailable(f'{book.path.name}/{name}：未取得该楼栋汇总')
    if len(hits) != 1: raise DataIssue(f'{book.path.name}/{name}：楼栋汇总匹配{len(hits)}条')
    n, r, formulas = hits[0]
    out = {'file': str(book.path), 'sheet': name, 'row': n}
    for key in ('lease', 'comp', 'due'):
        # 核准月表主汇总字段位于左侧；右侧可能有同名更新/调整列。
        # 如7月F/G为主汇总，X/Y为延迟更新，主汇总公式负责引用调整。
        matches = [i for i,v in enumerate(h) if clean(v) in {clean(a) for a in ALIASES[key]}]
        if not matches: raise MonthlyRentUnavailable(f'{name}：缺少{key}')
        i = matches[0]
        try: out[key] = rules.amount(cell(r, i))
        except ValueError as exc:
            issue = MonthlyRentUnavailable if cell(r,i) in (None,'') else DataIssue
            raise issue(f'{book.path.name}/{name}!{get_column_letter(i+1)}{n}：{exc}') from exc
        out[key+'_cell'] = f'{get_column_letter(i+1)}{n}'
        out[key+'_formula'] = formulas.get(i, '')
    # 含延迟支付的月表会同时保留普通汇总和延迟汇总。延迟明细必须与
    # “是否延迟支付”标记之后的租赁收入/赔偿金核对，不能和左侧普通汇总混比。
    markers = [i for i, value in enumerate(h) if clean(value) == clean('是否延迟支付')]
    if use_delayed and markers:
        marker = markers[0]
        for key in ('lease', 'comp'):
            matches = [i for i, value in enumerate(h) if i > marker and clean(value) in {clean(a) for a in ALIASES[key]}]
            if matches:
                i = matches[0]
                try: out['delayed_'+key] = rules.amount(cell(r, i))
                except ValueError as exc:
                    issue = MonthlyRentUnavailable if cell(r,i) in (None,'') else DataIssue
                    raise issue(f'{book.path.name}/{name}!{get_column_letter(i+1)}{n}：延迟汇总{key}：{exc}') from exc
                out['delayed_'+key+'_cell'] = f'{get_column_letter(i+1)}{n}'
                out['delayed_'+key+'_formula'] = formulas.get(i, '')
    return out


def extract_details(book, month, bid, rules, override=None, prefer_delay=False):
    """非延迟只读普通页；延迟只读专用页，多份匹配要求显式指定。"""
    regular = DETAIL_NAMES.get(month, '明细表')
    special_names = [n for n in book.sheets if any(t in n for t in ['延迟支付','延迟付款','延迟出账','最新调整','账单调整']) and n != regular]
    if override and ((not prefer_delay and override != regular) or (prefer_delay and override not in special_names)):
        raise DataIssue('指定明细页与延迟状态不符：非延迟只能使用普通页，延迟只能使用专用延迟/调整页')
    names = [override] if override else (special_names if prefer_delay else [regular])
    found = {}
    for name in names:
        if name not in book.sheets:
            if override: raise DataIssue(f'指定明细页不存在：{name}')
            continue
        try: h, rows = table(book, name, ['id', 'room', 'lease', 'comp'])
        except DataIssue:
            if override: raise
            continue
        cols = {}
        for k,a in ALIASES.items():
            if k=='due': continue
            if k in ('lease','comp'):
                cols[k]=next(i for i,v in enumerate(h) if clean(v) in {clean(x) for x in a})
            else: cols[k]=index(h,a,required=k in ('id','room'))
        matched = []
        for n, r, f in rows:
            if not same_id(cell(r, cols['id']), bid): continue
            item = {k: cell(r, i) for k,i in cols.items()}
            if item['room'] is None: raise DataIssue(f'{name}第{n}行缺房间号')
            for key in ('lease','comp'):
                # 明细某一费用为空、且不是无缓存公式：按该费用未发生处理。
                # 汇总一致性检查仍验证全部明细，避免少计费用。
                if item[key] in (None,'') and cols[key] not in f: item[key] = Decimal(0)
                try: item[key] = rules.amount(item[key])
                except ValueError as exc:
                    issue = MonthlyRentUnavailable if item[key] in (None,'') else DataIssue
                    raise issue(f'{name}第{n}行{key}金额缺失或异常') from exc
            item.update(file=str(book.path), sheet=name, row=n,
                        lease_formula=f.get(cols['lease'], ''), comp_formula=f.get(cols['comp'], ''))
            matched.append(item)
        if matched: found[name] = matched
    special = [n for n in found if n != regular]
    if len(special)>1: raise DataIssue(f'{book.path.name}：多个延迟/调整页含该楼栋{special}，用--detail-sheet 月份=工作表名指定')
    selected = special[0] if special else regular
    if override: selected = override
    if prefer_delay and not special and not override:
        raise MonthlyRentUnavailable(f'{book.path.name}：该月计划为延迟，但未找到该楼栋专用延迟明细')
    return found.get(selected, [])


def previous_month(d):
    return date(d.year-1,12,1) if d.month==1 else date(d.year,d.month-1,1)


def detail_identity(row):
    dates = [as_date(row.get(k)) for k in ('begin', 'end')]
    return (str(row.get('room') or ''), *dates,
            Decimal(str(row['lease'])), Decimal(str(row['comp'])))


def remove_previous(current, previous):
    """按房间、合同、金额匹配前月延迟记录并扣除，保留重复记录次数。"""
    counts = Counter(detail_identity(r) for r in previous)
    output = []
    for r in current:
        key = detail_identity(r)
        if counts[key]: counts[key]-=1
        else: output.append(r)
    if any(counts.values()):
        raise DataIssue('X-1延迟明细无法在X月逐笔匹配，不能安全剔除；请核实明细版本或人工提供已确认差额明细')
    return output


def split_current_bill_by_previous_month(rows, cutoff, include_cutoff=False):
    """区分X月账单中的实际X-1收入；可控制是否把23日当天纳入。"""
    previous, remaining = [], []
    for row in rows:
        paid_at = as_date(row.get('paid_at'))
        belongs_to_previous = paid_at is not None and (
            paid_at <= cutoff if include_cutoff else paid_at < cutoff)
        if belongs_to_previous:
            previous.append(row)
        else:
            # 日期缺失按已确认口径不擅自剔除。
            remaining.append(row)
    return previous, remaining


def write_output(template, output, app, details, status, trace):
    """保留指定模板A:K布局，只呈现租金；汇总公式连接实际明细。"""
    if output.exists(): raise DataIssue(f'输出已存在，请更换--output路径：{output}')
    wb = load_workbook(template, keep_links=False)
    chosen = 'xx(项目名)_xx(楼栋名)'
    if chosen not in wb: raise DataIssue(f'模板缺少{chosen}')
    for name in list(wb.sheetnames):
        if name != chosen: del wb[name]
    ws = wb[chosen]
    ws.title = re.sub(r'[\\/*?:\[\]]', '_', app['project']+'_'+app['building'])[:31]
    # 模板含约1.3万行占位；只保留表头及一行明细样式。
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row >= 10 or merged.max_col > 11: ws.unmerge_cells(str(merged))
    styles = [copy(ws.cell(10,c)._style) for c in range(1,12)]
    ws.delete_rows(11, max(ws.max_row-10,0))
    for row in ws.iter_rows():
        for c in row:
            if c.column > 11 or c.row >= 10: c.value=None
    ws['A1'] = app['project']+' '+app['building']+' 租金对账单'
    ws['A2'] = f"退出月份：{app['exit_date']:%Y年%m月}；仅租金部分"
    ws['A3'] = '所属门店及楼栋：'+app['project']+' '+app['building']
    ws['A4'] = '一、租赁收入及赔偿金汇总'
    ws['A6'],ws['B6'] = app['project'],app['building']
    ws['C5'] = '明细涉及房间数'
    ws['C6'] = len({str(r['room']) for r in details})
    last = max(10,9+len(details))
    ws['D6'],ws['E6'] = f'=SUM(F10:F{last})',f'=SUM(G10:G{last})'
    for c in ['F5','G5','H5','F6','G6','H6','I9','J9']: ws[c]=None
    for row in ws.iter_rows(min_row=5,max_row=6,min_col=6,max_col=8):
        for c in row: c.fill=PatternFill(); c.border=Border()
    for coord in ('D6','E6'): ws[coord].number_format='#,##0.00;[Red]-#,##0.00'
    # F/G承载房间明细金额，不能整列隐藏；仅隐藏无关的水电明细I/J列。
    ws.column_dimensions['I'].hidden=True
    ws.column_dimensions['J'].hidden=True
    ws['K9']='账单月份/内容'
    ws['K9']._style=copy(ws['H9']._style)
    for n,r in enumerate(details,10):
        values=[app['project'],app['building'],str(r['room']),r.get('begin'),r.get('end'),float(r['lease']),float(r['comp']),r.get('remark'),None,None,r['month']+'租金：'+r.get('reason','')]
        for i,v in enumerate(values,1):
            c=ws.cell(n,i); c._style=copy(styles[i-1]); c.value=float(v) if isinstance(v,Decimal) else v
            c.alignment=Alignment(vertical='center',wrap_text=True)
        for i in (4,5):
            try:
                v=as_date(values[i-1]); ws.cell(n,i).value=v; ws.cell(n,i).number_format='yyyy-mm-dd'
            except DataIssue: pass
        for i in (6,7): ws.cell(n,i).number_format='#,##0.00;[Red]-#,##0.00'
        ws.row_dimensions[n].height=38
    ws['K6']='；'.join([status]+app.get('rent_notes',[]))
    ws['K6'].alignment=Alignment(wrap_text=True,vertical='center')
    ws.column_dimensions['K'].width=35
    ws.row_dimensions[6].height=max(38, 15 * ((len(str(ws['K6'].value)) + 15) // 16))
    ws.print_area=f'A1:K{last}'
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
    ws.print_title_rows='1:9'; ws.freeze_panes='D10'; ws.sheet_view.showGridLines=False
    audit=wb.create_sheet('取数记录')
    audit.append(['阶段','月份','结果','来源文件','工作表/位置','源公式或说明'])
    for record in trace: audit.append([str(x) if isinstance(x,(Decimal,Path)) else x for x in record])
    for r in details:
        audit.append(['房间明细',r['month'],str(r['room']),Path(r['file']).name,f"{r['sheet']}!第{r['row']}行",r.get('lease_formula','')+' '+r.get('comp_formula','')])
    for c in audit[1]: c.fill=PatternFill('solid',fgColor='1F4E78'); c.font=Font(color='FFFFFF',bold=True)
    for i,width in enumerate([16,14,40,55,45,70],1): audit.column_dimensions[get_column_letter(i)].width=width
    for row in audit.iter_rows(min_row=2):
        for c in row: c.alignment=Alignment(wrap_text=True,vertical='top')
        audit.row_dimensions[row[0].row].height=32
    audit.freeze_panes='A2'; audit.auto_filter.ref=audit.dimensions
    output.parent.mkdir(parents=True,exist_ok=True)
    wb.save(output); wb.close()
    # openpyxl不计算公式，给汇总SUM写入独立计算的缓存供预览软件读取。
    totals={'D6':sum((r['lease'] for r in details),Decimal(0)), 'E6':sum((r['comp'] for r in details),Decimal(0))}
    with zipfile.ZipFile(output) as z:
        contents={n:z.read(n) for n in z.namelist()}
    xml=ET.fromstring(contents['xl/worksheets/sheet1.xml'])
    for c in xml.iter(NS+'c'):
        if c.attrib.get('r') in totals:
            v=c.find(NS+'v')
            if v is None: v=ET.SubElement(c,NS+'v')
            v.text=str(totals[c.attrib['r']])
    contents['xl/worksheets/sheet1.xml']=ET.tostring(xml,encoding='utf-8')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for n,v in contents.items(): z.writestr(n,v)


def generate(args, rules):
    """主流程：自动取数→历史核对→选明细→租金模板输出。"""
    root=Path(args.root).resolve()
    books=[]; trace=[]; app=None
    output=Path(args.output).resolve() if args.output else None
    try:
        app_path,history_path,month_paths=discover(root)
        ab=SourceBook(app_path); books.append(ab)
        app=resolve_application(ab,args,rules)
        if output is None:
            filename=re.sub(r'[\\/*?:"<>|]', '_',app['project']+'_'+app['building'])
            output=root/'租金输出'/f'{filename}_{datetime.now():%Y%m%d_%H%M%S}_租金对账单.xlsx'
        trace.append(['申请','',app['remarks'],app_path.name,app['application_sheet']+' 行'+str(app['application_rows']),'公式3='+str(app['tail_paid'])])
        if app['tail_paid']:
            # 自由备注不能用关键词冒充完整语义。可显式给出经业务判断的诉求。
            claim=args.claim_type
            if claim is None and not app['remarks'].strip(): claim='none'
            if claim is None: raise DataIssue('尾期已付且备注非空，请阅读诊断中的备注后指定--claim-type none/top_up/enterprise；不自动把关键词当补付结论')
            if claim=='none':
                write_output(root/'输出指引/模板文件_尾期对账单输出.xlsx',output,app,[], '已完成尾期支付，本次无需支付',trace)
                return {'output':str(output),'status':'已完成尾期支付','detail_rows':0}
            if not args.claim_details: raise DataIssue('补付/企业账单：请提供--claim-details已确认差额明细JSON（房间、租赁收入、赔偿金及来源），不能把含水电代管的总付款差额直接作为租金')
            provided=json.loads(Path(args.claim_details).read_text(encoding='utf-8-sig'))
            if not isinstance(provided,list) or not provided: raise DataIssue('补付明细必须为非空列表')
            details=[]
            for n,r in enumerate(provided,1):
                if not all(k in r for k in ['room','lease_income','compensation','month','source']): raise DataIssue('补付明细缺字段：room/lease_income/compensation/month/source')
                details.append(dict(room=r['room'],lease=rules.amount(r['lease_income']),comp=rules.amount(r['compensation']),month=r['month'],remark=r.get('remark',''),reason=claim,file=str(r['source']),sheet='人工确认补付明细',row=n))
            write_output(root/'输出指引/模板文件_尾期对账单输出.xlsx',output,app,details,'按确认的补付明细输出',trace)
            return {'output':str(output),'status':'补付明细已输出','detail_rows':len(details)}
        hb=SourceBook(history_path); books.append(hb)
        hs=rules.select_history_sheet(list(hb.sheets))
        h,rows=table(hb,hs,['id'])
        ii=index(h,ALIASES['id'])
        hits=[(n,r,f) for n,r,f in rows if same_id(cell(r,ii),app['building_id'])]
        if len(hits)!=1: raise DataIssue(f'{hs}：历史付款楼栋匹配{len(hits)}条')
        hn,hr,hf=hits[0]
        x=app['exit_date']; prior=previous_month(x)
        years={int(y) for p in month_paths.values() for y in re.findall(r'(20\d{2})\d{4}',p.name)}
        if years and years!={x.year}: raise DataIssue(f'退出年{x.year}与月度账单文件年份{years}不一致')
        last_month=max([x.month]+list(month_paths))
        unavailable=set()
        app['rent_notes']=[]
        def missing(no, reason):
            if no not in unavailable:
                note=f'{x.year}-{no:02d}月度租金未取得，本次未计入；{reason}'
                app['rent_notes'].append(note)
                trace.append(['租金缺失备注',f'{x.year}-{no:02d}',note,'','','未视为已核实的0元'])
                if no==x.month:
                    trace.append(['高风险-退出月租金缺失',f'{x.year}-{no:02d}',
                                  f'{no}月为楼栋退出月份，月度租金未取得，按0计入并待人工核对；{reason}',
                                  '', '', '退出月份由尾期申请反馈时间确定'])
                unavailable.add(no)
        summaries={}; monthly={}; delays={}; paid={}
        for no in range(1,last_month+1):
            if app['exit_type']=='rent_free' and no>x.month: continue
            delays[no]=rules.month_delayed(h,hr,no)
            p=month_paths.get(no)
            if p is None:
                missing(no,'缺少月度账单文件')
                continue
            try:
                b=SourceBook(p); books.append(b); monthly[no]=b
                summaries[no]=get_summary(b,no,app['building_id'],rules)
            except (MonthlyRentUnavailable, FileNotFoundError) as exc:
                missing(no,str(exc))
        for no in range(1,x.month):
            try:
                payment=rules.history_payment(h,hr,no,hf)
            except ValueError as exc:
                raise DataIssue(f'{history_path.name}/{hs}!第{hn}行：{no}月历史付款取数异常：{exc}') from exc
            paid[no]=payment['amount']
            delays[no]=rules.month_delayed(h,hr,no)
            coords=','.join(f'{get_column_letter(j+1)}{hn}' for j in [payment['amount_column']]+payment['document_columns'])
            trace.append(['历史付款',f'{x.year}-{no:02d}',str(paid[no]),history_path.name,f'{hs}!{coords}',payment['note']+f'；延迟={delays[no]}'])
            if delays[no] is None: raise DataIssue(f'{no}月全部计划字段空白，无法判断是否延迟')
            if no<x.month-1 and delays[no] and paid[no]==0: raise DataIssue(f'{no}月为X-2或更早延迟且未支付')
            if no in unavailable:
                continue
            s=summaries[no]
            trace.append(['月度应付',f'{x.year}-{no:02d}',str(s['due']),Path(s['file']).name,s['sheet']+'!'+s['due_cell'],s['due_formula']])
            if paid[no]!=0 and paid[no]!=s['due']:
                difference=paid[no]-s['due']
                if not rules.history_difference_allowed(paid[no],s['due']):
                    trace.append(['高风险-历史付款差异',f'{x.year}-{no:02d}',str(difference),history_path.name,
                                  f'{hs}!行{hn}',f'{no}月实付{paid[no]}，应付{s["due"]}，差额{difference}（绝对值超过1元），继续后续核对'])
                    continue
                trace.append(['四舍五入漏损',f'{x.year}-{no:02d}',str(difference),history_path.name,
                              f'{hs}!行{hn}',f'实付{paid[no]}；楼栋分成收益{s["due"]}；差额绝对值<=1元，放行且不调整原金额'])
        overrides={}
        for raw in args.detail_sheet or []:
            no,sep,name=raw.partition('=')
            if not sep: raise DataIssue('--detail-sheet格式为 月份=工作表名')
            overrides[int(no)]=name
        detail_cache={}
        def get_checked(no):
            if no not in detail_cache:
                if delays.get(no,False):
                    summaries[no]=get_summary(monthly[no],no,app['building_id'],rules,use_delayed=True)
                s=summaries[no]
                rs=extract_details(monthly[no],no,app['building_id'],rules,overrides.get(no),delays.get(no,False))
                if not rs: raise MonthlyRentUnavailable(f'{no}月未取得对应房间明细')
                lease=sum((r['lease'] for r in rs),Decimal(0)); comp=sum((r['comp'] for r in rs),Decimal(0))
                use_delayed = bool(delays.get(no,False))
                if use_delayed and ('delayed_lease' not in s or 'delayed_comp' not in s):
                    raise MonthlyRentUnavailable(f'{no}月使用延迟明细，但汇总表缺少“是否延迟支付”之后的租赁收入/赔偿金字段')
                expected_lease=s['delayed_lease'] if use_delayed else s['lease']
                expected_comp=s['delayed_comp'] if use_delayed else s['comp']
                if lease!=expected_lease or comp!=expected_comp:
                    basis='延迟汇总' if use_delayed else '普通汇总'
                    raise DataIssue(f'{no}月源明细与{basis}租金/赔偿金不一致：明细{lease}/{comp}，{basis}{expected_lease}/{expected_comp}；需核实调整项或明细版本')
                if use_delayed:
                    trace.append(['延迟汇总核对',f'{x.year}-{no:02d}',f'{lease}/{comp}',Path(s['file']).name,
                                  s['sheet']+'!'+s['delayed_lease_cell']+','+s['delayed_comp_cell'],'延迟明细与延迟汇总一致'])
                detail_cache[no]=rs
            return [dict(r) for r in detail_cache[no]]
        def get(no):
            if no in unavailable: return []
            try:
                return get_checked(no)
            except MonthlyRentUnavailable as exc:
                missing(no,str(exc))
                return []
        details=[]
        def append(no,rs,reason):
            if no in unavailable: return
            for r in rs: details.append(dict(r,month=f'{x.year}-{no:02d}',reason=reason))
            trace.append(['选择明细',f'{x.year}-{no:02d}',f'{len(rs)}条',month_paths[no].name,'',reason])
        for no,value in paid.items():
            if value==0: append(no,get(no),'历史实付为0')
        prior_delayed=delays.get(x.month-1,False)
        if app['exit_type']=='rent_free' and prior_delayed:
            trace.append(['末月',f'{x:%Y-%m}','不再追加末月明细','','','免租且X-1月延迟'])
        else:
            rs=get(x.month)
            cutoff=prior.replace(day=23)
            if app['exit_type']=='rent_free':
                previous_rows,_=split_current_bill_by_previous_month(rs,cutoff)
                missing_rows=[r for r in rs if as_date(r.get('paid_at')) is None]
                append(x.month,previous_rows+missing_rows,'免租：保留前月23日前收入；23日当天不含；日期缺失保留')
            elif prior_delayed:
                removed,rs=split_current_bill_by_previous_month(rs,cutoff,include_cutoff=True)
                trace.append(['剔除X-1实际收入',f'{x:%Y-%m}',
                              f'{len(removed)}条；租赁收入{sum((r["lease"] for r in removed),Decimal(0))}；赔偿金{sum((r["comp"] for r in removed),Decimal(0))}',
                              month_paths[x.month].name if x.month in month_paths else '',removed[0]['sheet'] if removed else '',
                              f'支付日期<={cutoff:%Y-%m-%d}；包含23日当天'])
                append(x.month,rs,'赔偿退出且X-1延迟：剔除X月中实际X-1收入')
            else: append(x.month,rs,'赔偿退出末月')
        if app['exit_type']=='compensation':
            for no in range(x.month+1,last_month+1):
                if no in unavailable: continue
                s=summaries[no]; total=s['lease']+s['comp']
                if total>0: trace.append(['后续月排除',f'{x.year}-{no:02d}',str(total),month_paths[no].name,s['sheet'],'租赁收入＋赔偿金>0'])
                else: append(no,get(no),'后续月租赁收入＋赔偿金<=0')
        write_output(root/'输出指引/模板文件_尾期对账单输出.xlsx',output,app,details,'租金部分已生成',trace)
        return {'output':str(output),'status':'租金部分已生成','detail_rows':len(details),
                'notes':app['rent_notes'],
                'lease_income':str(sum((r['lease'] for r in details),Decimal(0))),
                'compensation':str(sum((r['comp'] for r in details),Decimal(0)))}
    except (DataIssue,ValueError,KeyError) as exc:
        target=(output.with_suffix('.诊断.json') if output else root/'租金输出'/f'取数异常_{datetime.now():%Y%m%d_%H%M%S}.json')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'error':str(exc),'application':app,'trace':trace},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        raise DataIssue(f'{exc}\n诊断文件：{target}') from exc
    finally:
        for book in books: book.close()
