import { existsSync } from 'node:fs';
import { join } from 'node:path';

/** Retain the old Electron app name as well as its encrypted profile. */
export function legacyProfile(appData: string): { name: string; path: string } | undefined {
  for (const name of ['xiaoyou-desktop', '小幽']) {
    const path = join(appData, name);
    if (existsSync(path)) return { name, path };
  }
  return undefined;
}
