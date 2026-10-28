import asyncio
from types import SimpleNamespace
import threading
import pytest
from agent.provider import Provider
from agent.streaming import EventStream
from agent.reporting import financial_report
from agent.r35_database import R35Database
from agent.settings import Settings


def test_provider_one_request_no_retry_and_no_result_rows_sent(monkeypatch):
    import httpx
    captured={};calls=[]
    class MockClient:
        def __init__(self,**kwargs):captured['client']=kwargs
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append((url,json))
            return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'finish_reason':'stop','message':{'content':'{"scope_type":"project","project":"石厦公社"}'}}]})
    monkeypatch.setattr(httpx,'Client',MockClient)
    monkeypatch.setattr(httpx,'HTTPTransport',lambda **kwargs:captured.update(kwargs))
    settings=SimpleNamespace(key=lambda:'dummy-test-not-a-real-key',base_url='https://example.invalid',model='mock')
    assert Provider(settings).parse('项目费用','只提取json')['scope_type']=='project'
    assert len(calls)==1 and captured['retries']==0
    assert [m['content'] for m in calls[0][1]['messages']]==['只提取json','项目费用']


def test_stream_progress_arrives_before_result():
    async def scenario():
        release=threading.Event()
        class Fake:
            def chat(self,payload,on_event,cancelled):
                on_event('analysis',{'title':'解析','text':'项目月度查询'})
                release.wait(2)
                return {'status':'answered','answer':'模拟金额结果'}
        stream=EventStream(lambda:Fake(),{});g=stream.iterate()
        try:
            assert 'connected' in await anext(g)
            assert 'analysis' in await asyncio.wait_for(anext(g),1)
            release.set();rest=[]
            async for chunk in g:rest.append(chunk)
            assert any('event: done' in chunk for chunk in rest)
        finally:release.set();await g.aclose()
    asyncio.run(scenario())


def test_disconnect_never_retries():
    async def scenario():
        release=threading.Event();calls=[]
        class Fake:
            def chat(self,payload,on_event,cancelled):
                calls.append(1);on_event('analysis',{'title':'解析','text':'已开始'});release.wait(2)
                return {'status':'answered','answer':'模拟'}
        stream=EventStream(lambda:Fake(),{});g=stream.iterate()
        await anext(g);await anext(g);await g.aclose();release.set()
        assert stream.cancelled.is_set() and await asyncio.to_thread(stream.worker_finished.wait,2)
        assert calls==[1]
    asyncio.run(scenario())


def test_raw_key_file_never_needed_for_status_metadata(tmp_path):
    key=tmp_path/'key.txt';key.write_text('dummy-test-value')
    settings=Settings.load();settings.api_key_file=key
    assert settings.key_configured()  # 只检查非空文件；autouse已禁止读取Settings.key。


def test_contract_breakdown_does_not_duplicate_whole_contract_payment():
    scope={'kind':'contract','contract_id':'c','dimension_keys':['a','b'],'label':'c'}
    monthly=[{'dimension_key':key,'month_id':'202601','receipts_cents':10000,'receivable_cents':0,'payable_cents':10000,'adjustment_cents':0,'reserve_cents':0,'water_cents':0,'electricity_cents':0} for key in ['a','b']]
    executions=[{'dimension_key':key,'month_id':'202601','contract_id':'c'} for key in ['a','b']]
    payments=[{'document_id':'p','contract_id':'c','month_id':'202601','amount_yuan':'50','status':'支付成功'}]
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a','b'}}},by_dimension={key:{'project':'项目','building':key} for key in ['a','b']})
    rows,total=financial_report(scope,monthly,executions,payments,index,breakdown=True)
    assert all(r['difference_yuan'] is None and r['unallocated_payment_count']==1 for r in rows)
    assert total['paid_yuan']=='50.00' and total['difference_yuan']=='150.00'
