import { access, readFile, realpath } from 'node:fs/promises';
import { constants } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';

export interface BundledAgents {
  dsh: { command: string[]; version: string };
  pi: { command: string[]; version: string };
}

/** Resources are verified in full before packaging; startup checks only launch paths. */
export async function bundledAgents(root: string, platform = process.platform, architecture = process.arch): Promise<BundledAgents> {
  const base = await realpath(root);
  const manifest = JSON.parse(await readFile(join(base, 'manifest.json'), 'utf8'));
  if (manifest.v !== 1 || manifest.platform !== platform || manifest.architecture !== architecture)
    throw new Error('内置助手与这台电脑不匹配，请重新安装对应版本的小鱼');
  async function confined(path: unknown): Promise<string> {
    if (typeof path !== 'string' || !path || isAbsolute(path)) throw new Error('内置助手路径无效');
    const target = await realpath(resolve(base, path)), local = relative(base, target);
    if (!local || local.startsWith('..') || isAbsolute(local)) throw new Error('内置助手路径超出安装目录');
    return target;
  }
  const node = await confined(manifest.node);
  await access(node, constants.X_OK);
  async function tool(name: 'dsh' | 'pi') {
    const item = manifest.tools?.[name];
    if (!item || typeof item.version !== 'string' || !item.version) throw new Error('内置助手清单缺失');
    const entry = await confined(item.entry);
    await access(entry, constants.R_OK);
    return { command: [node, entry], version: item.version };
  }
  return { dsh: await tool('dsh'), pi: await tool('pi') };
}
