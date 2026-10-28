import re
import unicodedata
from rapidfuzz.fuzz import ratio


def norm(value):
    value=unicodedata.normalize('NFKC',str(value or '')).lower().replace('泊寓home','').replace('泊寓','')
    value=re.sub(r'[\s_—·（）()，,。:：/\\-]','',value)
    return re.sub(r'(号|栋|幢|座)$','',value)


def project_aliases(value):
    normalized=norm(value)
    aliases={normalized}
    aliases.add(re.sub(r'^(深圳市|深圳)?(福田|罗湖|南山|盐田|宝安|龙岗|龙华|坪山|光明|大鹏|观澜|坂田|布吉|大浪|西丽|龙城|横岗|平湖|民治|西乡|福永|石岩|沙井|松岗)','',normalized))
    return {a for a in aliases if a}


def name_score(query,aliases,numeric=False):
    q=norm(query)
    if not q:return 0
    scores=[]
    for alias in aliases:
        a=norm(alias)
        if not a:continue
        score=100.0 if q==a else 96.0 if len(q)>=3 and q in a else ratio(q,a)
        if numeric:
            qn=re.findall(r'\d+',q);an=re.findall(r'\d+',a)
            if qn and qn!=an:score=min(score,60)
        scores.append(score)
    return max(scores,default=0)


def select_candidates(candidates):
    ordered=sorted(candidates,key=lambda r:(-r['score'],r['label']))
    high=[row for row in ordered if row['score']>90]
    return high if high else ordered[:5]


class EntityIndex:
    def __init__(self,dimensions,contract_rows,documents):
        self.dimensions=dimensions
        self.by_dimension={r['dimension_key']:r for r in dimensions}
        self.project_names=sorted({r['project'] for r in dimensions})
        self.contracts={}
        for row in contract_rows:
            c=self.contracts.setdefault(row['contract_id'],{'id':row['contract_id'],'names':set(),'rows':[],'dimensions':set()})
            c['names'].add(row['contract_name']);c['rows'].append(row)
            c['dimensions'].update(d['dimension_key'] for d in dimensions if row['building_id'] and d['building_id']==row['building_id'])
        for d in dimensions:
            for cid in re.split(r'[,，;；\s]+',d.get('related_contracts') or ''):
                if cid in self.contracts:self.contracts[cid]['dimensions'].add(d['dimension_key'])
        self.documents={r['document_id']:r for r in documents}
        self.dimension_aliases={d['dimension_key']:{d['building']} for d in dimensions}
        for row in contract_rows:
            for d in dimensions:
                if row['building_id'] and row['building_id']==d['building_id']:self.dimension_aliases[d['dimension_key']].add(row['building'])

    def extract(self,question,project='',building='',entity='',scope_type='auto'):
        cid=re.search(r'NC-[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',question,re.I) or re.search(r'NC-[A-Za-z0-9-]+',question,re.I)
        known_docs=[did for did in self.documents if did.lower() in question.lower()]
        doc=re.search(r'NPS[A-Za-z0-9]+',question,re.I)
        if len(known_docs)==1:return {'scope_type':'document','entity':known_docs[0],'project':project,'building':building}
        if doc:return {'scope_type':'document','entity':doc[0].upper(),'project':project,'building':building}
        if cid:return {'scope_type':'contract','entity':cid[0],'project':project,'building':building}
        q=norm(question)
        if scope_type=='auto' and not entity:
            names=[n for c in self.contracts.values() for n in c['names'] if len(norm(n))>=8 and norm(n) in q]
            if len(set(names))==1:return {'scope_type':'contract','entity':names[0],'project':project,'building':building}
        if not project:
            matches=[p for p in self.project_names if any(len(a)>=4 and a in q for a in project_aliases(p))]
            if len(matches)==1:project=matches[0]
            elif matches:project=question  # 多个明确项目不可擅自选最长名称。
        if not building:
            pool=[d for d in self.dimensions if not project or name_score(project,project_aliases(d['project']))>90]
            clean=norm(re.sub(r'(?:20\d{2}年)?\d{1,2}月|20\d{2}年|(?<!\d)20\d{2}[-/]?\d{2}(?!\d)','',question))
            found=[]
            for d in pool:
                for alias in self.dimension_aliases[d['dimension_key']]:
                    a=norm(alias)
                    if not a or not re.search(r'(?<!\d)'+re.escape(a)+r'(?!\d)',clean):continue
                    if a.isdigit() and not re.search(r'(?<!\d)'+re.escape(a)+r'\s*(号|栋|幢|座)',question):continue
                    if re.search(r'[号栋幢座]',alias) or len(a)>=3:found.append((len(a),alias,d['dimension_key']))
            if found:building=max(found)[1] if len({v[2] for v in found})==1 else question
            else:
                match=re.search(r'([东西南北村新旧上下前后一二三四五六七八九十巷街路0-9A-Za-z\-]+(?:号|栋|幢|座))',question)
                if match:building=match[1]
        if scope_type=='auto':
            scope_type='building' if building else 'project' if project else 'contract' if entity and '合同' in question else 'document' if entity else 'auto'
        return {'scope_type':scope_type,'entity':entity,'project':project,'building':building}

    def candidates(self,scope_type,project='',building='',entity='',allow_high=True):
        results=[]
        if scope_type=='project':
            for name in self.project_names:
                results.append({'id':'p:'+name,'kind':'project','label':name,'score':round(name_score(project or entity,project_aliases(name)),3)})
        elif scope_type=='building':
            for d in self.dimensions:
                ps=name_score(project,project_aliases(d['project'])) if project else 100
                bs=name_score(building or entity,self.dimension_aliases[d['dimension_key']],numeric=True)
                score=min(ps,bs) if project else min(bs,90)  # 缺项目的重名楼栋不自动采用。
                results.append({'id':'b:'+d['dimension_key'],'kind':'building','label':d['project']+' / '+d['building'],'score':round(score,3)})
        elif scope_type=='contract':
            exact=[cid for cid in self.contracts if entity.lower()==cid.lower()]
            if exact and allow_high:return [{'id':'c:'+cid,'kind':'contract','label':cid+' / '+sorted(self.contracts[cid]['names'])[0],'score':100.0} for cid in exact]
            for cid,c in self.contracts.items():
                score=100 if entity.lower()==cid.lower() else name_score(entity,c['names'])
                results.append({'id':'c:'+cid,'kind':'contract','label':cid+' / '+sorted(c['names'])[0],'score':round(score,3)})
        elif scope_type=='document':
            exact=[did for did in self.documents if entity.upper()==did.upper()]
            if exact and allow_high:return [{'id':'d:'+did,'kind':'document','label':did+' / '+str(self.documents[did]['month_id']),'score':100.0} for did in exact]
            for did,d in self.documents.items():
                score=100 if entity.upper()==did.upper() else name_score(entity,[did])
                results.append({'id':'d:'+did,'kind':'document','label':did+' / '+str(d['month_id']),'score':round(score,3)})
        if not allow_high:
            for row in results:row['score']=min(row['score'],90)
        return select_candidates(results)

    def resolve(self,choice):
        token=choice['id'];kind=choice['kind']
        if kind=='project':keys=[d['dimension_key'] for d in self.dimensions if d['project']==token[2:]]
        elif kind=='building':keys=[token[2:]]
        elif kind=='contract':keys=sorted(self.contracts[token[2:]]['dimensions'])
        else:
            doc=self.documents[token[2:]];keys=sorted(self.contracts.get(doc['contract_id'],{}).get('dimensions',[]))
        return dict(choice,dimension_keys=keys,contract_id=token[2:] if kind=='contract' else self.documents[token[2:]]['contract_id'] if kind=='document' else None,
                    document_id=token[2:] if kind=='document' else None)
