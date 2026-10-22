import { join } from 'node:path';
import type { RuntimeLaunch } from './runtime-host.js';

export function bundledRuntimeLaunch(root: string, platform: string, cwd: string,
  manifest: { layout?: string }): RuntimeLaunch {
  if (manifest.layout === 'python-embedded') {
    if (platform !== 'win32') throw new Error('嵌入式 Windows Runtime 不能在此系统运行');
    return { command: join(root, 'python/python.exe'),
      args: ['-I', '-u', join(root, 'app/runtime_entry.py')], cwd };
  }
  if (manifest.layout && manifest.layout !== 'pyinstaller') throw new Error('未知的 Runtime 包格式');
  return { command: join(root, 'xiaoyou-runtime', platform === 'win32' ? 'xiaoyou-runtime.exe' : 'xiaoyou-runtime'), args: [], cwd };
}
