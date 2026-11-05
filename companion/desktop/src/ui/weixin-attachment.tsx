import React, { useEffect, useState } from 'react';
import type { Json, Message } from '../shared';

export function WeixinAttachment({ space, message, item, onRetry, busy }: {
  space: string; message: Message; item: Json;
  onRetry?: (message: Message, block: string, text: string) => void; busy?: string;
}) {
  const id = String(item.id || ''), name = String(item.name || '附件'), runtime = String(message._runtimeId || '');
  const image = item.kind === 'image', ready = item.status === 'ready';
  const [preview, setPreview] = useState(''), [error, setError] = useState(''), [saving, setSaving] = useState(false);
  const block = String(message.presentation?.id || '') + '#media:' + id;
  const delivery = message.delivery?.[block];
  useEffect(() => {
    let active = true;
    setPreview(''); setError('');
    if (image && ready && /^a[0-9a-f]{32}$/.test(id)) {
      void window.xiaoyu.channelArtifactPreview({ space, runtime, message: message.id, artifact: id, thumbnail: item.thumbnail === true })
        .then(value => { if (active) setPreview(value); })
        .catch(() => { if (active) setError('预览暂不可用'); });
    }
    return () => { active = false; };
  }, [space, runtime, message.id, id, image, ready, item.thumbnail]);
  async function save() {
    setSaving(true); setError('');
    try { await window.xiaoyu.channelArtifactSave({ space, runtime, message: message.id, artifact: id, name }); }
    catch (problem) { setError(problem instanceof Error ? problem.message : '附件暂时无法保存'); }
    finally { setSaving(false); }
  }
  const size = Number(item.size) || 0;
  return <div className="weixin-attachment-wrap">
    <div className={'weixin-attachment ' + (image ? 'image' : 'file')}>
      {image && preview ? <img src={preview} alt={name} /> : <span className="weixin-attachment-icon">{image ? '图片' : '文件'}</span>}
      <div><strong>{name}</strong><small>{size > 0 ? size < 1024 * 1024 ? `${Math.ceil(size / 1024)} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB` : ''}</small>
        {ready && <button className="quiet-button" disabled={saving} onClick={() => void save()}>{saving ? '正在保存…' : '保存'}</button>}
        {!ready && <small>附件暂不可用</small>}{error && <small role="status">{error}</small>}</div>
    </div>
    {(delivery === 'failed' || delivery === 'unknown') && <small role="status" className="weixin-delivery">没确认发到微信 {onRetry && <button className="quiet-button" disabled={Boolean(busy)} onClick={() => onRetry(message, block, name)}>重发</button>}</small>}
  </div>;
}
