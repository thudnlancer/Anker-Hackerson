import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SolixApp from './SolixApp';
import type { Decision, Overview } from './api';
import { forecastCount, newerSettlement, settlementMetric } from './solixDomain';

const decision: Decision = {
  id: 'record/1', run_id: 'run-1', site_id: 'site-1', decision_id: 'd1', source_kind: 'external', snapshot_hash: 'a'.repeat(64),
  model_version: 'shared', policy_version: 'v1', solver_status: 'optimal', created_at: '2026-10-17T00:00:00Z',
  action: { kind: 'discharge', power_kw: 2, control_semantics: 'grid_export_target' }, execution_status: 'candidate', execution_events: [], settlement: null,
  cost: { purchase_eur: 1, export_revenue_eur: -.1, degradation_eur: .1, terminal_credit_eur: .2, objective_eur: 1 },
  capabilities: { counterfactual: true, supported_interventions: ['first_charge_kw'] },
  snapshot: { device_time: '2026-10-17T00:00:00Z', observed_at: '2026-10-17T00:00:01Z', soc_percent: 35, pv_kw: 1, load_kw: 2, capacity_kwh: 14, max_charge_kw: 5, max_discharge_kw: 5, step_hours: .25, pv_forecast_kw: [1, 2, 3, 4], load_forecast_kw: [2, 2, 2, 2], import_eur_kwh: [.3, .1, .2, .4], export_eur_kwh: [.05, -.01, .05, .05], terminal_eur_kwh: .2, sources: {}, history_mode: 'none', cache_tail: null, forecast_status: 'ready', fallback_reason: null },
};
const overview: Overview = { run_id: 'run-1', runs: [{ run_id: 'run-1', source_kind: 'external', created_at: decision.created_at }], settlement: null,
  stations: [1, 2].map(i => ({ id: `record/${i}`, run_id: 'run-1', site_id: `site-${i}`, decision_id: `d${i}`, device_time: decision.snapshot.device_time, source_kind: 'external', soc_percent: i * 20, pv_kw: i, load_kw: i + 1, action: decision.action, forecast_status: 'ready', execution_status: 'candidate', solver_status: 'optimal', reason_summary: `站点 ${i} 的建议`, record_freshness: 'fresh' })),
};
function reply(value: unknown) { return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }); }
function setup() {
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.startsWith('/api/stations')) return reply(overview);
    if (path.endsWith('/explanation')) return reply({ title: '已保存的原因', summary: `证据 ${path.includes('%2F2') ? '2' : '1'}`, facts: [], scope: '模型假设' });
    if (path === '/api/plans/feasibility') return reply({ id: 'plan-1', status: 'queued', result: null, ...JSON.parse(String(options?.body)) });
    if (path === '/api/plans/plan-1') return reply({ id: 'plan-1', status: 'ready', result: { status: 'feasible', decision_id: decision.id, cost_delta_eur: -.2 }, error: null });
    if (path.endsWith('/counterfactuals')) return reply({ id: 'job-1', decision_id: decision.id, status: 'queued', result: null, error: null });
    if (path === '/api/counterfactuals/job-1') return reply({ id: 'job-1', status: 'ready', result: { baseline: { cost: decision.cost }, alternative: { cost: { ...decision.cost, objective_eur: .9 } }, delta_eur: -.1, assumptions: ['保留原预测'] }, error: null });
    if (path.startsWith('/api/pv/')) return reply({ days: [], arm: 'irradiance', site_id: decision.site_id });
    if (path.startsWith('/api/decisions/')) return reply({ ...decision, id: decodeURIComponent(path.split('/')[3]), site_id: path.includes('%2F2') ? 'site-2' : 'site-1' });
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetch); return fetch;
}
async function route(page: string) { await act(async () => { location.hash = `#/${page}`; window.dispatchEvent(new HashChangeEvent('hashchange')); }); }
beforeEach(() => { localStorage.clear(); location.hash = '#/home'; vi.stubGlobal('scrollTo', vi.fn()); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('SOLIX v13 API integration', () => {
  it('renders the supplied battery design and distinguishes a grid target from telemetry', async () => {
    setup(); render(<SolixApp/>); await screen.findByText('证据 1');
    fireEvent.click(screen.getByRole('button', { name: '展开能源信息节点' }));
    expect(screen.getByRole('button', { name: '展开能源信息节点' })).toHaveAttribute('aria-expanded', 'true');
    await route('overview');
    expect(screen.getByText(/放电指令表示并网点输出目标/)).toBeInTheDocument();
    expect(screen.getByText('快照未提供')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('¥');
  });
  it('never displays a delayed explanation or detail from another selected site', async () => {
    const fetch = setup(); let resolveDetail!: (response: Response) => void;
    const normal = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (path, options) => path === '/api/decisions/record%2F2' ? await new Promise<Response>(resolve => { resolveDetail = resolve; }) : normal(path, options));
    render(<SolixApp/>); await screen.findByText('证据 1');
    fireEvent.click(screen.getByRole('button', { name: /CURRENT SITE/ })); fireEvent.click(screen.getByRole('button', { name: /site-2.*候选建议/ }));
    expect(screen.queryByText('证据 1')).not.toBeInTheDocument();
    await route('ai'); expect(screen.getByText('等待当前站点决策证据…')).toBeInTheDocument();
    await act(async () => resolveDetail(reply({ ...decision, id: 'record/2', site_id: 'site-2' })));
    expect(await screen.findByText('证据 2')).toBeInTheDocument();
  });
  it('submits the server record identity and preserves a negative counterfactual difference', async () => {
    vi.useFakeTimers(); const fetch = setup(); await act(async () => { render(<SolixApp/>); }); await route('ai');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新求解并比较 →' })); });
    const post = fetch.mock.calls.find(([path]) => path.endsWith('/counterfactuals'))!;
    expect(post[0]).toBe('/api/decisions/record%2F1/counterfactuals'); expect(JSON.parse(String(post[1]?.body))).toEqual({ first_charge_kw: 0 });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); }); expect(screen.getByText('-0.100 EUR')).toBeInTheDocument();
  });
  it('anchors plan deadlines to device steps and polls the independent worker result', async () => {
    vi.useFakeTimers(); const fetch = setup(); await act(async () => { render(<SolixApp/>); }); await route('plan');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查模型可行性 →' })); });
    const post = fetch.mock.calls.find(([path]) => path === '/api/plans/feasibility')!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({ decision_id: 'record/1', target_soc_percent: 80, deadline: '2026-10-17T01:00:00.000Z' });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); }); expect(screen.getByText('-0.200 EUR')).toBeInTheDocument();
    expect(fetch.mock.calls.every(([path]) => !/execution-events|readbacks|services|power_control/.test(path))).toBe(true);
  });
  it('shows an empty PV state instead of inventing allocation percentages', async () => {
    setup(); render(<SolixApp/>); await screen.findByText('证据 1'); await route('analytics');
    await screen.findByText('当前站点和方案没有光伏日报，尚不能计算去向比例。');
    expect(screen.queryByRole('img', { name: '光伏电量去向估算' })).not.toBeInTheDocument();
    expect(screen.getByText('未结算 · 尚无结算记录')).toBeInTheDocument();
  });
  it('keeps a plan pinned to its original snapshot when collection publishes a new decision', async () => {
    vi.useFakeTimers(); const fetch = setup(); const normal = fetch.getMockImplementation()!; let rounds = 0;
    const next = { ...decision, id: 'record/new', snapshot: { ...decision.snapshot, device_time: '2026-10-17T01:00:00Z' } };
    fetch.mockImplementation(async (path, options) => {
      if (path.startsWith('/api/stations')) return reply(++rounds === 1 ? overview : { ...overview, stations: [{ ...overview.stations[0], id: next.id, device_time: next.snapshot.device_time }] });
      if (path === '/api/decisions/record%2Fnew') return reply(next);
      return normal(path, options);
    });
    await act(async () => { render(<SolixApp/>); }); await route('plan');
    fireEvent.change(screen.getByLabelText('目标储能 SOC'), { target: { value: '72' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_100); });
    expect(screen.getByLabelText('目标储能 SOC')).toHaveValue(72);
    expect(screen.getByRole('button', { name: '使用最新决策并重置工作区' })).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查模型可行性 →' })); });
    const post = fetch.mock.calls.find(([path]) => path === '/api/plans/feasibility')!;
    expect(JSON.parse(String(post[1]?.body)).decision_id).toBe('record/1');
  });
  it('isolates saved preferences by site and reports storage failure', async () => {
    setup(); render(<SolixApp/>); await screen.findByText('证据 1'); await route('preferences');
    fireEvent.click(screen.getByRole('button', { name: /费用优先/ })); fireEvent.click(screen.getByRole('button', { name: '保存家庭偏好' }));
    expect(JSON.parse(localStorage.getItem('solix-v13-local-v1')!).homes['site-1'].priority).toBe('cost');
    fireEvent.click(screen.getByRole('button', { name: /CURRENT SITE/ })); fireEvent.click(screen.getByRole('button', { name: /site-2.*候选建议/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /均衡模式/ })).toHaveAttribute('aria-pressed', 'true'));
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    fireEvent.click(screen.getByRole('button', { name: '保存家庭偏好' })); expect(screen.getByText(/浏览器禁止存储/)).toBeInTheDocument();
  });
});
describe('Evidence accounting boundaries', () => {
  it('keeps the newest settlement and never substitutes another site total', () => {
    const final = { status: 'complete', captured_at: '2026-10-17T01:00:00Z', raw_fields: { sites: [{ site_id: 'site-2', metrics: { saving_settled: 88 } }] } };
    expect(newerSettlement(final, { status: 'pending', captured_at: '2026-10-17T00:00:00Z' })).toBe(final);
    expect(settlementMetric(final, 'site-1', 'saving_settled')).toBeNull();
  });
  it('rejects mismatched forecast lengths instead of making up timestamps', () => {
    expect(forecastCount({ ...decision.snapshot, load_forecast_kw: [1] })).toBe(0);
  });
});
