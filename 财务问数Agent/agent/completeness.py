"""Read QA labels separately; never join them into money facts."""

def explain(scope, rows, contracts, index):
    by_key={}
    for row in rows:
        key=row['dimension_key']
        if key in by_key:raise ValueError('QA楼栋登记出现重复维度，停止展示完整性结论。')
        by_key[key]=row
    contract_keys=set()
    for row in contracts:
        key=(row['dimension_key'],row['contract_id'])
        if key in contract_keys:raise ValueError('QA合同楼栋关系重复。')
        contract_keys.add(key)
    items=[]
    for key in scope['dimension_keys']:
        r=dict(by_key.get(key,{}));dimension=index.by_dimension[key]
        r.update(dimension_key=key,project=dimension['project'],building=dimension['building'])
        complete=(r.get('completion_status')=='已处理完成' and
                  r.get('test_status')=='全部现有单据可测试' and not r.get('completeness_label'))
        r['information_complete']=complete if key in by_key else None
        r['information_status']='信息齐全（QA测试口径）' if complete else '楼栋信息仍不完整' if key in by_key else '尚无完整性登记'
        if key not in by_key:r['completeness_reason']='未找到当前楼栋维度的QA登记，不能推断已齐全。'
        r['contract_statuses']=[c for c in contracts if c['dimension_key']==key]
        items.append(r)
    counts={'complete':sum(r['information_complete'] is True for r in items),
            'incomplete':sum(r['information_complete'] is False for r in items),
            'unknown':sum(r['information_complete'] is None for r in items),'total':len(items)}
    lines=[]
    if len(items)==1:
        r=items[0];lines.append('楼栋资料状态（QA测试口径）：'+r['information_status']+'。')
        lines.append('登记依据：'+r.get('completeness_reason','未登记')+'；覆盖范围：'+(r.get('coverage') or '未登记')+'；登记版本：'+(r.get('registry_version') or '未知')+'。')
    else:
        lines.append(f"楼栋资料状态（QA测试口径）：齐全{counts['complete']}栋，仍不完整{counts['incomplete']}栋，未登记{counts['unknown']}栋；按楼栋分别展示，不将部分通过说成全部齐全。")
    if scope.get('contract_id'):
        lines.append('合同状态按本合同关联的各楼栋分别登记，不代表合同在其他楼栋范围全部完成。')
    lines.append('此为当前登记覆盖范围的QA测试完整性，不随本次查询月份缩小；不等于严格逐月财务全部对应、合同法律有效或欠款已结清。')
    return {'basis':'QA测试完整性；与严格逐月财务对应清单分开','counts':counts,'rows':items,'contract_rows':contracts,'explanation':'\n'.join(lines)}
