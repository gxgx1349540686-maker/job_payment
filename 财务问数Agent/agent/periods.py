import re


def parse_months(question,year):
    if re.search(r'上月|本月|去年|今年|最近|季度|半年|截至|年初|年末',question):
        raise ValueError('请使用明确年月或起止月份，例如2026年1月至9月；未提供日期则查全部已存日期。')
    if re.search(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d月\d+[日号]|按实际付款日期|按付款日期',question):
        raise ValueError('本版按费用归属/来源单据月份比较，请明确账期；不把付款日期当作费用月份。')
    question=re.sub(r'(\d{1,2})(至|到|—|~|～|-)(\d{1,2})月',r'\1月\2\3月',question)
    months=[];inferred=False;current_year=year
    for m in re.finditer(r'(?:(20\d{2})\s*(?:年|[-/])\s*)?(\d{1,2})\s*月|(?<!\d)(20\d{2})[-/]?(0[1-9]|1[0-2])(?!\d)',question):
        y,mm,y2,mm2=m.groups()
        if y or y2:current_year=int(y or y2)
        elif not months:inferred=True
        month=int(mm or mm2)
        if month<1 or month>12:raise ValueError('月份必须在1至12之间。')
        months.append(f'{current_year}{month:02d}')
    if len(months)>2 or len(months)==2 and not re.search(r'至|到|—|~|～|\d月\s*-',question):raise ValueError('请使用一个月份或连续起止月份。')
    if months:
        if months[0]>months[-1]:raise ValueError('起始月份不能晚于结束月份。')
        return months[0],months[-1],f'未注明年份的月份按资料年份{year}年理解。' if inferred else ''
    years=re.findall(r'(20\d{2})年',question)
    if years:
        if len(set(years))!=1:raise ValueError('跨年查询请填写明确起止月份。')
        return years[0]+'01',years[0]+'12',''
    return None,None,''
