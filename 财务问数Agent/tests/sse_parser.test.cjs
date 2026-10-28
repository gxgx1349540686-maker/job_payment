const test=require('node:test');const assert=require('node:assert/strict');
const {consume}=require('../web/sse.js');
function body(parts){return new ReadableStream({start(controller){for(const p of parts)controller.enqueue(p);controller.close();}});}
test('跨字节中文与多条事件正确解析',async()=>{
  const text=': heartbeat\n\nevent: answer_delta\ndata: {"text":"水费：0元\\n"}\n\nevent: done\ndata: {"status":"answered"}\n\n';
  const bytes=new TextEncoder().encode(text),parts=[];for(let i=0;i<bytes.length;i++)parts.push(bytes.slice(i,i+1));
  const events=[];await consume(body(parts),(event,data)=>events.push({event,data}));
  assert.deepEqual(events,[{event:'answer_delta',data:{text:'水费：0元\n'}},{event:'done',data:{status:'answered'}}]);
});
test('CRLF可跨网络分块',async()=>{
  const text='event: done\r\ndata: {"status":"ok"}\r\n\r\n',bytes=new TextEncoder().encode(text);
  const events=[];await consume(body([...bytes].map(b=>Uint8Array.of(b))),(e,d)=>events.push([e,d]));
  assert.deepEqual(events,[['done',{status:'ok'}]]);
});
test('不完整帧报错而不是静默当成功',async()=>{
  await assert.rejects(consume(body([new TextEncoder().encode('event: result\ndata: {')]),()=>{}),/中途结束/);
});
test('服务端错误事件保留且不自动重试',async()=>{
  const events=[];await consume(body([new TextEncoder().encode('event: error\ndata: {"detail":"停止"}\n\n')]),(e,d)=>events.push([e,d]));
  assert.deepEqual(events,[['error',{detail:'停止'}]]);
});
