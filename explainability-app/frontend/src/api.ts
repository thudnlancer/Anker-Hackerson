export interface Cost {
  purchase_eur: number;
  export_revenue_eur: number;
  degradation_eur: number;
  terminal_credit_eur: number;
  objective_eur: number;
}
export interface Action { kind: string; power_kw: number; control_semantics: string }
export interface Snapshot {
  device_time: string;
  observed_at: string;
  soc_percent: number;
  pv_kw: number;
  load_kw: number;
  capacity_kwh: number;
  max_charge_kw: number;
  max_discharge_kw: number;
  step_hours: number;
  pv_forecast_kw: number[];
  load_forecast_kw: number[];
  import_eur_kwh: number[];
  export_eur_kwh: number[];
  terminal_eur_kwh: number;
  sources: Record<string, string>;
  history_mode: string;
  cache_tail: string | null;
  forecast_status: string;
  fallback_reason: string | null;
}
export interface ExecutionEvent {
  event_id: string;
  instruction_id: string;
  kind: 'submitted' | 'confirmed' | 'failed';
  issued_at: string;
  observed_at: string | null;
  device_time: string | null;
  note: string;
  observed_mode: string | null;
  observed_power_kw: number | null;
}
export interface Settlement { [key: string]: unknown }
export interface Provenance {
  origin: string;
  input_kind: string;
  references: string[];
  note?: string;
}
export interface Station {
  id: string; run_id: string; site_id: string; decision_id: string;
  device_time: string; source_kind: string; soc_percent: number;
  pv_kw: number; load_kw: number; action: Action; forecast_status: string;
  execution_status: string; solver_status: string; reason_summary: string;
  sources?: Record<string, string>;
  provenance?: Provenance | null;
}
export interface Overview {
  run_id: string | null;
  runs: { run_id: string; source_kind: string; created_at: string }[];
  stations: Station[];
  settlement: Settlement | null;
}
export interface Decision {
  id: string; run_id: string; site_id: string; decision_id: string;
  source_kind: string; snapshot: Snapshot; snapshot_hash: string;
  action: Action; model_version: string; policy_version: string;
  solver_status: string; cost: Cost | null; execution_status: string;
  execution_events: ExecutionEvent[]; created_at: string;
  capabilities: { counterfactual: boolean; supported_interventions: string[] };
  settlement: Settlement | null;
  provenance?: Provenance | null;
}
export interface Explanation {
  title: string; summary: string;
  facts: { id: string; code: string; text: string; evidence_refs: string[] }[];
  scope: string; execution_status: string; source_kind: string;
}
export interface PlanStep {
  step: number; charge_kw: number; discharge_kw: number;
  grid_import_kw: number; grid_export_kw: number; soc_end_percent: number;
}
export interface Scenario { status: string; solve_ms: number; cost: Cost; plan: PlanStep[] }
export interface ComparisonResult {
  baseline: Scenario; alternative: Scenario; delta_eur: number; assumptions: string[];
}
export type JobStatus = 'queued' | 'running' | 'ready' | 'infeasible' | 'timeout' | 'unsupported' | 'failed';
export interface Job {
  id: string; status: JobStatus; decision_id: string; first_charge_kw: number;
  result: ComparisonResult | null; error: string | null;
}
export interface QuestionAnswer {
  intent: 'why' | 'compare_no_charge' | 'power_limit' | 'unsupported';
  answer: string; evidence_refs: string[]; job: Job | null;
}
export interface PlanResult {
  status: 'feasible' | 'infeasible' | 'unsupported' | 'timeout' | 'failed';
  decision_id: string; message?: string; target_soc_percent?: number;
  deadline?: string; deadline_step?: number; max_reachable_energy_kwh?: number;
  target_energy_kwh?: number; expected_completion_step?: number | null;
  baseline_cost_eur?: number; planned_cost_eur?: number; cost_delta_eur?: number;
  adjustments?: { kind: string; target_soc_percent?: number; deadline?: string;
    status: string; cost_delta_eur: number }[];
  assumptions?: string[]; attribution?: string;
}
export interface PlanJob {
  id: string; decision_id: string; target_soc_percent: number; deadline: string;
  status: JobStatus; result: PlanResult | null; error: string | null;
}
export interface PVDaily {
  site_id: string; device_date: string; arm: string; steps: number;
  allocation_method: string; source_kind: string; curtailment_status: string;
  totals: Record<string, number>; bottlenecks: Record<string, number | null>; caveat: string;
}
export interface OfflineReplaySummary { site_id: string; arms: string[]; hours_per_arm: number; time_basis: string; start_local_hour: string; end_local_hour: string; source_sha256: string; source_kind: string; official_score_claim: boolean; negative_price_rows: number; weather_source: string; scope: string }
export interface OfflineReplayStep { record: Record<string, string | number | boolean>; facts: { text: string; evidence_refs: string[] }[]; source_sha256: string; source_kind: string; time_basis: string; capabilities: { counterfactual: boolean; plan_feasibility: boolean }; execution_status: string; settlement: null; scope: string }

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') detail = data.detail;
      else if (typeof data.error === 'string') detail = data.error;
    } catch { /* Keep the HTTP status if the response is not JSON. */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
export const api = {
  stations: (runId?: string) => request<Overview>(`/api/stations${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`),
  decision: (id: string) => request<Decision>(`/api/decisions/${encodeURIComponent(id)}`),
  explanation: (id: string) => request<Explanation>(`/api/decisions/${encodeURIComponent(id)}/explanation`),
  compare: (id: string, power: number) => request<Job>(`/api/decisions/${encodeURIComponent(id)}/counterfactuals`, { first_charge_kw: power }),
  job: (id: string) => request<Job>(`/api/counterfactuals/${encodeURIComponent(id)}`),
  question: (id: string, question: string) => request<QuestionAnswer>('/api/questions', { decision_id: id, question }),
  plan: (id: string, targetSoc: number, deadline: string) => request<PlanJob>('/api/plans/feasibility', {
    decision_id: id, target_soc_percent: targetSoc, deadline,
  }),
  planJob: (id: string) => request<PlanJob>(`/api/plans/${encodeURIComponent(id)}`),
  pvDaily: (siteId: string, arm = 'zero_flow') => request<{ site_id: string; arm: string; days: PVDaily[] }>(`/api/pv/${encodeURIComponent(siteId)}/daily?arm=${encodeURIComponent(arm)}`),
  offlineReplay: () => request<OfflineReplaySummary>('/api/offline-replay'),
  offlineReplayStep: (index: number, arm = 'irradiance') => request<OfflineReplayStep>(`/api/offline-replay/steps/${index}?arm=${encodeURIComponent(arm)}`),
};
