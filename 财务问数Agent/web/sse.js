(function(root){
  function parseFrame(frame){
    let event='message';const data=[];
    for(const line of frame.split('\n')){
      if(line.startsWith(':'))continue;
      const sep=line.indexOf(':');const field=sep<0?line:line.slice(0,sep);
      let value=sep<0?'':line.slice(sep+1);if(value.startsWith(' '))value=value.slice(1);
      if(field==='event')event=value;
      if(field==='data')data.push(value);
    }
    return data.length?{event,data:JSON.parse(data.join('\n'))}:null;
  }
  async function consume(body,onEvent){
    const reader=body.getReader(),decoder=new TextDecoder('utf-8');let buffer='';
    function dispatch(){
      let boundary;
      while((boundary=buffer.indexOf('\n\n'))>=0){
        const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
        const parsed=parseFrame(frame);if(parsed)onEvent(parsed.event,parsed.data);
      }
    }
    try{
      while(true){const {value,done}=await reader.read();if(done)break;buffer=(buffer+decoder.decode(value,{stream:true})).replace(/\r\n/g,'\n');dispatch();}
      buffer=(buffer+decoder.decode()).replace(/\r\n/g,'\n');dispatch();
      if(buffer.trim()&&!buffer.trim().startsWith(':'))throw new Error('数据流在一个消息中途结束，请手动重试。');
    }finally{reader.releaseLock();}
  }
  const api={consume,parseFrame};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.FinanceSSE=api;
})(typeof window!=='undefined'?window:globalThis);
