import type { Action, Decision, Settlement, Snapshot } from './api';

export const money = (value: number | null | undefined, digits = 3) =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)} EUR` : '—';
export const power = (value: number) => Number.isFinite(value) ? `${value.toFixed(2)} kW` : '—';
export const stamp = (value: string) => value.replace('T', ' ').replace(/Z$/, ' UTC');
export function actionName(action: Action) {
  const semantics: Record<string, string> = {
    battery_charge: '电池充电功率', grid_charge: '并网点购电目标',
    grid_export_target: '并网点输出目标', self_consumption: '家庭自用模式',
  };
  return semantics[action.control_semantics] ?? `外部动作（${action.control_semantics}）`;
}
export const statusName = (value: string) => ({
  candidate: '候选建议', candidate_only: '候选建议', submitted: '已提交 · 等待回读',
  confirmed: '回读已确认', confirmation_unverified: '回读待核验', failed: '失败',
  optimal: '求解成功', queued: '等待计算', running: '计算中', ready: '结果可用',
  feasible: '可行', infeasible: '约束不可行', unsupported: '能力不可用', timeout: '计算超时',
}[value] ?? value);
export function newerSettlement(before?: Settlement | null, after?: Settlement | null) {
  if (!before) return after ?? null;
  if (!after) return before;
  const time = (s: Settlement) => typeof s.captured_at === 'string' && Number.isFinite(Date.parse(s.captured_at)) ? Date.parse(s.captured_at) : -Infinity;
  if (time(after) > time(before)) return after;
  if (time(after) < time(before)) return before;
  return before.status !== 'complete' && after.status === 'complete' ? after : before;
}
export function settlementMetric(settlement: Settlement | null, site: string, key: string): number | null {
  if (settlement?.status !== 'complete') return null;
  const raw = settlement.raw_fields as { sites?: { site_id: string; metrics?: Record<string, unknown> }[] } | undefined;
  const value = raw?.sites?.find(s => s.site_id === site)?.metrics?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
export function forecastCount(snapshot: Snapshot) {
  const arrays = [snapshot.import_eur_kwh, snapshot.export_eur_kwh, snapshot.pv_forecast_kw, snapshot.load_forecast_kw];
  if (!Number.isFinite(Date.parse(snapshot.device_time)) || !Number.isFinite(snapshot.step_hours) || snapshot.step_hours <= 0) return 0;
  const length = arrays[0].length;
  return length && arrays.every(values => values.length === length && values.every(Number.isFinite)) ? length : 0;
}
export function matchesDecision(decision: Decision | undefined, id?: string, run?: string, site?: string) {
  return !!decision && decision.id === id && decision.run_id === run && decision.site_id === site;
}
