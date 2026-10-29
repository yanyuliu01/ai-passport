import React, { useEffect, useRef, useState } from 'react';
import type { HelperView, Json, SpaceView } from '../shared';
import { Avatar, Icon } from './components';

interface Harness { harness: string; label: string; installed: boolean; ways: string[]; providers: string[]; login_hint: string | null }
interface ModelSetting { name: string; model: string; models: string[] }
interface ModelOption { id: string; name: string; efforts?: string[] }
// The provider a harness usually runs on; any provider's key is still accepted.
const USUAL_PROVIDER: Record<string, string> = { claude_code: 'anthropic', codex: 'openai', dsh: 'deepseek', pi: 'openai' };
const PROVIDER_LABELS: Record<string, string> = { openai: 'OpenAI', anthropic: 'Anthropic', deepseek: 'DeepSeek', google: 'Google', openrouter: 'OpenRouter', custom: '其他（自己写变量名）' };
const EFFORT_LABELS: Record<string, string> = { '': '默认', off: '不思考', low: '低', medium: '中', high: '高' };
// Matches the Runtime's levels per harness (config.EFFORTS_BY_TYPE); dsh has no medium.
const efforts = (harness: string) => ['', ...(harness === 'dsh' ? ['off', 'low', 'high'] : ['low', 'medium', 'high'])];

/** Every client can manage helpers on one explicitly selected computer. */
export function HelperSettings({ space, helpers, disabled, onRun }: { space?: SpaceView; helpers: HelperView[]; disabled: boolean; onRun(action: () => Promise<unknown>): Promise<void> }) {
  const connections = space?.connections || [];
  const [runtime, setRuntime] = useState('');
  const target = connections.find(row => row.runtime === runtime) || (!runtime ? connections.find(row => row.local) || connections[0] : undefined);
  const chosen = target?.runtime || runtime;
  const [added, setAdded] = useState<string[]>([]), [removable, setRemovable] = useState<string[]>([]), [restorable, setRestorable] = useState<string[]>([]);
  const [adding, setAdding] = useState(false), [removing, setRemoving] = useState<string>();
  const [modelSettings, setModelSettings] = useState<ModelSetting[]>([]), [editingModel, setEditingModel] = useState<ModelSetting>();
  const [permissions, setPermissions] = useState<string>(), [error, setError] = useState('');
  const generation = useRef(0);
  const reload = () => {
    const request = ++generation.current;
    setAdded([]); setRemovable([]); setRestorable([]); setModelSettings([]); setError('');
    if (space && chosen && target?.connected) void window.xiaoyu.helperOptions({ space: space.id, runtime: chosen }).then(value => {
      if (request !== generation.current) return;
      setAdded(Array.isArray(value.added) ? value.added as string[] : []);
      setRemovable(Array.isArray(value.removable) ? value.removable as string[] : Array.isArray(value.added) ? value.added as string[] : []);
      setRestorable(Array.isArray(value.restorable) ? value.restorable as string[] : []);
      setModelSettings(Array.isArray(value.model_settings) ? value.model_settings as unknown as ModelSetting[] : []);
    }).catch(reason => { if (request === generation.current) setError(String(reason)); });
  };
  useEffect(() => { reload(); return () => { ++generation.current; }; }, [space?.id, chosen, target?.connected, JSON.stringify(helpers.map(row => row.members || row))]);
  useEffect(() => { setRuntime(''); }, [space?.id]);
  useEffect(() => { setAdding(false); setRemoving(undefined); setEditingModel(undefined); setPermissions(undefined); }, [space?.id, chosen]);
  const unavailable = disabled || !space || !target?.connected;
  const rows = helpers.flatMap(item => item.members || [item]).filter(item => item.runtime === chosen && !item.name.includes('@'));
  return <article className="settings-card vertical">
    <label className="field">管理哪台电脑<select value={chosen} onChange={event => setRuntime(event.target.value)}>{connections.map(row => <option key={row.runtime} value={row.runtime}>{row.name}{row.connected ? '' : ' · 离线'}</option>)}</select></label>
    <p className="usage-freshness">添加或移除只影响所选电脑。移除后不再接收新任务，已有任务和历史保留。</p>
    {rows.map(item => { const setting = modelSettings.find(row => row.name === item.name); return <div className="helper-line" key={item.id}><Avatar type={item.type} size={26} /><span>{item.label}<small>{item.name}</small></span>
      <small className={item.connected ? 'ok' : ''}>{item.connected ? item.model || '已就绪' : '连不上'}</small>
      {setting && <button disabled={unavailable} onClick={() => setEditingModel(setting)}>选择模型</button>}
      {added.includes(item.name) && <button disabled={unavailable} onClick={() => setPermissions(item.name)}>工具权限</button>}
      {removable.includes(item.name) && <button disabled={unavailable} onClick={() => setRemoving(item.name)}>移除</button>}</div>; })}
    {!rows.length && <p>这台电脑还没有可用的帮手</p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    <button className="quiet-button add-helper" disabled={unavailable} onClick={() => setAdding(true)}><Icon name="plus" />添加帮手</button>
    {restorable.map(name => <button key={name} disabled={unavailable} onClick={() => space && void onRun(async () => { await window.xiaoyu.addHelper({ space: space.id, runtime: chosen, helper: { restore: name } }); reload(); })}>恢复 {name}</button>)}
    {removing && <div className="modal-shade"><section className="usage-modal" role="dialog" aria-modal="true"><h2>移除 {removing}？</h2><p>从 {target?.name} 移除，已有任务继续完成，历史保留。</p><footer><button onClick={() => setRemoving(undefined)}>取消</button><button onClick={() => space && void onRun(async () => { await window.xiaoyu.removeHelper({ space: space.id, runtime: chosen, name: removing }); setRemoving(undefined); reload(); })}>移除</button></footer></section></div>}
    {editingModel && space && <HelperModel runtime={chosen} space={space} setting={editingModel} onClose={() => setEditingModel(undefined)} onSaved={() => { setEditingModel(undefined); reload(); }} />}
    {adding && space && <AddHelper runtime={chosen} space={space} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); reload(); }} />}
    {permissions && space && <HelperPermissions runtime={chosen} space={space} name={permissions} onClose={() => setPermissions(undefined)} />}
  </article>;
}

function AddHelper({ runtime, space, onClose, onAdded }: { runtime: string; space: SpaceView; onClose(): void; onAdded(): void }) {
  const [options, setOptions] = useState<Harness[]>(), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [harness, setHarness] = useState(''), [model, setModel] = useState(''), [effort, setEffort] = useState('');
  const [access, setAccess] = useState('magpie'), [provider, setProvider] = useState('openai'), [env, setEnv] = useState(''), [key, setKey] = useState(''), [name, setName] = useState('');
  const [gateway, setGateway] = useState('http://127.0.0.1:3425'), [models, setModels] = useState<ModelOption[]>(), [loadingModels, setLoadingModels] = useState(false);
  const catalogGeneration = useRef(0);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus(); void window.xiaoyu.helperOptions({ space: space.id, runtime }).then(value => {
    const rows = Array.isArray(value.harnesses) ? value.harnesses as Harness[] : [];
    setOptions(rows); const first = rows.find(row => row.installed) || rows[0];
    if (first) { setHarness(first.harness); setName(first.harness.replace('_code', '')); }
  }).catch(reason => setError(reason instanceof Error ? reason.message : '读不到这台电脑上的帮手选项')); }, [space.id, runtime]);
  const chosen = options?.find(row => row.harness === harness);
  const levels = models?.find(row => row.id === model)?.efforts || [];
  const offeredEfforts = access === 'magpie' ? efforts(harness).filter(value => !value || levels.includes(value) || (value === 'off' && levels.includes('none'))) : efforts(harness);
  useEffect(() => { if (!offeredEfforts.includes(effort)) setEffort(''); }, [model, access, harness, models]);
  async function refreshModels() {
    const generation = ++catalogGeneration.current;
    setError(''); setLoadingModels(true); setModels(undefined); setModel('');
    try {
      const result = await window.xiaoyu.helperModels({ space: space.id, runtime, gateway_url: gateway, key: key || undefined });
      if (generation !== catalogGeneration.current) return;
      const rows = Array.isArray(result.models) ? result.models as ModelOption[] : [];
      setModels(rows); setModel(rows[0]?.id || '');
      if (!rows.length) setError('模型服务里还没有模型，请先在 Magpie 添加供应商');
    } catch (reason) { if (generation === catalogGeneration.current) setError(reason instanceof Error ? reason.message : '无法读取模型服务'); }
    finally { setLoadingModels(false); }
  }
  useEffect(() => { if (chosen && !chosen.ways.includes(access)) setAccess(chosen.ways[0]); if (!efforts(harness).includes(effort)) setEffort(''); setProvider(USUAL_PROVIDER[harness] || 'openai'); }, [harness]);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(''); setNotice('');
    if (access === 'key' && !key.trim()) { setError('要填 API Key'); return; }
    if (access === 'key' && provider === 'custom' && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(env)) { setError('自定义服务商要写环境变量名'); return; }
    setBusy(true);
    try {
      const result = await window.xiaoyu.addHelper({ space: space.id, runtime, helper: { name: name.trim(), harness, access, model: model.trim(), effort,
        ...(access === 'key' ? { provider, key, ...(provider === 'custom' ? { env } : {}) } : {}),
        ...(access === 'magpie' ? { gateway_url: gateway, ...(key ? { key } : {}) } : {}) } as Json });
      setKey('');
      if (result.problem) setNotice('已添加，但启动检查没通过：' + String(result.problem)); else onAdded();
    } catch (reason) { setError(reason instanceof Error ? reason.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '没有加上，请重试'); }
    finally { setBusy(false); }
  }
  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="usage-modal add-helper-modal" role="dialog" aria-modal="true" aria-labelledby="add-helper-title" onSubmit={submit}>
      <header><div><h2 id="add-helper-title">添加帮手</h2><small>添加到 · {space.connections?.find(row => row.runtime === runtime)?.name || '所选电脑'}</small></div><button ref={close} type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></header>
      {!options && !error && <p className="usage-model">正在读取这台电脑上装了什么…</p>}
      {options && <>
        <div className="field-label">用哪种 harness</div>
        <div className="pick" role="radiogroup" aria-label="harness">{options.map(row => <label key={row.harness} className={row.harness === harness ? 'on' : ''}>
          <input type="radio" name="harness" checked={row.harness === harness} onChange={() => { setHarness(row.harness); setName(row.harness.replace('_code', '')); }} />
          <strong>{row.label}</strong><small>{row.installed ? '已装好' : '这台电脑还没装'}</small></label>)}</div>
        {access !== 'magpie' && <label className="field">模型<input value={model} placeholder="不填就用它的默认模型" maxLength={64} onChange={event => setModel(event.target.value)} /></label>}
        <label className="field">努力程度<select value={effort} onChange={event => setEffort(event.target.value)}>{offeredEfforts.map(value => <option key={value} value={value}>{EFFORT_LABELS[value]}</option>)}</select></label>
        <div className="field-label">怎么接入这个模型</div>
        <div className="ways" role="radiogroup" aria-label="接入方式">
          <label className={access === 'magpie' ? 'on' : ''}><input type="radio" name="access" checked={access === 'magpie'} onChange={() => { catalogGeneration.current++; setKey(''); setModels(undefined); setAccess('magpie'); setModel(''); }} />模型服务</label>
          <label className={access === 'key' ? 'on' : ''}><input type="radio" name="access" checked={access === 'key'} onChange={() => { catalogGeneration.current++; setKey(''); setAccess('key'); setModel(''); }} />填 API Key</label>
          <label className={access === 'login' ? 'on' : ''} aria-disabled={!chosen?.ways.includes('login')}><input type="radio" name="access" disabled={!chosen?.ways.includes('login')} checked={access === 'login'} onChange={() => { catalogGeneration.current++; setKey(''); setAccess('login'); setModel(''); }} />用订阅登录</label>
        </div>
        {access === 'magpie' ? <>
          <p className="usage-freshness">先在 Magpie 添加供应商或登录订阅，再选择模型。不同帮手可使用同一个供应商。</p>
          <label className="field">Magpie 地址<input value={gateway} onChange={event => { catalogGeneration.current++; setGateway(event.target.value); setModels(undefined); setModel(''); }} /></label>
          <label className="field">网关访问凭据（未设置可留空）<input type="password" autoComplete="off" value={key} onChange={event => { catalogGeneration.current++; setKey(event.target.value); setModels(undefined); setModel(''); }} /></label>
          <button type="button" disabled={loadingModels} onClick={() => void refreshModels()}>{loadingModels ? '正在读取…' : '读取模型'}</button>
          {models && <label className="field">模型<select value={model} onChange={event => setModel(event.target.value)}>{models.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
        </> : access === 'key' ? <>
          <p className="usage-freshness">直连使用这个 harness 自己支持的接口。跨服务商模型请使用模型服务。</p>
          <label className="field">服务商<select value={provider} onChange={event => setProvider(event.target.value)}>{(chosen?.providers || []).map(item => <option key={item} value={item}>{PROVIDER_LABELS[item] || item}</option>)}</select></label>
          {provider === 'custom' && <label className="field">环境变量名<input value={env} placeholder="比如 MOONSHOT_API_KEY" maxLength={64} onChange={event => setEnv(event.target.value)} /></label>}
          <label className="field">API Key<input type="password" autoComplete="off" value={key} placeholder="只存在这台电脑上" onChange={event => setKey(event.target.value)} /></label>
        </> : <p className="usage-freshness">{chosen?.login_hint || '这个 harness 不支持订阅登录'}</p>}
        <label className="field">叫它什么<input value={name} maxLength={24} required onChange={event => setName(event.target.value)} /></label>
      </>}
      {error && <p role="alert" className="field-error">{error}</p>}
      {notice && <p role="status" className="usage-freshness">{notice}</p>}
      <footer><span /><div className="add-helper-actions"><button type="button" onClick={notice ? onAdded : onClose}>{notice ? '知道了' : '取消'}</button>
        {!notice && <button className="primary" type="submit" disabled={busy || !options || !harness || !name.trim() || (access === 'magpie' && (!model || !models))}>{busy ? '正在添加…' : '添加'}</button>}</div></footer>
    </form>
  </div>;
}

function HelperPermissions({ runtime, space, name, onClose }: { runtime: string; space: SpaceView; name: string; onClose(): void }) {
  const [servers, setServers] = useState<{id: string; name: string}[]>(), [grants, setGrants] = useState<Json>({});
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { void window.xiaoyu.helperOptions({ space: space.id, runtime }).then(result => {
    setServers(result.capabilities as {id: string; name: string}[] || []);
    setGrants((result.helper_grants as Record<string, Json>)?.[name] || {});
  }).catch(reason => setError(String(reason))); }, [space.id, name]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await window.xiaoyu.helperGrants({ space: space.id, runtime, name, grants }); onClose(); }
    catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }
  return <div className="modal-shade"><form className="usage-modal" role="dialog" aria-modal="true" onSubmit={save}>
    <header><h2>{name} 的工具权限</h2><button type="button" onClick={onClose}>关闭</button></header>
    <p>保存后，正在运行的帮手下一次调用即按新权限检查。</p>
    {servers?.map(server => <label className="field" key={server.id}><input type="checkbox" checked={grants[server.id] !== false}
      onChange={event => setGrants(previous => ({ ...previous, [server.id]: event.target.checked }))} />{server.name}</label>)}
    {servers?.length === 0 && <p>这台电脑还没有登记外部工具。</p>}
    {error && <p role="alert">{error}</p>}
    <footer><button type="submit" disabled={busy || !servers}>保存</button></footer>
  </form></div>;
}

function HelperModel({ runtime, space, setting, onClose, onSaved }: { runtime: string; space: SpaceView; setting: ModelSetting; onClose(): void; onSaved(): void }) {
  const [model, setModel] = useState(setting.model || setting.models.find(value => !value.includes('astra')) || '');
  const [custom, setCustom] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await window.xiaoyu.helperModel({ space: space.id, runtime, name: setting.name, model: model.trim() }); onSaved(); }
    catch (reason) { setError(reason instanceof Error ? reason.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '没有保存成功，请重试'); }
    finally { setBusy(false); }
  }
  return <div className="modal-shade"><form className="usage-modal add-helper-modal" role="dialog" aria-modal="true" aria-labelledby="helper-model-title" onSubmit={save}>
    <header><h2 id="helper-model-title">选择模型</h2><button type="button" disabled={busy} onClick={onClose}>关闭</button></header>
    <p>保存后用于新的任务，正在进行的任务继续使用原模型。</p>
    <label className="field">模型<select autoFocus value={custom ? '__custom' : model} disabled={busy} onChange={event => {
      if (event.target.value === '__custom') { setCustom(true); setModel(''); } else { setCustom(false); setModel(event.target.value); }
    }}>
      {!setting.model && <option value="" disabled>请选择模型</option>}
      {setting.models.map(value => <option key={value} value={value}>{value}</option>)}
      <option value="__custom">其他模型…</option>
    </select></label>
    {custom && <label className="field">模型名称<input value={model} maxLength={128} disabled={busy} placeholder="填写当前服务支持的模型名称" onChange={event => setModel(event.target.value)} /></label>}
    <p className="usage-freshness">模型的可用性和费用取决于你的订阅或服务商。</p>
    {error && <p role="alert" className="field-error">{error}</p>}
    <footer><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || !model.trim()} type="submit">{busy ? '正在保存…' : '保存'}</button></footer>
  </form></div>;
}
