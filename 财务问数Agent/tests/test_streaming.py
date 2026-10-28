import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from agent.settings import Settings
from agent.streaming import EventStream,encode_event
from agent.workflow import Agent,QueryCancelled


class LocalModel:
    enabled=False


@pytest.fixture(scope='module')
def agent():return Agent(Settings.load(),model=LocalModel())


def events_from_text(text):
    result=[]
    for frame in text.split('\n\n'):
        lines=frame.splitlines()
        name=next((line[7:] for line in lines if line.startswith('event: ')),None)
        data=next((line[6:] for line in lines if line.startswith('data: ')),None)
        if name and data:result.append((name,json.loads(data)))
    return result


def test_encode_event_keeps_multiline_text_in_one_data_frame():
    text=encode_event('answer_delta',{'text':'第一行\n第二行'})
    assert events_from_text(text)==[('answer_delta',{'text':'第一行\n第二行'})]


def test_real_workflow_progress_no_sensitive_state(agent):
    events=[]
    response=agent.chat({'message':'应收多少'},on_event=lambda e,d:events.append((e,d)))
    assert response['status']=='clarify_metric'
    progress=[data for event,data in events if event=='progress']
    assert [p['stage'] for p in progress]==['interpret','interpret','clarify','clarify']
    assert all(set(p)=={'stage','label','state'} for p in progress)


def test_cancel_after_first_node_prevents_later_steps(agent):
    cancel=threading.Event();events=[]
    def emit(event,data):
        events.append((event,data))
        if event=='progress' and data['stage']=='interpret' and data['state']=='completed':cancel.set()
    with pytest.raises(QueryCancelled):agent.chat({'message':'应收多少'},on_event=emit,cancelled=cancel)
    assert not any(e=='progress' and d['stage']=='clarify' for e,d in events)


def test_sse_clarification_and_followup_return_validated_result(agent,monkeypatch):
    import app
    monkeypatch.setattr(app,'_agent',agent)
    with TestClient(app.app) as client:
        def post(payload):
            response=client.post('/api/chat/stream',json=payload)
            assert response.status_code==200
            assert response.headers['content-type'].startswith('text/event-stream')
            assert response.headers['x-accel-buffering']=='no'
            events=events_from_text(response.text)
            assert events[0][0]=='connected' and events[-1][0]=='done'
            result=next(d for e,d in events if e=='result')
            assert ''.join(d['text'] for e,d in events if e=='answer_delta')==result['answer']
            return result,events
        one,_=post({'message':'2026年1月至4月应收多少','project':'石厦公社','building':'西村167号'})
        assert one['status']=='clarify_metric'
        two,_=post({'session_id':one['session_id'],'selection':'company_receivable'})
        assert two['status']=='clarify_building'
        choice=next(c for c in two['choices'] if '西村167' in c['label'])
        three,events=post({'session_id':one['session_id'],'selection':choice['id']})
        assert three['status']=='answered'
        assert three['data']['known_amount_total']=='38900.00'
        assert three['data']['period']['end']=='202604'
        assert any(e=='progress' and d['stage']=='execute' and d['state']=='completed' for e,d in events)
        assert len([1 for e,d in events if e=='answer_delta'])>1


def test_stream_initialization_error_is_sanitized(monkeypatch):
    import app
    def fail():raise RuntimeError('do-not-expose-internal-credentials')
    monkeypatch.setattr(app,'get_agent',fail)
    with TestClient(app.app) as client:
        response=client.post('/api/chat/stream',json={'message':'应收多少'})
        events=events_from_text(response.text)
        assert events[-1][0]=='error'
        assert 'do-not-expose' not in response.text
        assert not any(e=='result' for e,d in events)


def test_stream_input_validation_and_origin_still_enforced(agent,monkeypatch):
    import app
    monkeypatch.setattr(app,'_agent',agent)
    with TestClient(app.app) as client:
        assert client.post('/api/chat/stream',json={'message':'x'*2001}).status_code==422
        assert client.post('/api/chat/stream',json={'message':'应收多少'},headers={'Origin':'https://evil.example'}).status_code==403
        response=client.post('/api/chat/stream',json={'selection':'not-in-session'})
        assert events_from_text(response.text)[-1][0]=='error'


def test_connected_and_progress_arrive_before_work_finishes():
    async def scenario():
        release=threading.Event()
        class SlowMock:
            def chat(self,payload,on_event,cancelled):
                on_event('progress',{'stage':'interpret','label':'理解问题','state':'started'})
                release.wait(3)
                return {'answer':'模拟完成','status':'answered'}
        stream=EventStream(lambda:SlowMock(),{})
        generator=stream.iterate()
        try:
            assert 'event: connected' in await asyncio.wait_for(anext(generator),1)
            assert 'event: progress' in await asyncio.wait_for(anext(generator),1)
            assert not release.is_set()  # 实际查询未完成，客户端已收到流事件。
            release.set()
            rest=[]
            async for frame in generator:rest.append(frame)
            assert any('event: result' in frame for frame in rest)
        finally:
            release.set()
            await generator.aclose()
    asyncio.run(scenario())


def test_disconnect_sets_cancellation_without_retry():
    async def scenario():
        release=threading.Event();seen=[]
        class SlowMock:
            def chat(self,payload,on_event,cancelled):
                seen.append('call')
                on_event('progress',{'stage':'interpret','label':'理解问题','state':'started'})
                release.wait(3)
                if cancelled.is_set():seen.append('cancelled')
                return {'answer':'模拟完成','status':'answered'}
        stream=EventStream(lambda:SlowMock(),{})
        generator=stream.iterate()
        await anext(generator)
        await anext(generator)
        await generator.aclose()
        assert stream.cancelled.is_set()
        release.set()
        assert await asyncio.to_thread(stream.worker_finished.wait,2)
        assert seen==['call','cancelled']
    asyncio.run(scenario())
