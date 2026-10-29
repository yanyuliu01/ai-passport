import type { SecretStore } from './client.js';

export function loadModelKey(secrets: SecretStore): string | undefined {
  const space = secrets.load<string>('account-space');
  if (!space) return secrets.load<string>('deepseek-key');
  return secrets.load<string>('deepseek-key:' + space) ||
    (secrets.load<string>('deepseek-key-owner') === space ? secrets.load<string>('deepseek-key') : undefined);
}
export function saveModelKey(secrets: SecretStore, key: string): void {
  const space = secrets.load<string>('account-space');
  secrets.save(space ? 'deepseek-key:' + space : 'deepseek-key', key);
}
/** Capture the previous owner before sign-in can change account-space. */
export function bindLegacyModelKey(secrets: SecretStore): void {
  const space = secrets.load<string>('account-space');
  if (space && secrets.load<string>('deepseek-key') && !secrets.load<string>('deepseek-key-owner'))
    secrets.save('deepseek-key-owner', space);
}
