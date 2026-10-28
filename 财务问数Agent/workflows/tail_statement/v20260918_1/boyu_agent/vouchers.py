"""申请行无图片时读取补充凭证；金额仍由原业务规则核验。"""
import hashlib
import io
import re
import textwrap
from pathlib import Path

FOLDER = '每次付款后修改_持续更新数据库/需更新_楼栋尾期账单水电费取数表/0909水电划扣凭证/0909水电划扣凭证/其他凭证'


def name_match(text, name):
    text = re.sub(r'\s+', '', str(text)).lower()
    name = re.sub(r'\s+', '', str(name)).lower()
    name = re.sub(r'(栋|号)$', '', name)
    return bool(name) and bool(re.search(r'(?<![0-9a-z])'+re.escape(name)+r'(?![0-9a-z-])', text))


def project_match(text, project):
    return str(project).replace(' ', '').lower() in str(text).replace(' ', '').lower()


def table_rows(path):
    import zipfile
    if zipfile.is_zipfile(path):
        from openpyxl import load_workbook
        # 部分文件扩展名为xls但实际上是xlsx。
        with path.open('rb') as stream:
            book = load_workbook(stream, read_only=True, data_only=True)
            try:
                return [(sheet.title, list(sheet.values)) for sheet in book]
            finally: book.close()
    import xlrd
    book = xlrd.open_workbook(str(path))
    try:
        return [(sheet.name, [sheet.row_values(i) for i in range(sheet.nrows)]) for sheet in book.sheets()]
    finally: book.release_resources()


def render_table(rows, title):
    """解析单元格后分页生成凭证图片，避免长表在凭证区缩成不可读内容。"""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 20)
    lines = textwrap.wrap(title, 48) + ['单元格数据（按原表列顺序；竖线分隔）']
    for number, row in rows:
        text = f'行{number}: ' + ' | '.join('' if x is None else str(x) for x in row)
        lines.extend(textwrap.wrap(text, 48) or [''])
    output = []
    for start in range(0, len(lines), 24):
        chunk = lines[start:start+24]
        image = Image.new('RGB', (1100, 40+32*len(chunk)), 'white')
        draw = ImageDraw.Draw(image)
        for i, line in enumerate(chunk): draw.text((15, 15+i*32), line, font=font, fill='black')
        data = io.BytesIO()
        image.save(data, format='PNG')
        output.append({'data': data.getvalue(), 'format': 'png', 'width': image.width,
                       'height': image.height, 'text': '\n'.join(chunk), 'source': title})
    return output


def fallback_vouchers(root, app):
    from PIL import Image
    directory = Path(root)/FOLDER
    images, notes, hashes = [], [], set()
    aliases = list(set([app['building']] + app.get('building_aliases', [])))
    if not directory.exists(): return [], ['补充划扣凭证目录不存在，未核验，待核对']
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.name.startswith('~$'): continue
        relative = str(path.relative_to(directory))
        if not project_match(relative, app['project']): continue
        building_hit = any(name_match(relative, name) for name in aliases)
        if len(set(re.findall(r'泊寓[^、\\/]+', relative))) > 1:
            if building_hit:
                notes.append(f'凭证目录涉及多个项目，无法唯一确认归属：{relative}；待核对')
            continue
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.xlsx', '.xls'): continue
        key = hashlib.sha256(path.read_bytes()).hexdigest()
        if key in hashes: continue
        try:
            if path.suffix.lower() in ('.xlsx', '.xls'):
                selected = []
                for sheet_name, rows in table_rows(path):
                    if building_hit:
                        hits = [(i, row) for i, row in enumerate(rows, 1) if any(x not in (None, '') for x in row)]
                    else:
                        hits = [(i, row) for i, row in enumerate(rows, 1)
                                if any(any(name_match(str(value), name) for name in aliases) for value in row if value is not None)]
                        if hits:
                            headers = [(i, row) for i, row in enumerate(rows[:3], 1) if i < hits[0][0]]
                            hits = headers + hits
                    if hits: selected.extend(render_table(hits, relative+'/'+sheet_name))
                images.extend(selected)
                if selected: hashes.add(key)
            elif building_hit:
                with Image.open(path) as image:
                    image = image.convert('RGB')
                    buffer = io.BytesIO()
                    image.save(buffer, 'PNG')
                    images.append({'data': buffer.getvalue(), 'format': 'png', 'width': image.width,
                                   'height': image.height, 'source': relative})
                hashes.add(key)
        except Exception as exc:
            notes.append(f'外部凭证{relative}解析未完成：{type(exc).__name__}: {exc}；待核对')
    if images: notes.append(f'申请行无凭证，已从补充目录取得{len(hashes)}个去重文件；表格数据分页粘贴至凭证区')
    else: notes.append('申请行无凭证，补充目录未取得项目及楼栋同时匹配的凭证，未核验，待核对')
    return images, notes
