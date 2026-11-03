/** Keep the Mac running for remote work while allowing its display to sleep. */
export function keepAwakeWhileRunning(platform: NodeJS.Platform, blocker: {
  start(type: 'prevent-app-suspension'): number;
  stop(id: number): boolean;
}): () => void {
  if (platform !== 'darwin') return () => {};
  const id = blocker.start('prevent-app-suspension');
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    blocker.stop(id);
  };
}
