import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
});

describe('储能解释用户流程', () => {
  it('呈现人工来源、候选建议与正确场次，刷新保持选择', async () => {
    const fetchMock = mockApi(); render(<App />);
    expect(await screen.findByRole('heading', { name: '低价充电站' })).toBeInTheDocument();
    expect(screen.getByText('人工场景 · 未连接设备控制 · 无官方结算')).toBeInTheDocument();
    expect(screen.getByText('候选建议')).toBeInTheDocument();
    expect(screen.getByText('求解成功')).toBeInTheDocument();
    expect(screen.getByText('未结算')).toBeInTheDocument();
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
  it('提交零充电干预，完成导航并如实展示负费用差', async () => {
    const fetchMock = mockApi(); window.location.hash = '#/decisions/evidence-1'; render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '计算替代方案' }));
    expect(await screen.findByText('−€0.500000')).toBeInTheDocument();
    expect(screen.getByText('在本次假设下，替代方案费用更低。')).toBeInTheDocument();
    const request = fetchMock.mock.calls.find(([path]) => path === '/api/decisions/evidence-1/counterfactuals');
    expect(request).toBeDefined();
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ first_charge_kw: 0 });
    expect(window.location.hash).toBe('#/comparisons/job-1');
    expect(screen.getAllByRole('table')).toHaveLength(2);
    expect(screen.getByText(/不能据此推断 HA 控制模式/)).toBeInTheDocument();
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
  it('计划页提交异步任务并显示模型费用差', async () => {
    window.location.hash = '#/plans';
    const fetchMock = mockApi({
      '/api/plans/feasibility': { id: 'plan-1', decision_id: 'evidence-1', status: 'queued', result: null, error: null },
      '/api/plans/plan-1': { id: 'plan-1', decision_id: 'evidence-1', status: 'ready', error: null,
        result: { status: 'feasible', target_soc_percent: 80, expected_completion_step: 2,
          cost_delta_eur: 0.12, assumptions: ['保存快照模型估算。'] } },
    });
    render(<App />);
    fireEvent.change(await screen.findByLabelText('决策快照'), { target: { value: 'evidence-1' } });
    await waitFor(() => expect(screen.getByLabelText('设备时间截止点')).not.toHaveValue(''));
    fireEvent.click(screen.getByRole('button', { name: '检查计划可行性' }));
    expect(await screen.findByText('€0.1200', {}, { timeout: 2500 })).toBeInTheDocument();
    expect(screen.getByText('第 3 步')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path === '/api/plans/feasibility')).toBe(true);
  });
  it('光伏页标注回放、估算分摊与弃光未测', async () => {
    window.location.hash = '#/pv';
    const fetchMock = mockApi({ '/api/pv/1/daily?arm=zero_flow': { site_id: '1', arm: 'zero_flow', days: [{
      site_id: '1', device_date: '2031-01-01', arm: 'zero_flow', steps: 24,
      allocation_method: 'estimate_pv_first_to_load_then_battery_then_grid', source_kind: 'replay_trace',
      curtailment_status: 'not_measured', totals: { pv_generated_kwh: 3, direct_self_use_kwh: 1,
        pv_to_battery_kwh: 1, pv_to_grid_kwh: 1, grid_export_kwh: 1.5, negative_export_loss_eur: 0.1 },
      bottlenecks: { battery_full_steps: 2, charge_limit_steps: null, negative_price_export_steps: 1 },
      caveat: '光伏去向为估算；未提供潜在出力，不能核算弃光。',
    }] } });
    render(<App />);
    expect(await screen.findByText('2031-01-01')).toBeInTheDocument();
    expect(screen.getByText('直接供负载（估算）')).toBeInTheDocument();
    expect(screen.getByText('未提供功率上限')).toBeInTheDocument();
    expect(screen.getByText(/不能核算弃光/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('回放口径'), { target: { value: 'irradiance' } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path === '/api/pv/1/daily?arm=irradiance')).toBe(true));
  });
  it('历史练习臂显著标注五项证据边界', async () => {
    window.location.hash = '#/pv';
    mockApi({ '/api/pv/1/daily?arm=irradiance': { site_id: '1', arm: 'irradiance', days: [{
      site_id: '1', device_date: '2026-09-14', arm: 'irradiance', steps: 10,
      allocation_method: 'estimate_pv_first_to_load_then_battery_then_grid', source_kind: 'replay_trace',
      curtailment_status: 'not_measured', totals: { pv_generated_kwh: 3, direct_self_use_kwh: 1,
        pv_to_battery_kwh: 1, pv_to_grid_kwh: 1, grid_export_kwh: 1, negative_export_loss_eur: 0 },
      bottlenecks: { battery_full_steps: 0, charge_limit_steps: 0, negative_price_export_steps: 0 },
      caveat: '时间口径为 dataset_local_hour。',
    }] } });
    render(<App />);
    fireEvent.change(screen.getByLabelText('回放口径'), { target: { value: 'irradiance' } });
    const boundary = await screen.findByLabelText('三站历史回放证据边界');
    expect(boundary).toHaveTextContent('不是比赛现场三台设备');
    expect(boundary).toHaveTextContent('未来实测短波辐照度代理');
    expect(boundary).toHaveTextContent('not_measured');
    expect(boundary).toHaveTextContent('没有负价样本');
    expect(boundary).toHaveTextContent('不得用于在线 deadline 或跨时区比较');
    expect(boundary).toHaveTextContent('不是一条完整决策回放');
  });
});
