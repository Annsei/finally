/**
 * reload.ts — indirection over window.location.reload so components that
 * hard-refresh after auth/season changes stay unit-testable (jsdom treats
 * location assignment as navigation, so it can't be stubbed directly).
 */
export function hardReload(): void {
  window.location.reload();
}
