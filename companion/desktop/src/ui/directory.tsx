import React, { useEffect, useRef, useState } from 'react';
import type { Json, Message, SpaceView } from '../shared';
import { messageBlocks, authorLabel, displayIssue } from '../core/presentation';
import { orderMessages } from '../core/message-order';
import { DisplayText } from './display-text';

type Row = Json & { uid: string; title: string; directory_runtime: string };
const items = (value: unknown): Json[] => Array.isArray(value) ? value as Json[] : [];
const errorText = (error: unknown) => error instanceof Error ? error.message : '来源暂不可用，请重试';

/** A shared projection. No local rename/archive or synthetic conversation. */
export function Directory({ space, onClose }: { space: SpaceView; onClose(): void }) {
  const [query,setQuery] = useState(''), [archived,setArchived] = useState(false), [internal,setInternal] = useState(false);
  const [rows,setRows] = useState<Row[]>([]), [cursors,setCursors] = useState<Record<string,string>>({}), [problem,setProblem] = useState(''), [busy,setBusy] = useState(false);
  const [selected,setSelected] = useState<Row>(), [history,setHistory] = useState<Row>(), [title,setTitle] = useState(''), [project,setProject] = useState('');
  const generation = useRef(0);
  const sources = space.connections?.filter(c => c.connected && c.capabilities.includes('conversation_directory_v1')).map(c=>c.runtime) || [];
  async function load(append=false) {
    const version=++generation.current; setBusy(true); setProblem('');
    const outcomes=await Promise.allSettled(sources.filter(id=>!append||cursors[id]).map(async runtime=>{
      const page=await window.xiaoyu.directory({space:space.id,runtime,query,cursor:append?cursors[runtime]:undefined});
      return {runtime,page};
    }));
    if(version!==generation.current)return;
    const merged=new Map((append?rows:[]).map(row=>[row.uid,row])), next=append?{...cursors}:{} as Record<string,string>;
    const failures:string[]=[];
    for(const outcome of outcomes) {
      if(outcome.status==='rejected'){failures.push(errorText(outcome.reason));continue;}
      const {runtime,page}=outcome.value;
      if(items(page.storage_errors).length)failures.push("共享整理资料不可用，原文件已保留；整理操作暂时停用。");
      for(const row of items(page.entries)) merged.set(String(row.uid),{...row,directory_runtime:runtime} as Row);
      if(page.more&&page.cursor)next[runtime]=String(page.cursor);else delete next[runtime];
    }
    setRows([...merged.values()].sort((a,b)=>Number(Boolean(b.pinned))-Number(Boolean(a.pinned))||Number(b.updated_at||0)-Number(a.updated_at||0)));
    setCursors(next);setProblem(failures.join('；')||(!sources.length?'已连接的来源尚未提供统一目录，请更新来源 Runtime。':''));setBusy(false);
  }
  useEffect(()=>{const timer=setTimeout(()=>void load(),250);return()=>{clearTimeout(timer);generation.current++;};},[space.id,sources.join('|'),query]);
  function select(row: Row){setSelected(row);setTitle(row.title);setProject(String(row.project||''));}
  async function edit(fields: Json) {
    if(!selected)return; const version=generation.current; setBusy(true);setProblem('');
    try {const result=await window.xiaoyu.directoryEdit({space:space.id,runtime:selected.directory_runtime,id:selected.uid,revision:Number(selected.metadata_revision||0),fields});
      if(version!==generation.current)return;
      if(result.status==='queued'){setProblem('已排队，来源电脑尚未确认应用。');setBusy(false);return;}
      setSelected(undefined);await load();
    }catch(error){if(version===generation.current){setProblem(errorText(error));setBusy(false);}}
  }
  return <div className="directory-shade"><section className="directory-dialog" role="dialog" aria-modal="true" aria-label="统一会话目录">
    <header><h2>统一会话目录</h2><button onClick={onClose}>关闭</button></header>
    {history?<History key={history.uid} space={space.id} row={history} onBack={()=>setHistory(undefined)}/> : selected?<>
      <button className="quiet-button" onClick={()=>setSelected(undefined)}>‹ 返回目录</button><h3>{selected.title}</h3>
      {Boolean(selected.binding_error)&&<p role="status">执行关联记录有缺口，请结合来源记录核对。</p>}
      <p className="directory-provenance">{String(selected.source_name||'')} · {String(selected.agent||selected.kind)}<br/>原名：{String(selected.original_title||selected.title)}<br/>执行目录：{String(selected.cwd||'无执行目录')}</p>
      <label>统一名称<input value={title} maxLength={200} onChange={e=>setTitle(e.target.value)}/></label>
      <label>项目分类<input value={project} maxLength={120} onChange={e=>setProject(e.target.value)}/></label>
      <div className="directory-actions">
        {!(selected.kind==='conversation'&&selected.conversation_id==='default')&&<button disabled={busy||!title.trim()} onClick={()=>void edit({title:title.trim(),project:project.trim()})}>保存名称与分类</button>}
        <button disabled={busy} onClick={()=>void edit({pinned:!selected.pinned})}>{selected.pinned?'取消置顶':'置顶'}</button>
        {!(selected.kind==='conversation'&&selected.conversation_id==='default')&&<button disabled={busy} onClick={()=>void edit({archived:!selected.archived})}>{selected.archived?'恢复记录':'归档记录'}</button>}
        <button disabled={busy} onClick={()=>setHistory(selected)}>查看来源记录</button>
        <button disabled={busy} onClick={()=>{const version=generation.current;setBusy(true);void window.xiaoyu.directoryMetadata({space:space.id,runtime:selected.directory_runtime,id:selected.uid}).then(result=>{if(version===generation.current){setProblem(result.status==='ready'?'摘要已生成，返回目录刷新后查看。':'已请求生成摘要，稍后刷新目录查看。');setBusy(false);}}).catch(error=>{if(version===generation.current){setProblem(errorText(error));setBusy(false);}});}}>生成标题与摘要建议</button>
      </div>
      {Boolean(selected.generated)&&<article><small>{(selected.generated as Json).stale?'摘要待更新':'摘要'} · {String((selected.generated as Json).source)}</small><p>{String((selected.generated as Json).summary||'')}</p><button onClick={()=>setTitle(String((selected.generated as Json).title||title))}>使用建议名称</button></article>}
      {Boolean((selected.native_open as Json)?.supported)?<button disabled={busy} onClick={()=>{const version=generation.current;void window.xiaoyu.directoryOpen({space:space.id,runtime:selected.directory_runtime,id:selected.uid}).then(()=>{if(version===generation.current)setProblem('已请求来源电脑打开原会话，请在原生应用核对。');}).catch(error=>{if(version===generation.current)setProblem(errorText(error));});}}>在来源电脑打开原生会话</button>:<p className="directory-provenance">{String((selected.native_open as Json)?.reason||'')}</p>}
      {Boolean(selected.native_presentation) && <p role="status">{String((selected.native_presentation as Json).label || '')}</p>}
      <small>归档只影响目录展示。名称和分类不会改变真实工作目录。原生会话的控制接管需来源支持；此处的记录为只读。</small>
    </>:<>
      <input aria-label="搜索所有来源会话" placeholder="搜索名称、项目或电脑" value={query} maxLength={200} onChange={e=>setQuery(e.target.value)}/>
      <div className="directory-actions"><label><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>已归档</label><label><input type="checkbox" checked={internal} onChange={e=>setInternal(e.target.checked)}/>内部帮手会话</label><button disabled={busy} onClick={()=>void load()}>刷新</button></div>
      {rows.filter(row=>(archived||!row.archived)&&(internal||!row.internal)&&(!row.bound||query.trim())).map(row=><button className="directory-row" key={row.uid} onClick={()=>select(row)}><strong>{row.pinned?'★ ':''}{row.title}</strong><small>{String(row.source_name||'')} · {String(row.agent||row.kind)} · {String(row.project||'未分类')}</small></button>)}
      {!busy&&!rows.length&&<p>暂无匹配记录。</p>}{Boolean(Object.keys(cursors).length)&&<button disabled={busy} onClick={()=>void load(true)}>加载更多会话</button>}
    </>}
    {busy&&<p role="status">正在读取来源…</p>}{problem&&<p role="status">{problem}</p>}
  </section></div>;
}
function History({space,row,onBack}:{space:string;row:Row;onBack():void}) {
  const [messages,setMessages]=useState<Json[]>([]),[cursor,setCursor]=useState<string>(),[more,setMore]=useState(true),[busy,setBusy]=useState(false),[problem,setProblem]=useState('');
  const generation=useRef(0);
  async function load(reset=false){const version=++generation.current;setBusy(true);setProblem('');try{
    const page=await window.xiaoyu.directoryHistory({space,runtime:row.directory_runtime,id:row.uid,cursor:reset?undefined:cursor});
    if(version!==generation.current)return;
    if(page.status==='queued'){setProblem('来源电脑尚未返回记录，可稍后重试。');setBusy(false);return;}
    const merged=new Map((reset?[]:messages).map(m=>[String(m.mesh_origin||'')+':'+m.id,m]));
    for(const message of items(page.messages)){const key=String(message.mesh_origin||'')+':'+message.id;const old=merged.get(key);if(!old||Number(message.rev)>=Number(old.rev))merged.set(key,message);}
    setMessages(orderMessages([...merged.values()] as Message[]).slice(-200));setCursor(page.cursor?String(page.cursor):undefined);setMore(Boolean(page.more));setBusy(false);
  }catch(error){if(version===generation.current){setProblem(errorText(error));setBusy(false);}}}
  useEffect(()=>{void load(true);return()=>{generation.current++;};},[space,row.uid,row.directory_runtime]);
  return <><button className="quiet-button" onClick={onBack}>‹ 返回目录</button><h3>{row.title} · 来源记录</h3><small>公开文字，只读查看；早期记录可能有缺口。当前最多显示 200 条，原生输入不会重新发到主对话。</small>
    {messages.map(message=><article className="native-entry" key={String(message.mesh_origin||'')+':'+message.id}><small>{message.presentation ? authorLabel(message) : message.input_origin==='native_protocol'&&(message.author as Json)?.kind==='owner'?'原生会话输入':String((message.author as Json)?.name||(message.author as Json)?.kind||'')}</small>{messageBlocks(message as Message).map(block => <div key={block.id} data-block-id={block.id}><DisplayText block={block} /></div>)}{displayIssue(message) && <small role="status">{displayIssue(message)}</small>}</article>)}
    {problem&&<p role="status">{problem}</p>}<div className="directory-actions">{more&&<button disabled={busy} onClick={()=>void load()}>继续翻页</button>}<button disabled={busy} onClick={()=>void load(true)}>重新读取</button></div></>;
}
