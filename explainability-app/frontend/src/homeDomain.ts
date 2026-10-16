import type { CollectionStatus, Settlement, Snapshot, Station } from './api';

export type Language = 'en' | 'zh';
export interface HomePreferences {
  country: 'DE' | 'NL' | 'FR';
  tariff: 'unknown' | 'fixed' | 'tou' | 'dynamic';
  installation: 'rooftop' | 'balcony';
  backup: 'unknown' | 'socket' | 'whole_home' | 'none';
  priority: 'balanced' | 'cost' | 'solar' | 'gentle';
  reserve: number;
}
export const defaultPreferences: HomePreferences = {
  country: 'DE', tariff: 'unknown', installation: 'rooftop', backup: 'unknown', priority: 'balanced', reserve: 20,
};
export const zones = { DE: 'Europe/Berlin', NL: 'Europe/Amsterdam', FR: 'Europe/Paris' };
export function formatTime(iso: string, zone: string, language: Language, full = false) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return language === 'en' ? 'Time unavailable' : '时间未知';
  return new Intl.DateTimeFormat(language === 'en' ? 'en-GB' : 'zh-CN', {
    timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    ...(full ? { month: 'short', day: 'numeric', timeZoneName: 'short' } : { timeZoneName: 'short' }),
  }).format(date);
}
export function slotTime(snapshot: Snapshot, index: number) {
  return new Date(Date.parse(snapshot.device_time) + index * snapshot.step_hours * 3_600_000).toISOString();
}
export function forecastLength(snapshot: Snapshot) {
  if (!Number.isFinite(Date.parse(snapshot.device_time)) || !Number.isFinite(snapshot.step_hours) || snapshot.step_hours <= 0) return 0;
  return Math.min(snapshot.import_eur_kwh.length, snapshot.export_eur_kwh.length, snapshot.pv_forecast_kw.length, snapshot.load_forecast_kw.length);
}
export function artificial(station?: Station) {
  return !station || /demo|synthetic|artificial/.test(station.source_kind) || station.provenance?.input_kind === 'synthetic';
}
export function homeHealth(station?: Station, failedRefresh = false, collection?: CollectionStatus | null, settlement?: Settlement | null) {
  if (!station) return 'empty';
  if (!failedRefresh && station.execution_status !== 'failed' && collection?.state === 'stopped' && collection.reason === 'completed' && settlement?.source_kind === 'simulator_mock' && settlement.status === 'complete') return 'completed';
  if (failedRefresh || collection?.state === 'stopped' || collection?.state === 'degraded' || station.record_freshness === 'stale' || station.record_freshness === 'clock_unverified') return 'attention';
  if (artificial(station)) return 'demo';
  if (station.execution_status === 'failed') return 'attention';
  if (station.record_freshness !== 'fresh') return 'unknown';
  if (station.execution_status !== 'confirmed') return 'advice';
  return 'verified';
}
export interface PlanDraft {
  name: string;
  kind: 'ev' | 'dishwasher' | 'laundry' | 'other';
  energyKwh: number;
  powerKw: number;
  efficiency: number;
  startSlot: number;
  endSlot: number;
  flexible: boolean;
}
export interface PlanSlot { index: number; energyKwh: number; start: string; end: string }
export interface PlanAssessment {
  status: 'feasible' | 'insufficient' | 'invalid' | 'unavailable';
  requiredInputKwh: number;
  availableKwh: number;
  shortfallKwh: number;
  hours: number;
  costEur: number | null;
  slots: PlanSlot[];
}
export interface SavedPlan {
  id: string; siteId: string; decisionId: string; deviceTime: string;
  draft: PlanDraft; assessment: PlanAssessment; savedAt: string;
}

/** Advice only: time/power check and a retail-price schedule, independent of the household MILP. */
export function assessPlan(snapshot: Snapshot, draft: PlanDraft): PlanAssessment {
  const result: PlanAssessment = { status: 'invalid', requiredInputKwh: 0, availableKwh: 0, shortfallKwh: 0, hours: 0, costEur: null, slots: [] };
  const { energyKwh, powerKw, efficiency, startSlot, endSlot } = draft;
  if (!draft.name.trim() || ![energyKwh, powerKw, efficiency].every(Number.isFinite) || energyKwh <= 0 || energyKwh > 200 || powerKw <= 0 || powerKw > 50 || efficiency <= 0 || efficiency > 1 || !Number.isInteger(startSlot) || !Number.isInteger(endSlot) || startSlot < 0 || endSlot <= startSlot) return result;
  const length = forecastLength(snapshot);
  if (!length || endSlot > length) return { ...result, status: 'unavailable' };
  const indices = Array.from({ length: endSlot - startSlot }, (_, i) => startSlot + i);
  // The official contest does not support negative-price import. Never present it as a subsidy.
  if (indices.some((i) => !Number.isFinite(snapshot.import_eur_kwh[i]) || snapshot.import_eur_kwh[i] < 0)) return { ...result, status: 'unavailable' };
  const input = energyKwh / efficiency;
  const perSlot = powerKw * snapshot.step_hours;
  const available = indices.length * perSlot;
  const base = { ...result, requiredInputKwh: input, availableKwh: available, shortfallKwh: Math.max(0, input - available), hours: input / powerKw };
  if (input > available + 1e-9) return { ...base, status: 'insufficient' };
  let selected: number[];
  if (draft.flexible && draft.kind !== 'dishwasher' && draft.kind !== 'laundry') {
    selected = [...indices].sort((a, b) => snapshot.import_eur_kwh[a] - snapshot.import_eur_kwh[b] || a - b);
  } else {
    const count = Math.ceil(input / perSlot - 1e-9);
    let best = Number.POSITIVE_INFINITY;
    selected = indices.slice(0, count);
    for (let first = startSlot; first + count <= endSlot; first++) {
      let remaining = input; let cost = 0;
      for (let i = first; i < first + count; i++) { const take = Math.min(perSlot, remaining); cost += take * snapshot.import_eur_kwh[i]; remaining -= take; }
      if (cost < best) { best = cost; selected = Array.from({ length: count }, (_, i) => first + i); }
    }
  }
  let remaining = input; let cost = 0; const slots: PlanSlot[] = [];
  for (const index of selected) {
    if (remaining <= 1e-9) break;
    const energy = Math.min(perSlot, remaining);
    slots.push({ index, energyKwh: energy, start: slotTime(snapshot, index), end: slotTime(snapshot, index + 1) });
    cost += energy * snapshot.import_eur_kwh[index]; remaining -= energy;
  }
  return { ...base, status: 'feasible', costEur: cost, slots: slots.sort((a, b) => a.index - b.index) };
}

export interface LocalData { version: 1; language: Language; homes: Record<string, HomePreferences>; plans: SavedPlan[] }
export const storageKey = 'solix-home-ux-v1';
function validSavedPlan(raw: unknown): raw is SavedPlan {
  if (!raw || typeof raw !== 'object') return false;
  const p = raw as SavedPlan;
  return typeof p.id === 'string' && typeof p.siteId === 'string' && typeof p.decisionId === 'string'
    && typeof p.deviceTime === 'string' && Number.isFinite(Date.parse(p.deviceTime))
    && !!p.draft && ['ev','dishwasher','laundry','other'].includes(p.draft.kind)
    && typeof p.draft.name === 'string' && p.draft.name.trim().length > 0
    && [p.draft.energyKwh, p.draft.powerKw, p.draft.efficiency].every(Number.isFinite)
    && p.draft.energyKwh > 0 && p.draft.powerKw > 0 && p.draft.efficiency > 0 && p.draft.efficiency <= 1
    && p.assessment?.status === 'feasible' && (p.assessment.costEur === null || Number.isFinite(p.assessment.costEur))
    && Array.isArray(p.assessment.slots) && p.assessment.slots.length > 0
    && p.assessment.slots.every((s) => s && typeof s.start === 'string' && typeof s.end === 'string'
      && Number.isFinite(Date.parse(s.start)) && Number.isFinite(Date.parse(s.end)) && Number.isFinite(s.energyKwh));
}
export function readLocalData(): LocalData {
  const empty: LocalData = { version: 1, language: 'en', homes: {}, plans: [] };
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    if (!value || value.version !== 1) return empty;
    const homes: Record<string, HomePreferences> = {};
    for (const [site, raw] of Object.entries(value.homes ?? {})) {
      const p = raw as HomePreferences;
      if (p && ['DE', 'NL', 'FR'].includes(p.country) && ['unknown', 'fixed', 'tou', 'dynamic'].includes(p.tariff) && ['rooftop', 'balcony'].includes(p.installation) && ['unknown', 'socket', 'whole_home', 'none'].includes(p.backup) && ['balanced', 'cost', 'solar', 'gentle'].includes(p.priority) && Number.isFinite(p.reserve) && p.reserve >= 0 && p.reserve <= 80) homes[site] = p;
    }
    const plans = Array.isArray(value.plans) ? value.plans.filter(validSavedPlan) : [];
    return { version: 1, language: value.language === 'zh' ? 'zh' : 'en', homes, plans };
  } catch { return empty; }
}
