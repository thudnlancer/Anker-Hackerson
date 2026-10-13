import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { Decision, Explanation, Job, Overview } from './api';

const decision: Decision = {
  id: 'evidence-1', run_id: 'demo-run', site_id: '低价充电站', decision_id: 'decision-1',
  source_kind: 'synthetic', snapshot_hash: 'a'.repeat(64), model_version: 'synthetic-milp-v1',
  policy_version: 'demo-v1', solver_status: 'optimal', created_at: '2026-10-13T01:00:00Z',
  action: { kind: 'charge', power_kw: 3, control_semantics: 'battery_charge' },
  execution_status: 'candidate', execution_events: [], settlement: null,
  cost: { purchase_eur: 0.9, export_revenue_eur: 0, degradation_eur: 0.1, terminal_credit_eur: 0.431, objective_eur: 0.569 },
  capabilities: { counterfactual: true, supported_interventions: ['first_charge_kw'] },
  snapshot: {
    device_time: '2026-10-13T01:00:00Z', observed_at: '2026-10-13T01:00:01Z',
    soc_percent: 35, pv_kw: 1.3, load_kw: 2.1, capacity_kwh: 14, max_charge_kw: 5,
    max_discharge_kw: 5, step_hours: 1, pv_forecast_kw: [1.3, 1.5, 1.7, 1.5],
    load_forecast_kw: [2.1, 2.1, 2.1, 2.1], import_eur_kwh: [0.1, 0.3, 0.4, 0.2],
    export_eur_kwh: [0.05, 0.05, 0.05, 0.05], terminal_eur_kwh: 0.2,
    sources: { price: '人工电价', pv: '人工光伏' }, history_mode: 'frozen_baseline',
    cache_tail: '2026-10-12T01:00:00Z', forecast_status: 'persistence_fallback',
    fallback_reason: '历史过期，使用当前观测延续。',
  },
};
const overview: Overview = {
  run_id: 'demo-run', runs: [
    { run_id: 'demo-run', source_kind: 'synthetic', created_at: '2026-10-13T01:00:00Z' },
    { run_id: 'external-run', source_kind: 'real', created_at: '2026-10-13T02:00:00Z' },
  ], settlement: null,
  stations: [{
    id: decision.id, run_id: decision.run_id, site_id: decision.site_id,
    decision_id: decision.decision_id, device_time: decision.snapshot.device_time,
    source_kind: decision.source_kind, soc_percent: decision.snapshot.soc_percent,
    pv_kw: decision.snapshot.pv_kw, load_kw: decision.snapshot.load_kw, action: decision.action,
    forecast_status: decision.snapshot.forecast_status, execution_status: decision.execution_status,
    solver_status: decision.solver_status, reason_summary: '首步电价较低，预留后续用电。',
  }],
};
const explanation: Explanation = {
  title: '依据当前快照', summary: '首步电价较低，预留后续用电。',
  scope: '解释仅引用当前记录。', execution_status: 'candidate', source_kind: 'synthetic',
  facts: [{ id: 'fact-1', code: 'price', text: '首步购电价格为 0.1 EUR/kWh。', evidence_refs: ['snapshot.import_eur_kwh[0]'] }],
};
const readyJob: Job = {
  id: 'job-1', decision_id: 'evidence-1', status: 'ready', first_charge_kw: 0, error: null,
  result: {
    baseline: { status: 'optimal', solve_ms: 20, cost: decision.cost!, plan: [{ step: 0, charge_kw: 3, discharge_kw: 0, grid_import_kw: 3.8, grid_export_kw: 0, soc_end_percent: 50 }] },
    alternative: { status: 'optimal', solve_ms: 20, cost: { ...decision.cost!, objective_eur: 0.069 }, plan: [{ step: 0, charge_kw: 0, discharge_kw: 0, grid_import_kw: 0.8, grid_export_kw: 0, soc_end_percent: 35 }] },
    delta_eur: -0.5, assumptions: ['同一输入快照，固定首步电池充电功率。'],
  },
};

function mockApi(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
    const defaults: Record<string, unknown> = {
      '/api/stations': overview, '/api/stations?run_id=demo-run': overview,
      '/api/stations?run_id=external-run': { ...overview, run_id: 'external-run', stations: [] },
      '/api/decisions/evidence-1': decision, '/api/decisions/evidence-1/explanation': explanation,
      '/api/decisions/evidence-1/counterfactuals': { ...readyJob, status: 'queued', result: null },
      '/api/counterfactuals/job-1': readyJob,
      '/api/questions': { intent: 'unsupported', answer: '支持原因、首步不充与功率边界问题。', evidence_refs: [], job: null },
    };
    const body = overrides[path] ?? defaults[path];
    return new Response(JSON.stringify(body ?? { detail: '未找到记录' }), { status: body ? 200 : 404, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
beforeEach(() => {
  window.location.hash = '#/';
  vi.stubGlobal('scrollTo', vi.fn());
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('储能解释用户流程', () => {
  it('呈现人工来源、候选建议与正确场次，刷新保持选择', async () => {
    const fetchMock = mockApi(); render(<App />);
    expect(await screen.findByRole('heading', { name: '低价充电站' })).toBeInTheDocument();
    expect(screen.getByText('人工场景 · 未连接设备控制 · 无官方结算')).toBeInTheDocument();
    expect(screen.getByText('候选建议')).toBeInTheDocument();
    expect(screen.getByText('求解成功')).toBeInTheDocument();
    expect(screen.getByText('未结算')).toBeInTheDocument();
    expect(screen.getByText('记录年龄（响应时）：未提供')).toBeInTheDocument();
    expect(screen.getByText('新鲜度未提供')).toBeInTheDocument();
    expect(screen.getByText('采集状态未提供')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('当前场次'), { target: { value: 'external-run' } });
    expect(await screen.findByText('这个场次还没有决策')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新记录' }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => path === '/api/stations?run_id=external-run').length).toBe(2));
    expect(screen.getByLabelText('当前场次')).toHaveValue('external-run');
  });
  it('打开原因卡无需反事实计算，展示回退及未执行事实', async () => {
    const fetchMock = mockApi(); window.location.hash = '#/decisions/evidence-1'; render(<App />);
    expect(await screen.findByText('首步购电价格为 0.1 EUR/kWh。')).toBeInTheDocument();
    expect(screen.getByText('snapshot.import_eur_kwh[0]')).toBeInTheDocument();
    expect(screen.getByText('历史过期，使用当前观测延续。')).toBeInTheDocument();
    expect(screen.getByText('目前只有建议，尚无指令提交或设备回读记录。')).toBeInTheDocument();
    expect(screen.getByText('未结算')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path.includes('counterfactual'))).toBe(false);
  });
  it('零并网输出目标明确注明功率含义，避免误读为电池零放电', async () => {
    const gridTargetOverview = {
      ...overview,
      stations: overview.stations.map((station) => ({
        ...station, source_kind: 'external', site_id: '并网零目标站',
        action: { kind: 'discharge', power_kw: 0, control_semantics: 'grid_export_target' },
        sources: { pv: 'observed', load: 'observed', pv_forecast: 'assumption', load_forecast: 'assumption' },
        reason_summary: '并网点输出目标为 0 kW，电池放电以对应计划为准。',
      })),
    };
    mockApi({ '/api/stations': gridTargetOverview, '/api/stations?run_id=demo-run': gridTargetOverview });
    render(<App />);
    expect(await screen.findByText('功率含义：并网点输出目标')).toBeInTheDocument();
    expect(screen.getByRole('article')).toHaveTextContent('放电0.00 kW');
    expect(screen.getByRole('article')).toHaveTextContent('功率含义：并网点输出目标');
    expect(screen.queryByText('功率含义：电池充电功率')).not.toBeInTheDocument();
  });
  it('提交零充电干预，完成导航并如实展示负费用差', async () => {
    const fetchMock = mockApi(); window.location.hash = '#/decisions/evidence-1'; render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '计算替代方案' }));
    expect(await screen.findByText('−€0.500000')).toBeInTheDocument();
    expect(screen.getByText('在本次假设下，替代方案费用更低。')).toBeInTheDocument();
    expect(screen.getByText('−€0.500000').closest('.delta-card')).toHaveClass('negative');
    const request = fetchMock.mock.calls.find(([path]) => path === '/api/decisions/evidence-1/counterfactuals');
    expect(request).toBeDefined();
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ first_charge_kw: 0 });
    expect(window.location.hash).toBe('#/comparisons/job-1');
    expect(screen.getAllByRole('table')).toHaveLength(2);
    expect(screen.getByText(/不能据此推断 HA 控制模式/)).toBeInTheDocument();
  });
  it('浮点误差级的负差额展示近似相同，保留真实负差额判断', async () => {
    const tinyDelta = -1.776e-15;
    mockApi({ '/api/counterfactuals/job-1': {
      ...readyJob,
      result: {
        ...readyJob.result!, delta_eur: tinyDelta,
        alternative: { ...readyJob.result!.alternative, cost: { ...readyJob.result!.baseline.cost, objective_eur: readyJob.result!.baseline.cost.objective_eur + tinyDelta } },
      },
    } });
    window.location.hash = '#/comparisons/job-1'; render(<App />);
    expect(await screen.findByText('≈€0.000000')).toBeInTheDocument();
    expect(screen.getByText('在计算精度内，两种方案费用相同。')).toBeInTheDocument();
    expect(screen.queryByText('在本次假设下，替代方案费用更低。')).not.toBeInTheDocument();
    expect(screen.queryByText('−€0.000000')).not.toBeInTheDocument();
    expect(screen.getByText('≈€0.000000').closest('.delta-card')).not.toHaveClass('negative');
  });
  it('真实记录未注册模型时明确展示能力不可用', async () => {
    mockApi({ '/api/decisions/evidence-1': { ...decision, source_kind: 'external', snapshot: { ...decision.snapshot, sources: { pv: 'unknown', load: 'assumption' } }, capabilities: { counterfactual: false, supported_interventions: [] } } });
    window.location.hash = '#/decisions/evidence-1'; render(<App />);
    expect(await screen.findByText('该记录尚未注册对应模型，无法计算替代方案。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '计算替代方案' })).not.toBeInTheDocument();
    expect(screen.getByText('外部策略记录')).toBeInTheDocument();
    expect(screen.queryByText('模型接入验证 · 人工输入')).not.toBeInTheDocument();
  });
  it('历史模型接入的人工输入来源在总览、详情和比较中持续可见', async () => {
    const sources = { pv: 'synthetic', load: 'synthetic', pv_forecast: 'synthetic', load_forecast: 'assumption' };
    const validationOverview = {
      ...overview, run_id: 'external-run',
      stations: overview.stations.map((station) => ({ ...station, run_id: 'external-run', source_kind: 'external', sources })),
    };
    const validationDecision = {
      ...decision, run_id: 'external-run', source_kind: 'external',
      snapshot: { ...decision.snapshot, sources: { pv: 'unknown', load: 'unknown', pv_forecast: 'unknown', load_forecast: 'assumption' } },
      provenance: { origin: 'external_adapter', input_kind: 'synthetic', references: [], note: '实际历史策略源码，适配器明确声明人工输入用于接入验证。' },
    };
    mockApi({ '/api/stations': validationOverview, '/api/stations?run_id=external-run': validationOverview, '/api/decisions/evidence-1': validationDecision });
    render(<App />);
    expect(await screen.findByText('模型接入验证 · 人工输入')).toBeInTheDocument();
    expect(screen.getByText('快照光伏')).toBeInTheDocument();
    expect(screen.getByText('人工输入验证模型接入 · 执行与结算依据独立记录')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看决策依据' }));
    expect(await screen.findByRole('heading', { name: '低价充电站 · 决策依据' })).toBeInTheDocument();
    expect(screen.getByText('模型接入验证 · 人工输入')).toBeInTheDocument();
    expect(screen.getAllByText('来源未知')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: '计算替代方案' }));
    expect(await screen.findByRole('heading', { name: '如果换一种做法' })).toBeInTheDocument();
    expect(await screen.findByText('模型接入验证 · 人工输入')).toBeInTheDocument();
    expect(screen.queryByText('外部策略记录')).not.toBeInTheDocument();
  });
  it('超时保持独立状态，不解释为不可行', async () => {
    mockApi({ '/api/counterfactuals/job-1': { ...readyJob, status: 'timeout', result: null, error: '工作进程超过 30 秒。' } });
    window.location.hash = '#/comparisons/job-1'; render(<App />);
    expect(await screen.findByRole('heading', { name: '计算超时' })).toBeInTheDocument();
    expect(screen.getByText('计算超时无法判断替代方案是否可行。')).toBeInTheDocument();
    expect(screen.queryByText('约束不可行')).not.toBeInTheDocument();
  });
  it('不支持的问题返回明确范围，提供证据型追问入口', async () => {
    mockApi(); window.location.hash = '#/decisions/evidence-1'; render(<App />);
    const input = await screen.findByLabelText('你的问题');
    fireEvent.change(input, { target: { value: '给我买一块电池' } });
    fireEvent.click(screen.getByRole('button', { name: '提问' }));
    expect(await screen.findByText('支持原因、首步不充与功率边界问题。')).toBeInTheDocument();
    expect(screen.getByText('当前支持范围')).toBeInTheDocument();
  });
  it('任务排队后自动读取完成结果', async () => {
    let calls = 0;
    const fetchMock = mockApi();
    fetchMock.mockImplementation(async (path: string) => {
      if (path.includes('/api/decisions/')) return new Response(JSON.stringify(decision), { status: 200 });
      calls += 1;
      return new Response(JSON.stringify(calls === 1 ? { ...readyJob, status: 'running', result: null } : readyJob), { status: 200 });
    });
    window.location.hash = '#/comparisons/job-1'; render(<App />);
    expect(await screen.findByText('正在重新优化后续计划')).toBeInTheDocument();
    expect(await screen.findByText('−€0.500000', {}, { timeout: 2500 })).toBeInTheDocument();
    expect(calls).toBe(2);
  });
  it('读取失败时显示可重试错误，隐藏编造的站点', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: '服务不可用' }), { status: 503 })));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('服务不可用');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '低价充电站' })).not.toBeInTheDocument();
  });
  it.each([
    ['pending', 'official', '待结算'],
    ['complete', 'official', '官方已结算'],
    ['complete', 'demo', '人工结算记录'],
  ])('结算状态 %s 来源 %s 独立展示为 %s', async (status, source, expected) => {
    mockApi({ '/api/stations': { ...overview, settlement: { status, source_kind: source } }, '/api/stations?run_id=demo-run': { ...overview, settlement: { status, source_kind: source } } });
    render(<App />);
    expect(await screen.findByText(expected)).toBeInTheDocument();
    if (status === 'pending') expect(screen.queryByText('官方已结算')).not.toBeInTheDocument();
    if (source === 'demo') expect(screen.queryByText('官方已结算')).not.toBeInTheDocument();
  });
  it('采集年龄只使用响应墙钟，设备虚拟时钟单独展示', async () => {
    const timedOverview: Overview = {
      ...overview, server_time: '2026-10-13T01:00:42Z',
      stations: overview.stations.map((station) => ({ ...station, device_time: '2027-06-30T04:00:00Z', observed_at: '2026-10-13T01:00:00Z', created_at: '2026-10-13T01:00:02Z', record_freshness: 'fresh' })),
      collection: { state: 'running', last_success_at: '2026-10-13T01:00:00Z', updated_at: '2026-10-13T01:00:40Z', poll_interval_seconds: 5, freshness_threshold_seconds: 60 },
    };
    mockApi({ '/api/stations': timedOverview, '/api/stations?run_id=demo-run': timedOverview });
    render(<App />);
    expect(await screen.findByText('记录年龄（响应时）：42 秒')).toBeInTheDocument();
    expect(screen.getByText(/^设备时钟（UTC）：2027/)).toBeInTheDocument();
    expect(screen.getByText(/^采集墙钟（UTC）：2026/)).toBeInTheDocument();
    expect(screen.getByText('记录新鲜')).toBeInTheDocument();
    expect(screen.getByText('采集器运行中')).toBeInTheDocument();
    expect(screen.getByText(/三站整轮最近成功（UTC）：/)).toBeInTheDocument();
  });
  it('未来采集墙钟与后端未核验状态不会显示为新鲜', async () => {
    const futureOverview: Overview = {
      ...overview, server_time: '2026-10-13T01:00:00Z',
      stations: overview.stations.map((station) => ({ ...station, observed_at: '2026-10-13T01:00:05Z', record_freshness: 'clock_unverified' })),
      collection: { state: 'unknown' },
    };
    mockApi({ '/api/stations': futureOverview, '/api/stations?run_id=demo-run': futureOverview });
    render(<App />);
    expect(await screen.findByText('记录年龄（响应时）：时钟待核验')).toBeInTheDocument();
    expect(screen.getByText('采集时钟待核验')).toBeInTheDocument();
    expect(screen.getByText('采集器状态未知')).toBeInTheDocument();
    expect(screen.queryByText('记录新鲜')).not.toBeInTheDocument();
    expect(screen.queryByText('采集器运行中')).not.toBeInTheDocument();
  });
  it('自动刷新保持场次，可暂停和手动刷新，隐藏页面不继续请求', async () => {
    vi.useFakeTimers();
    let hidden = false;
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    const fetchMock = mockApi();
    await act(async () => { render(<App />); });
    const count = () => fetchMock.mock.calls.filter(([path]) => path.startsWith('/api/stations')).length;
    const initial = count();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(count()).toBe(initial + 1);
    expect(screen.getByLabelText('当前场次')).toHaveValue('demo-run');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '暂停自动刷新' })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(count()).toBe(initial + 1);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '刷新记录' })); });
    expect(count()).toBe(initial + 2);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开启自动刷新' })); });
    expect(count()).toBe(initial + 3);
    hidden = true; fireEvent(document, new Event('visibilitychange'));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(count()).toBe(initial + 3);
    hidden = false;
    await act(async () => { fireEvent(document, new Event('visibilitychange')); });
    expect(count()).toBe(initial + 4);
    expect(screen.getByLabelText('当前场次')).toHaveValue('demo-run');
  });
  it('自动轮询等待前一请求完成，等待期间保留站卡', async () => {
    vi.useFakeTimers();
    const fetchMock = mockApi();
    await act(async () => { render(<App />); });
    const initial = fetchMock.mock.calls.length;
    let finish: ((value: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(async () => new Promise<Response>((resolve) => { finish = resolve; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls).toHaveLength(initial + 1);
    expect(screen.getByRole('heading', { name: '低价充电站' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新记录' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetchMock.mock.calls).toHaveLength(initial + 1);
    await act(async () => { finish!(new Response(JSON.stringify(overview), { status: 200 })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls).toHaveLength(initial + 2);
  });
  it('刷新失败显示错误并保留上次成功的场次和记录', async () => {
    const fetchMock = mockApi(); render(<App />);
    await screen.findByRole('heading', { name: '低价充电站' });
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新记录' })).not.toBeDisabled());
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: '采集接口暂时离线' }), { status: 503 }));
    fireEvent.click(screen.getByRole('button', { name: '刷新记录' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('采集接口暂时离线');
    expect(screen.getByText(/刷新失败，显示上次成功记录/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '低价充电站' })).toBeInTheDocument();
    expect(screen.getByLabelText('当前场次')).toHaveValue('demo-run');
  });
  it('详情固定决策并显示采集墙钟、入库墙钟与年龄', async () => {
    mockApi({ '/api/decisions/evidence-1': { ...decision, server_time: '2026-10-13T01:00:21Z' } });
    window.location.hash = '#/decisions/evidence-1'; render(<App />);
    await screen.findByRole('heading', { name: '低价充电站 · 决策依据' });
    expect(screen.getByText('采集墙钟（UTC）')).toBeInTheDocument();
    expect(screen.getByText('入库墙钟（UTC）')).toBeInTheDocument();
    expect(screen.getByText('20 秒')).toBeInTheDocument();
    expect(screen.getByText(/采集状态为读取详情时的快照（服务响应 UTC 时间：/)).toHaveTextContent('返回总览刷新当前状态');
    expect(screen.getByText('尚无关联指令的回读证据')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('你的问题'), { target: { value: '保留正在输入的问题' } });
    expect(screen.getByLabelText('你的问题')).toHaveValue('保留正在输入的问题');
    expect(window.location.hash).toBe('#/decisions/evidence-1');
  });
  it('详情只读展示回读判定及中文原因，不将unsupported伪装为确认', async () => {
    const readbacks = [
      { observation: { observation_id: 'observation-1', instruction_id: 'instruction-1', observed_at: '2026-10-13T01:00:02Z' }, verification: { status: 'verified', reasons: [] } },
      { observation: { observation_id: 'observation-2', instruction_id: 'instruction-1', observed_at: '2026-10-13T01:00:03Z' }, verification: { status: 'pending', reasons: ['awaiting_device_tick', 'stale_readback', 'clock_unverified'] } },
      { observation: { observation_id: 'observation-3', instruction_id: 'instruction-1', observed_at: '2026-10-13T01:00:04Z' }, verification: { status: 'mismatch', reasons: ['setpoint_mismatch', 'power_balance_mismatch', 'unexpected_grid_import', 'upstream_custom_reason'] } },
      { observation: { observation_id: 'observation-4', instruction_id: 'instruction-2', observed_at: '2026-10-13T01:00:05Z' }, verification: { status: 'unsupported', reasons: ['missing_submission'] } },
    ];
    mockApi({ '/api/decisions/evidence-1': { ...decision, execution_evidence: { readbacks } } });
    window.location.hash = '#/decisions/evidence-1'; render(<App />);
    expect(await screen.findByText('提交后遥测一致')).toBeInTheDocument();
    expect(screen.getByText('待回读')).toBeInTheDocument();
    expect(screen.getByText('不一致')).toBeInTheDocument();
    expect(screen.getByText('缺少支持证据')).toBeInTheDocument();
    expect(screen.getByText('缺少对应提交回执')).toBeInTheDocument();
    expect(screen.getByText('等待提交后的设备时刻；回读记录过期；时钟待核验')).toBeInTheDocument();
    expect(screen.getByText('控制目标不一致；功率收支不一致；发生非预期购电；upstream_custom_reason')).toBeInTheDocument();
    const panel = screen.getByRole('heading', { name: '关联指令的回读证据' }).closest('section')!;
    expect(within(panel).getAllByRole('row')).toHaveLength(5);
    expect(within(panel).queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getAllByText('候选建议')).toHaveLength(2);
  });
  it.each([
    ['external', 'confirmation_unverified', 'unverified', '回读记录待核验'],
    ['demo', 'confirmed', undefined, '回读已确认'],
  ] as const)('旧确认来源 %s 应展示 %s 状态及正确时间线', async (source, executionStatus, verificationStatus, expected) => {
    const submitted = { event_id: 'legacy-submit', instruction_id: 'legacy-instruction', kind: 'submitted', issued_at: '2027-01-24T00:00:00Z', observed_at: null, device_time: null, note: '', observed_mode: null, observed_power_kw: null };
    const confirmed = { ...submitted, event_id: 'legacy-confirm', kind: 'confirmed', observed_at: '2026-10-13T01:00:03Z', device_time: '2027-01-24T00:15:00Z', observed_mode: 'charge', observed_power_kw: 3, verification_status: verificationStatus };
    mockApi({ '/api/decisions/evidence-1': { ...decision, source_kind: source, execution_status: executionStatus, execution_events: [submitted, confirmed] } });
    window.location.hash = '#/decisions/evidence-1'; render(<App />);
    expect(await screen.findAllByText(expected)).toHaveLength(3);
    const timeline = screen.getByRole('heading', { name: '执行时间线' }).closest('section')!;
    expect(within(timeline).getAllByText(expected)).toHaveLength(2);
    if (source === 'external') {
      expect(screen.queryByText('回读已确认')).not.toBeInTheDocument();
      expect(screen.getAllByText(expected)[0]).toHaveClass('badge-amber');
    } else expect(screen.queryByText('回读记录待核验')).not.toBeInTheDocument();
  });
});
