export class SetupProblem extends Error {
  constructor(readonly kind: 'key' | 'credit' | 'network' | 'provider' | 'service', message: string) { super(message); }
}

export function modelProblem(status: number): SetupProblem {
  if (status === 401) return new SetupProblem('key', '这把 DeepSeek Key 无效，请检查后重新填写。');
  if (status === 402) return new SetupProblem('credit', 'DeepSeek 账户余额不足，请充值后重试。');
  if (status === 403) return new SetupProblem('key', '这把 Key 暂无模型访问权限，请检查 DeepSeek 账户。');
  if (status === 429) return new SetupProblem('provider', 'DeepSeek 暂时繁忙或请求受限，请稍后重试。');
  return new SetupProblem('provider', 'DeepSeek 暂时无法回复，请稍后重试。');
}

/** A small explicit, paid probe; never follows a redirect or returns provider diagnostics/secrets. */
export async function checkDeepSeekKey(key: string, signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<void> {
  if (key.length < 12 || key.length > 4096 || /[\r\n\0]/.test(key))
    throw new SetupProblem('key', '请填写完整的 DeepSeek API Key。');
  const controller = new AbortController(), abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetcher('https://api.deepseek.com/anthropic/v1/messages', {
      method: 'POST', redirect: 'error', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01' },
      body: JSON.stringify({ model: 'deepseek-flash', max_tokens: 16, thinking: { type: 'disabled' },
        stream: false, messages: [{ role: 'user', content: 'Reply OK only.' }] }),
    });
    if (!response.ok) { await response.body?.cancel(); throw modelProblem(response.status); }
    const reader = response.body?.getReader();
    if (!reader) throw new SetupProblem('provider', 'DeepSeek 没有返回回复，请重试。');
    const chunks: Uint8Array[] = []; let size = 0;
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength;
      if (size > 65536) { await reader.cancel(); throw new SetupProblem('provider', 'DeepSeek 回复格式异常，请重试。'); }
      chunks.push(next.value);
    }
    let value;
    try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw new SetupProblem('provider', 'DeepSeek 回复格式异常，请稍后重试。'); }
    if (!Array.isArray(value.content) || !value.content.some((item: { type?: string; text?: string }) =>
      item.type === 'text' && typeof item.text === 'string' && item.text.trim()))
      throw new SetupProblem('provider', 'DeepSeek 没有返回文字，请重试。');
  } catch (error) {
    if (error instanceof SetupProblem) throw error;
    throw new SetupProblem('network', signal?.aborted ? '验证已暂停，原配置保留。' : '暂时连不上 DeepSeek，请检查网络后重试。');
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}
