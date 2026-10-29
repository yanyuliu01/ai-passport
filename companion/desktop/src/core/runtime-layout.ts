import { join } from 'node:path';
import type { RuntimeLaunch } from './runtime-host.js';

export function bundledRuntimeLaunch(root: string, platform: string, cwd: string,
  manifest: { layout?: string }): RuntimeLaunch {
  if (manifest.layout === 'python-embedded') {
    if (platform !== 'win32') throw new Error('嵌入式 Windows Runtime 不能在此系统运行');
    return { command: join(root, 'python/python.exe'),
      args: ['-I', '-X', 'utf8', '-u', join(root, 'app/runtime_entry.py')], cwd, personaFile: join(root, 'persona.txt') };
  }
  if (manifest.layout && manifest.layout !== 'pyinstaller') throw new Error('未知的 Runtime 包格式');
  return { command: join(root, 'xiaoyu-runtime', platform === 'win32' ? 'xiaoyu-runtime.exe' : 'xiaoyu-runtime'), args: [], cwd, personaFile: join(root, 'persona.txt') };
}
