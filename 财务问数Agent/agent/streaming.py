"""SSE输出经过校验的阶段进度及答案，不输出未完成的模型JSON、SQL或推理内容。"""
import asyncio
import json
import queue
import threading


def encode_event(name,data):
    return f"event: {name}\ndata: {json.dumps(data,ensure_ascii=False,separators=(',',':'))}\n\n"


class EventStream:
    def __init__(self,agent_factory,payload):
        self.agent_factory=agent_factory
        self.payload=payload
        self.cancelled=threading.Event()
        self.events=queue.Queue(maxsize=64)
        self.worker_finished=threading.Event()

    def emit(self,name,data):
        while not self.cancelled.is_set():
            try:
                self.events.put((name,data),timeout=.1)
                return
            except queue.Full:continue

    def run(self):
        try:
            response=self.agent_factory().chat(self.payload,on_event=self.emit,cancelled=self.cancelled)
            if self.cancelled.is_set():return
            self.emit('answer_start',{})
            # 金额已查询并校验后才分段传输，不以模型未经验证的Token作为财务答案。
            text=response['answer']
            for offset in range(0,len(text),64):
                self.emit('answer_delta',{'text':text[offset:offset+64]})
            self.emit('result',response)
            self.emit('done',{'status':response['status']})
        except ValueError as exc:
            self.emit('error',{'detail':str(exc)})
        except Exception:
            self.emit('error',{'detail':'本轮处理未完成，请检查本地服务后手动重试。'})
        finally:self.worker_finished.set()

    async def iterate(self):
        yield encode_event('connected',{'message':'已连接，正在处理问题。'})
        thread=threading.Thread(target=self.run,name='finance-sse-worker',daemon=True)
        thread.start()
        heartbeat=asyncio.get_running_loop().time()
        try:
            while True:
                try:name,data=self.events.get_nowait()
                except queue.Empty:
                    if self.worker_finished.is_set():break
                    if asyncio.get_running_loop().time()-heartbeat>=10:
                        yield ': heartbeat\n\n'
                        heartbeat=asyncio.get_running_loop().time()
                    await asyncio.sleep(.025)
                    continue
                yield encode_event(name,data)
                heartbeat=asyncio.get_running_loop().time()
                if name in ('done','error'):break
        finally:
            # 连接中断阻止后续节点；已经发出的模型请求无法撤回，也不会自动重发。
            self.cancelled.set()
