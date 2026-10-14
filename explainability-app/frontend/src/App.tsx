import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, type Cost, type Decision, type Explanation, type Job, type Overview, type PlanJob, type PVDaily, type Provenance, type QuestionAnswer, type Scenario, type Settlement, type Station } from './api';

type View = { kind: 'overview' } | { kind: 'decision'; id: string } | { kind: 'comparison'; id: string } | { kind: 'plans' } | { kind: 'pv' };
const actionNames: Record<string, string> = { charge: '充电', discharge: '放电', hold: '保持策略', self_consumption: '自用模式' };
const statusNames: Record<string, string> = {
  candidate: '候选建议', candidate_only: '候选建议', submitted: '已提交', confirmed: '回读已确认', failed: '失败',
  optimal: '求解成功', feasible: '可行解', ready: '可用', unavailable: '预测不可用', api_first: 'API 优先', api_ready: 'API 可用',
  model_unready: '模型未就绪', persistence: '当前观测延续', persistence_fallback: '已回退', fallback: '已回退',
  stale_history: '历史已过期', history_stale: '历史已过期', historical: '历史模型', demo: '人工演示',
  queued: '等待计算', running: '计算中', infeasible: '约束不可行', timeout: '计算超时', unsupported: '能力不可用',
};
function label(value: string) { return statusNames[value] ?? value; }
function controlLabel(value: string) { return ({ battery_charge: '电池充电功率', grid_charge: '并网点购电目标', grid_export_target: '并网点输出目标', self_consumption: '家庭自用', external: '外部定义' } as Record<string, string>)[value] ?? value; }
function sourceLabel(value: string) { return ({ price: '电价来源', weather: '天气来源', pv: '光伏观测来源', load: '负载观测来源', soc: '电量来源', pv_forecast: '光伏预测来源', load_forecast: '负载预测来源' } as Record<string, string>)[value] ?? value; }
function sourceValueLabel(value: string) { return ({ observed: '当前观测', predicted: '预测结果', synthetic: '人工数据', assumption: '模型假设', unknown: '来源未知' } as Record<string, string>)[value] ?? value; }
function historyLabel(value: string) { return ({ none: '未使用历史', live_history: '持续在线采集', frozen_baseline: '冻结回放缓存' } as Record<string, string>)[value] ?? value; }
function settlementLabel(value: unknown) {
  if (!value || typeof value !== 'object') return '未结算';
  const record = value as Settlement;
  if (record.status === 'pending') return '待结算';
  if (record.status === 'complete' && record.source_kind === 'official') return '官方已结算';
  if (record.status === 'complete' && record.source_kind === 'demo') return '人工结算记录';
  return '结算记录待核验';
}
function isDemo(value: string) { return /demo|synthetic|artificial/.test(value); }
function isModelValidation(source: string, sources?: Record<string, string>, provenance?: Provenance | null) {
  if (provenance?.origin === 'model_validation' || provenance?.input_kind === 'synthetic') return true;
  const values = Object.values(sources ?? {});
  return source === 'external' && values.length > 0 && values.every((value) => value === 'synthetic' || value === 'assumption');
}
function money(value: number, digits = 4) { return `€${value.toFixed(digits)}`; }
function power(value: number) { return `${value.toFixed(2)} kW`; }
function time(value: string | null | undefined, full = false) {
  if (!value) return '未提供';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    ...(full ? { year: 'numeric' } : {}),
  }).format(parsed);
}
function currentView(): View {
  const segments = window.location.hash.replace(/^#\/?/, '').split('/');
  if (segments[0] === 'decisions' && segments[1]) return { kind: 'decision', id: decodeURIComponent(segments[1]) };
  if (segments[0] === 'comparisons' && segments[1]) return { kind: 'comparison', id: decodeURIComponent(segments[1]) };
  if (segments[0] === 'plans') return { kind: 'plans' };
  if (segments[0] === 'pv') return { kind: 'pv' };
  return { kind: 'overview' };
}
function navigate(path = '/') { window.location.hash = path; }
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    bolt: <path d="m13 2-9 12h7l-1 8 10-13h-7l0-7Z" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    back: <path d="M19 12H5m5-5-5 5 5 5" />,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></>,
    battery: <><rect x="3" y="6" width="17" height="12" rx="2" /><path d="M22 10v4M7 10v4m4-4v4m4-4v4" /></>,
    home: <><path d="m3 10 9-7 9 7M5 9v12h14V9" /><path d="M9 21v-8h6v8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 6.1a8 8 0 0 1 13 2M4.9 15.9a8 8 0 0 0 13 2" /></>,
    compare: <><path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5m0-9v.1" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    chat: <path d="M21 11a8 8 0 0 1-8 8H8l-5 3V11a9 9 0 0 1 18 0Z" />,
    shield: <><path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4Z" /><path d="m8 11 3 3 5-6" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.info}</svg>;
}
function Badge({ value, tone }: { value: string; tone?: 'green' | 'amber' | 'muted' | 'red' }) {
  const inferred = /failed|infeasible|timeout/.test(value) ? 'red' : /fallback|stale|unready|submitted|unsupported/.test(value) ? 'amber' : /optimal|ready|confirmed/.test(value) ? 'green' : 'muted';
  return <span className={`badge badge-${tone ?? inferred}`}><span className="badge-dot" />{label(value)}</span>;
}
function SourceBadge({ source, sources, provenance }: { source: string; sources?: Record<string, string>; provenance?: Provenance | null }) {
  const validation = isModelValidation(source, sources, provenance);
  return <span className={`source-badge ${isDemo(source) || validation ? 'demo' : ''}`} title={provenance?.note}>{validation ? '模型接入验证 · 人工输入' : isDemo(source) ? '人工演示' : '外部策略记录'}</span>;
}
function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="error-box" role="alert"><Icon name="info" /><div><strong>暂时无法读取数据</strong><p>{message}</p></div>{retry && <button className="button secondary small" onClick={retry}>重试</button>}</div>;
}
function Loading({ text = '正在读取决策证据…' }: { text?: string }) { return <div className="loading" role="status"><span className="spinner" />{text}</div>; }
function PageTitle({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="page-title"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="subtitle">{description}</p></div>{action}</div>;
}
function StatusGrid({ solver, forecast, execution, settlement }: { solver: string; forecast: string; execution: string; settlement: unknown }) {
  const settled = settlementLabel(settlement);
  return <div className="status-grid"><div><span>求解</span><Badge value={solver} /></div><div><span>预测</span><Badge value={forecast} /></div><div><span>执行</span><Badge value={execution} /></div><div><span>结算</span><Badge value={settled} tone={settled === '官方已结算' ? 'green' : settled === '待结算' ? 'amber' : 'muted'} /></div></div>;
}
function BatteryRing({ percent }: { percent: number }) {
  const safe = Math.min(100, Math.max(0, percent));
  return <div className="battery-ring" aria-label={`电池电量 ${percent.toFixed(0)}%`}>
    <svg viewBox="0 0 100 100" aria-hidden="true"><circle className="ring-track" cx="50" cy="50" r="41" /><circle className="ring-value" cx="50" cy="50" r="41" strokeDasharray={`${safe * 2.576} 257.6`} /></svg>
    <div><strong>{percent.toFixed(0)}<small>%</small></strong><span>SOC 电量</span></div>
  </div>;
}
function StationCard({ station, index, settlement }: { station: Station; index: number; settlement: Settlement | null }) {
  return <article className="station-card">
    <div className="station-card-top"><span className="site-label">SITE {String(index + 1).padStart(2, '0')}</span><SourceBadge source={station.source_kind} sources={station.sources} provenance={station.provenance} /></div>
    <h3>{station.site_id}</h3>
    <p className="device-time"><Icon name="clock" size={14} />{time(station.device_time)} UTC · 设备时间</p>
    <div className="station-energy"><BatteryRing percent={station.soc_percent} /><div className="energy-readings"><div><Icon name="sun" /><span>快照光伏<strong>{power(station.pv_kw)}</strong></span></div><div><Icon name="home" /><span>输入负载<strong>{power(station.load_kw)}</strong></span></div></div></div>
    <div className="recommendation"><span>当前建议</span><strong>{actionNames[station.action.kind] ?? station.action.kind}<em>{power(station.action.power_kw)}</em></strong></div>
    <p className="reason-summary">{station.reason_summary || '等待策略模块提供决策依据。'}</p>
    <StatusGrid solver={station.solver_status} forecast={station.forecast_status} execution={station.execution_status} settlement={settlement} />
    <button className="card-link" onClick={() => navigate(`/decisions/${encodeURIComponent(station.id)}`)}>查看决策依据<Icon name="arrow" size={17} /></button>
  </article>;
}
function OverviewPage({ selectedRun, selectRun }: { selectedRun: string; selectRun: (run: string) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    api.stations(selectedRun || undefined).then((next) => {
      if (active) { setData(next); if (!selectedRun && next.run_id) selectRun(next.run_id); }
    }).catch((reason: Error) => { if (active) setError(reason.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [selectedRun, selectRun, refresh]);
  const activeData = !selectedRun || data?.run_id === selectedRun ? data : null;
  const stations = activeData?.stations ?? [];
  const confirmed = stations.filter((station) => station.execution_status === 'confirmed').length;
  const fallback = stations.filter((station) => /fallback|stale|unready|persistence/.test(station.forecast_status)).length;
  const modelValidation = stations.length > 0 && stations.every((station) => isModelValidation(station.source_kind, station.sources, station.provenance));
  return <>
    <PageTitle eyebrow="ENERGY DECISION OBSERVATORY" title="让每一度电都有理由" description="看懂当前建议，比较替代方案，追踪真实执行。" action={<button className="button secondary" onClick={() => setRefresh((n) => n + 1)} disabled={loading}><Icon name="refresh" size={17} />刷新记录</button>} />
    <div className="context-bar"><div className="run-select"><label htmlFor="run-select">当前场次</label><select id="run-select" value={selectedRun} onChange={(event) => selectRun(event.target.value)}>{!selectedRun && <option value="">最近场次</option>}{data?.runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id}{isDemo(run.source_kind) ? ' · 人工演示' : ''}</option>)}</select></div><div className="context-note"><Icon name="shield" size={17} />{modelValidation ? '人工输入验证模型接入 · 执行与结算依据独立记录' : stations.length && stations.every((station) => isDemo(station.source_kind)) ? '人工场景 · 未连接设备控制 · 无官方结算' : '页面依据导入记录展示状态'}</div></div>
    {error && <ErrorBox message={error} retry={() => setRefresh((n) => n + 1)} />}
    {!activeData && loading ? <Loading /> : !activeData && error ? null : <>
      <div className="summary-strip"><div><span className="metric-icon"><Icon name="grid" /></span><span>站点记录<strong>{stations.length}<small>个</small></strong></span></div><div><span className="metric-icon"><Icon name="check" /></span><span>回读确认<strong>{confirmed}<small>个</small></strong></span></div><div><span className="metric-icon amber"><Icon name="info" /></span><span>回退或延续估计<strong>{fallback}<small>个</small></strong></span></div><div><span className="metric-icon"><Icon name="sun" /></span><span>快照光伏合计<strong>{stations.reduce((total, station) => total + station.pv_kw, 0).toFixed(2)}<small>kW</small></strong></span></div></div>
      <div className="section-heading"><h2>站点决策</h2><span>每条建议都关联独立快照</span></div>
      {stations.length ? <div className="station-grid">{stations.map((station, index) => <StationCard station={station} index={index} settlement={activeData?.settlement ?? null} key={station.id} />)}</div> : <div className="empty-state"><Icon name="battery" size={32} /><h2>这个场次还没有决策</h2><p>通过决策导入接口添加记录，或运行演示数据初始化命令。</p></div>}
      <div className="explainer-strip"><div className="explainer-icon"><Icon name="shield" size={24} /></div><div><h3>建议、执行和收益，各有证据</h3><p>求解成功表示找到可行方案。只有关联指令的回读记录才能确认执行；官方收益需要结算结果。</p></div><span className="outline-label">事实优先</span></div>
    </>}
  </>;
}
function CostBreakdown({ cost, caption }: { cost: Cost | null; caption?: string }) {
  if (!cost) return <p className="muted">尚未提供费用分项。</p>;
  return <div className="cost-breakdown">{caption && <p className="cost-caption">{caption}</p>}<div><span>购电支出</span><strong>{money(cost.purchase_eur)}</strong></div><div><span>售电收入</span><strong>− {money(cost.export_revenue_eur)}</strong></div><div><span>电池损耗</span><strong>{money(cost.degradation_eur)}</strong></div><div><span>期末库存价值</span><strong>− {money(cost.terminal_credit_eur)}</strong></div><div className="cost-total"><span>模型目标费用</span><strong>{money(cost.objective_eur, 6)}</strong></div></div>;
}
function SeriesChart({ values, title }: { values: number[]; title: string }) {
  if (!values.length) return <p className="muted">未提供{title}数据。</p>;
  const max = Math.max(...values, 0.001); const min = Math.min(...values, 0); const range = max - min || 1;
  const points = values.map((value, i) => `${24 + i * 302 / Math.max(1, values.length - 1)},${100 - (value - min) / range * 76}`).join(' ');
  const tickIndices = values.length <= 8 ? values.map((_, i) => i) : Array.from({ length: 8 }, (_, i) => Math.round(i * (values.length - 1) / 7));
  return <div className="series-chart"><svg viewBox="0 0 350 132" role="img" aria-label={`${title}：${values.map((value) => value.toFixed(3)).join('，')}`}><path d="M24 100H326M24 62H326M24 24H326" stroke="#e8eeeb" /><polyline points={points} stroke="#138774" strokeWidth="2.5" fill="none" />{tickIndices.map((i) => <g key={i}><circle cx={24 + i * 302 / Math.max(1, values.length - 1)} cy={100 - (values[i] - min) / range * 76} r="4" fill="#138774" /><text x={24 + i * 302 / Math.max(1, values.length - 1)} y="123" textAnchor="middle" fill="#7d8b88" fontSize="10">{i === 0 ? '首步' : `+${i}`}</text></g>)}</svg><div><span>{title}</span><strong>{values[0].toFixed(3)} EUR/kWh<small>首步</small></strong></div></div>;
}
function EvidenceRefs({ values }: { values: string[] }) { return <div className="evidence-refs">{values.map((value) => <code key={value} title={value}>{value}</code>)}</div>; }
function QuestionBox({ decision }: { decision: Decision }) {
  const [question, setQuestion] = useState(''); const [answer, setAnswer] = useState<QuestionAnswer | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(text: string) {
    if (!text.trim()) return;
    setQuestion(text); setBusy(true); setError(''); setAnswer(null);
    try { setAnswer(await api.question(decision.id, text)); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel question-panel"><div className="panel-heading"><Icon name="chat" /><h2>继续问一个问题</h2><span className="outline-label">有限意图</span></div><p className="muted">回答引用当前证据；替代方案通过模型重新计算。</p><div className="question-chips">{['为什么这样做？', '首步不充会怎样？', '为什么不能增加功率？'].map((text) => <button key={text} onClick={() => void submit(text)} disabled={busy}>{text}</button>)}</div><form className="question-form" onSubmit={(event: FormEvent) => { event.preventDefault(); void submit(question); }}><input aria-label="你的问题" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="询问原因、首步不充，或功率边界" maxLength={500} /><button className="button primary" disabled={busy || !question.trim()}>{busy ? '读取证据…' : '提问'}<Icon name="arrow" size={16} /></button></form>{error && <ErrorBox message={error} />}{answer && <div className={`answer-box ${answer.intent === 'unsupported' ? 'answer-unsupported' : ''}`} role="status"><strong>{answer.intent === 'unsupported' ? '当前支持范围' : '基于当前决策的回答'}</strong><p>{answer.answer}</p><EvidenceRefs values={answer.evidence_refs} />{answer.job && <button className="card-link" onClick={() => navigate(`/comparisons/${encodeURIComponent(answer.job!.id)}`)}>打开替代方案任务 · {label(answer.job.status)}<Icon name="arrow" size={16} /></button>}</div>}</section>;
}
function CompareForm({ decision }: { decision: Decision }) {
  const [value, setValue] = useState('0'); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > decision.snapshot.max_charge_kw) { setError('功率须在 0 到最大充电功率之间。'); return; }
    setBusy(true); setError('');
    try { const job = await api.compare(decision.id, parsed); navigate(`/comparisons/${encodeURIComponent(job.id)}`); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel compare-request"><div className="panel-heading"><Icon name="compare" /><h2>如果首步换一种做法</h2></div><p>固定首步电池充电功率，重新优化后续计划。</p>{!decision.capabilities.counterfactual ? <div className="capability-note"><Badge value="unsupported" /><p>该记录尚未注册对应模型，无法计算替代方案。</p></div> : <form onSubmit={(event) => void submit(event)}><label htmlFor="first-charge">首步电池充电功率</label><div className="compare-input"><input id="first-charge" type="number" min="0" max={decision.snapshot.max_charge_kw} step="0.01" required value={value} onChange={(event) => setValue(event.target.value)} /><span>kW</span></div><small>0 kW 表示首步禁止电池充电；控制动作由策略模块定义。</small><button className="button primary full" disabled={busy}>{busy ? '创建任务…' : '计算替代方案'}<Icon name="arrow" size={17} /></button></form>}{error && <ErrorBox message={error} />}</section>;
}
function DecisionPage({ id }: { id: string }) {
  const [data, setData] = useState<Decision | null>(null); const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setData(null); setExplanation(null);
    Promise.all([api.decision(id), api.explanation(id)]).then(([decision, reasons]) => { if (active) { setData(decision); setExplanation(reasons); } }).catch((reason: Error) => { if (active) setError(reason.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, refresh]);
  if (loading) return <Loading />;
  if (error || !data || !explanation) return <ErrorBox message={error || '未找到这条决策。'} retry={() => setRefresh((n) => n + 1)} />;
  const snapshot = data.snapshot;
  return <>
    <button className="back-link" onClick={() => navigate('/')}><Icon name="back" size={16} />返回站点总览</button>
    <PageTitle eyebrow="DECISION EVIDENCE" title={`${data.site_id} · 决策依据`} description={`设备时间 ${time(snapshot.device_time, true)} UTC · 场次 ${data.run_id}`} action={<SourceBadge source={data.source_kind} sources={snapshot.sources} provenance={data.provenance} />} />
    <div className="decision-hero"><div><p>当前策略建议</p><h2>{actionNames[data.action.kind] ?? data.action.kind}<span>{power(data.action.power_kw)}</span></h2><p className="hero-reason">{explanation.summary}</p><span className="control-note">功率含义：{controlLabel(data.action.control_semantics)}</span></div><BatteryRing percent={snapshot.soc_percent} /></div>
    <StatusGrid solver={data.solver_status} forecast={snapshot.forecast_status} execution={data.execution_status} settlement={data.settlement} />
    <div className="detail-layout"><div className="detail-main">
      <section className="panel"><div className="panel-heading"><Icon name="shield" /><h2>为什么这样做</h2><span className="outline-label">规则生成 · 有证据</span></div><div className="fact-list">{explanation.facts.map((fact, index) => <article className="fact" key={fact.id}><span className="fact-number">{String(index + 1).padStart(2, '0')}</span><div><p>{fact.text}</p><EvidenceRefs values={fact.evidence_refs} /></div></article>)}</div><p className="scope-note">{explanation.scope}</p></section>
      <section className="panel"><div className="panel-heading"><Icon name="sun" /><h2>数据与假设</h2></div><div className="data-summary"><div><span>输入光伏</span><strong>{power(snapshot.pv_kw)}</strong></div><div><span>输入负载</span><strong>{power(snapshot.load_kw)}</strong></div><div><span>充电上限</span><strong>{power(snapshot.max_charge_kw)}</strong></div><div><span>步长</span><strong>{snapshot.step_hours} 小时</strong></div></div><SeriesChart values={snapshot.import_eur_kwh} title="购电价格" /><div className="source-grid">{Object.entries(snapshot.sources).map(([key, value]) => <div key={key}><span>{sourceLabel(key)}</span><strong>{sourceValueLabel(value)}</strong></div>)}<div><span>历史模式</span><strong>{historyLabel(snapshot.history_mode)}</strong></div><div><span>历史尾部（UTC）</span><strong>{time(snapshot.cache_tail, true)}</strong></div></div>{snapshot.fallback_reason && <div className="fallback-note"><Icon name="info" /><div><strong>当前使用降级方案</strong><p>{snapshot.fallback_reason}</p></div></div>}</section>
      <section className="panel"><div className="panel-heading"><Icon name="clock" /><h2>执行时间线</h2><Badge value={data.execution_status} /></div><div className="timeline"><div className="timeline-item"><span className="timeline-dot" /><div><strong>候选决策已生成</strong><p>{time(data.created_at, true)} UTC · {data.decision_id}</p></div></div>{data.execution_events.map((event) => <div className={`timeline-item ${event.kind}`} key={event.event_id}><span className="timeline-dot" /><div><strong>{label(event.kind)}</strong><p>{time(event.observed_at ?? event.issued_at, true)} UTC · 指令 {event.instruction_id}</p>{event.device_time && <p>设备时间 {time(event.device_time, true)} UTC</p>}{event.note && <p>{event.note}</p>}{event.observed_mode && <p>回读：{event.observed_mode}{event.observed_power_kw != null ? ` · ${power(event.observed_power_kw)}` : ''}</p>}</div></div>)}</div>{!data.execution_events.length && <p className="scope-note">目前只有建议，尚无指令提交或设备回读记录。</p>}</section>
      <QuestionBox decision={data} />
    </div><aside className="detail-aside"><CompareForm decision={data} /><section className="panel"><div className="panel-heading"><h2>预计费用</h2></div><CostBreakdown cost={data.cost} /><p className="scope-note">费用依据本次输入及模型假设计算。{settlementLabel(data.settlement)}。</p>{data.settlement && <pre className="settlement-record">{JSON.stringify(data.settlement, null, 2)}</pre>}</section><section className="panel provenance-panel"><h2>证据留档</h2><dl><dt>模型版本</dt><dd>{data.model_version}</dd><dt>策略版本</dt><dd>{data.policy_version}</dd><dt>决策 ID</dt><dd>{data.decision_id}</dd><dt>快照 SHA-256</dt><dd className="hash">{data.snapshot_hash}</dd></dl></section></aside></div>
  </>;
}
function PlanTable({ scenario }: { scenario: Scenario }) {
  return <div className="table-scroll"><table><caption className="sr-only">逐步电池及并网计划</caption><thead><tr><th>步骤</th><th>充电</th><th>放电</th><th>购电</th><th>售电</th><th>SOC</th></tr></thead><tbody>{scenario.plan.map((step) => <tr key={step.step}><td>{step.step === 0 ? '首步' : `+${step.step}`}</td><td>{step.charge_kw.toFixed(2)}</td><td>{step.discharge_kw.toFixed(2)}</td><td>{step.grid_import_kw.toFixed(2)}</td><td>{step.grid_export_kw.toFixed(2)}</td><td>{step.soc_end_percent.toFixed(1)}%</td></tr>)}</tbody></table><p className="table-unit">充放电与购售电功率单位：kW</p></div>;
}
function ScenarioPanel({ title, scenario, alternative = false }: { title: string; scenario: Scenario; alternative?: boolean }) {
  return <section className={`panel scenario-panel ${alternative ? 'alternative' : ''}`}><div className="scenario-heading"><span className="scenario-letter">{alternative ? 'B' : 'A'}</span><div><p>{alternative ? 'FIRST-STEP INTERVENTION' : 'RECORDED MODEL BASELINE'}</p><h2>{title}</h2></div><Badge value={scenario.status} /></div><CostBreakdown cost={scenario.cost} /><h3>逐步计划</h3><PlanTable scenario={scenario} /></section>;
}
function ComparisonPage({ id }: { id: string }) {
  const [job, setJob] = useState<Job | null>(null); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const [source, setSource] = useState<string | null>(null);
  const [inputSources, setInputSources] = useState<Record<string, string> | undefined>();
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout> | undefined; let sourceRequested = false;
    setJob(null); setError(''); setSource(null); setInputSources(undefined); setProvenance(null);
    async function load() {
      try {
        const next = await api.job(id);
        if (!active) return;
        setJob(next);
        if (!sourceRequested) {
          sourceRequested = true;
          void api.decision(next.decision_id).then((decision) => { if (active) { setSource(decision.source_kind); setInputSources(decision.snapshot.sources); setProvenance(decision.provenance ?? null); } }).catch(() => { if (active) setSource('unknown'); });
        }
        if (next.status === 'queued' || next.status === 'running') timer = setTimeout(() => void load(), 1200);
      } catch (reason) { if (active) setError((reason as Error).message); }
    }
    void load(); return () => { active = false; clearTimeout(timer); };
  }, [id, refresh]);
  return <>
    <button className="back-link" onClick={() => job ? navigate(`/decisions/${encodeURIComponent(job.decision_id)}`) : navigate('/')}><Icon name="back" size={16} />返回决策依据</button>
    <PageTitle eyebrow="COUNTERFACTUAL COMPARISON" title="如果换一种做法" description="同一输入快照、同一模型，固定首步动作后重新优化。" action={source && source !== 'unknown' ? <SourceBadge source={source} sources={inputSources} provenance={provenance} /> : <span className="source-badge">{source === 'unknown' ? '记录来源待核验' : '正在读取记录来源'}</span>} />
    {error && <ErrorBox message={error} retry={() => setRefresh((n) => n + 1)} />}
    {!job && !error && <Loading text="正在读取替代方案任务…" />}
    {job && <><div className="comparison-context"><div><Icon name="compare" /><span>首步电池充电固定为 <strong>{power(job.first_charge_kw)}</strong></span></div><Badge value={job.status === 'ready' ? '计算完成' : job.status} tone={job.status === 'ready' ? 'green' : undefined} /></div>
      {(job.status === 'queued' || job.status === 'running') && <div className="job-pending" role="status"><span className="spinner" /><h2>{job.status === 'queued' ? '任务已入队，等待模型工作进程' : '正在重新优化后续计划'}</h2><p>页面会自动更新。普通原因解释仍可直接查看。</p></div>}
      {job.status !== 'queued' && job.status !== 'running' && job.status !== 'ready' && <div className="job-message" role="status"><Icon name="info" size={30} /><h2>{label(job.status)}</h2><p>{job.error || '此次比较没有可展示的模型结果。'}</p>{job.status === 'timeout' && <p>计算超时无法判断替代方案是否可行。</p>}{job.status === 'unsupported' && <p>需要为这条真实记录注册对应模型适配器。</p>}</div>}
      {job.status === 'ready' && job.result && <><div className={`delta-card ${job.result.delta_eur < 0 ? 'negative' : ''}`}><div><p>替代方案费用 − 原方案费用</p><h2>{job.result.delta_eur >= 0 ? '+' : '−'}{money(Math.abs(job.result.delta_eur), 6)}</h2><span>{job.result.delta_eur > 0 ? '在本次假设下，替代方案费用更高。' : job.result.delta_eur < 0 ? '在本次假设下，替代方案费用更低。' : '在本次假设下，两种方案费用相同。'}</span></div><div className="delta-icon"><Icon name="compare" size={34} /></div></div><div className="scenario-grid"><ScenarioPanel title="原方案" scenario={job.result.baseline} /><ScenarioPanel title="替代方案" scenario={job.result.alternative} alternative /></div><section className="panel assumptions-panel"><div className="panel-heading"><Icon name="info" /><h2>比较成立的假设</h2></div><ul>{job.result.assumptions.map((assumption, index) => <li key={index}>{assumption}</li>)}</ul><p className="scope-note">这是模型费用比较，不代表实际执行收益或官方比赛成绩。0 kW 干预仅约束电池首步充电，不能据此推断 HA 控制模式。</p></section></>}
    </>}
  </>;
}
function PlanPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [decisionId, setDecisionId] = useState('');
  const [target, setTarget] = useState(80);
  const [deadline, setDeadline] = useState('');
  const [job, setJob] = useState<PlanJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.stations().then(setOverview).catch((e: Error) => setError(e.message)); }, []);
  useEffect(() => {
    if (!decisionId) { setDecision(null); setDeadline(''); return; }
    let active = true;
    api.decision(decisionId).then((next) => {
      if (!active) return;
      setDecision(next);
      const nextDeadline = new Date(new Date(next.snapshot.device_time).getTime() + next.snapshot.step_hours * Math.min(4, next.snapshot.pv_forecast_kw.length) * 3600000);
      const local = new Date(nextDeadline.getTime() - nextDeadline.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      setDeadline(local);
    }).catch((e: Error) => setError(e.message));
    return () => { active = false; };
  }, [decisionId]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    const timer = setTimeout(() => api.planJob(job.id).then(setJob).catch((e: Error) => setError(e.message)), 900);
    return () => clearTimeout(timer);
  }, [job]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!decision || !deadline) return;
    setBusy(true); setError(''); setJob(null);
    try { setJob(await api.plan(decision.id, target, new Date(deadline).toISOString())); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <PageTitle eyebrow="PLAN FEASIBILITY" title="计划可行性检查" description="使用已保存的决策快照评估目标电量与截止时间；不会读取新设备状态或下发控制。" />
    {error && <ErrorBox message={error} />}
    {!overview && !error && <Loading />}
    {overview && <form className="panel plan-form" onSubmit={(e) => void submit(e)}>
      <label>决策快照<select aria-label="决策快照" value={decisionId} onChange={(e) => { setDecisionId(e.target.value); setJob(null); }} required>
        <option value="">选择站点决策</option>{overview.stations.map((station) => <option key={station.id} value={station.id}>{station.site_id} · {time(station.device_time, true)} UTC</option>)}
      </select></label>
      <label>目标电量 SOC (%)<input aria-label="目标电量 SOC (%)" type="number" min="1" max="100" step="1" value={target} onChange={(e) => setTarget(Number(e.target.value))} required /></label>
      <label>设备时间截止点<input aria-label="设备时间截止点" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} required /></label>
      <button className="button primary" disabled={!decision || busy || !deadline}>{busy ? '提交中…' : '检查计划可行性'}</button>
      {decision && <p className="scope-note">当前模型：{decision.model_version} · 预测步数：{decision.snapshot.pv_forecast_kw.length} · 步长：{decision.snapshot.step_hours} 小时</p>}
    </form>}
    {job && <section className="panel plan-result" aria-live="polite">
      <div className="panel-heading"><h2>检查结果</h2><Badge value={job.status} /></div>
      {['queued', 'running'].includes(job.status) && <div className="job-pending"><span className="spinner" /><p>{job.status === 'queued' ? '任务已入队，等待模型工作进程' : '正在检查截止电量约束'}</p></div>}
      {!['queued', 'running'].includes(job.status) && !job.result && <p role="status">{job.error || '任务没有返回结果。'}</p>}
      {job.result && <>
        {job.result.message && <p className="scope-note">{job.result.message}</p>}
        {job.result.status === 'feasible' && <div className="plan-metrics"><div><span>预计完成</span><strong>{job.result.expected_completion_step == null ? '已满足' : `第 ${job.result.expected_completion_step + 1} 步`}</strong></div><div><span>预计费用差</span><strong>{money(job.result.cost_delta_eur ?? 0, 4)}</strong></div><div><span>截止目标</span><strong>{job.result.target_soc_percent?.toFixed(0)}%</strong></div></div>}
        {job.result.max_reachable_energy_kwh != null && <p>截止前最大可达电量估算：<strong>{job.result.max_reachable_energy_kwh.toFixed(2)} kWh</strong></p>}
        {!!job.result.adjustments?.length && <><h3>可调整方案</h3><div className="table-scroll"><table><thead><tr><th>调整</th><th>目标/截止</th><th>模型费用差</th></tr></thead><tbody>{job.result.adjustments.map((item, i) => <tr key={i}><td>{item.kind === 'lower_target' ? '降低目标电量' : '延后截止'}</td><td>{item.target_soc_percent != null ? `${item.target_soc_percent}%` : time(item.deadline, true)}</td><td>{money(item.cost_delta_eur, 4)}</td></tr>)}</tbody></table></div></>}
        {job.result.assumptions?.map((item, i) => <p className="scope-note" key={i}>{item}</p>)}
      </>}
    </section>}
  </>;
}
function PVPage() {
  const [siteId, setSiteId] = useState('1'); const [days, setDays] = useState<PVDaily[]>([]);
  const [error, setError] = useState(''); const [message, setMessage] = useState(''); const [busy, setBusy] = useState(false);
  async function load() {
    setError('');
    try { setDays((await api.pvDaily(siteId)).days); }
    catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { void load(); }, [siteId]);
  async function importFile(file?: File) {
    if (!file) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const reports = file.text ? (await file.text()).split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line) as PVDaily) : [];
      for (const report of reports) await fetch('/api/pv/accounting', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(report) }).then(async (response) => { if (!response.ok) throw new Error((await response.json()).detail || `导入失败 (${response.status})`); });
      setMessage(`已导入 ${reports.length} 条站点日核算记录。`); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <PageTitle eyebrow="PV ACCOUNTING" title="光伏消纳分析" description="按回放功率平衡估算光伏去向，并单独展示 trace 中记录的负价外送损失。" />
    <div className="pv-toolbar"><label>站点<input aria-label="站点编号" value={siteId} onChange={(e) => setSiteId(e.target.value)} /></label><label className="button secondary">导入 JSONL<input aria-label="导入光伏 JSONL" type="file" accept=".jsonl,.ndjson,application/json" disabled={busy} onChange={(e) => void importFile(e.target.files?.[0])} /></label><button className="button secondary" onClick={() => void load()}>刷新</button></div>
    {message && <p role="status">{message}</p>}{error && <ErrorBox message={error} retry={() => void load()} />}
    {!error && days.length === 0 && <div className="empty-state"><Icon name="sun" size={32} /><h2>暂无光伏核算记录</h2><p>导入由回放 trace 生成的 JSONL 文件后查看站点日汇总。</p></div>}
    <div className="pv-day-list">{days.map((day) => {
      const total = day.totals.pv_generated_kwh || 0; const parts = [day.totals.direct_self_use_kwh, day.totals.pv_to_battery_kwh, day.totals.pv_to_grid_kwh];
      const colors = ['#168b77', '#e3a934', '#5687a1']; const titles = ['直接供负载（估算）', '进入电池（估算）', '光伏外送（估算）'];
      return <article className="pv-day" key={`${day.site_id}-${day.device_date}-${day.arm}`}><div className="pv-day-heading"><h2>{day.device_date}</h2><span className="source-badge">回放 trace</span><Badge value={day.arm} /></div><div className="pv-total"><strong>{total.toFixed(2)} kWh</strong><span>trace 光伏发电量</span></div><div className="pv-stack" role="img" aria-label={`光伏去向估算：${titles.map((t, i) => `${t} ${parts[i].toFixed(2)} 千瓦时`).join('，')}`}>{parts.map((part, i) => <span key={titles[i]} style={{ width: `${total > 0 ? part / total * 100 : 0}%`, background: colors[i] }} />)}</div><div className="pv-legend">{parts.map((part, i) => <div key={titles[i]}><i style={{ background: colors[i] }} /><span>{titles[i]}<strong>{part.toFixed(2)} kWh</strong></span></div>)}</div><div className="plan-metrics"><div><span>电网外送（trace）</span><strong>{day.totals.grid_export_kwh.toFixed(2)} kWh</strong></div><div><span>负价外送损失（trace）</span><strong>{money(day.totals.negative_export_loss_eur, 4)}</strong></div><div><span>电池已满时段</span><strong>{day.bottlenecks.battery_full_steps}</strong></div><div><span>充电功率瓶颈</span><strong>{day.bottlenecks.charge_limit_steps == null ? '未提供功率上限' : day.bottlenecks.charge_limit_steps}</strong></div><div><span>负价外送时段</span><strong>{day.bottlenecks.negative_price_export_steps}</strong></div></div><p className="scope-note">{day.caveat}</p></article>;
    })}</div>
  </>;
}
export default function App() {
  const [view, setView] = useState<View>(currentView); const [selectedRun, setSelectedRun] = useState('');
  useEffect(() => { const listener = () => { setView(currentView()); window.scrollTo?.(0, 0); }; window.addEventListener('hashchange', listener); return () => window.removeEventListener('hashchange', listener); }, []);
  const currentName = view.kind === 'overview' ? '站点总览' : view.kind === 'decision' ? '决策依据' : view.kind === 'comparison' ? '替代方案' : view.kind === 'plans' ? '计划可行性' : '光伏分析';
  return <div className="app-shell"><a className="skip-link" href="#main" onClick={(event) => { event.preventDefault(); document.getElementById('main')?.focus(); }}>跳转到内容</a><aside className="sidebar"><a className="brand" href="#/"><span className="brand-mark"><Icon name="bolt" size={25} /></span><span>电到为止<small>ENERGY EXPLAINABILITY</small></span></a><span className="nav-caption">工作空间</span><nav aria-label="主导航"><a href="#/" className={view.kind === 'overview' ? 'active' : ''}><Icon name="grid" />站点总览</a><span className={view.kind === 'decision' ? 'active current-nav' : 'current-nav'}><Icon name="shield" />决策依据</span><span className={view.kind === 'comparison' ? 'active current-nav' : 'current-nav'}><Icon name="compare" />替代方案</span><a href="#/plans" className={view.kind === 'plans' ? 'active' : ''}><Icon name="check" />计划检查</a><a href="#/pv" className={view.kind === 'pv' ? 'active' : ''}><Icon name="sun" />光伏分析</a></nav><div className="sidebar-foot"><span className="live-dot" /><strong>独立解释框架</strong><p>事实留档 · 模型比较<br />执行追踪</p><span className="version">LOCAL / v0.1</span></div></aside><div className="app-body"><header className="topbar"><span className="breadcrumb">家庭储能<span>/</span>{currentName}</span><div><span className="topbar-note">决策透明，安心用电</span><span className="topbar-pill"><span className="live-dot" />本地解释台</span></div></header><main id="main" tabIndex={-1}>{view.kind === 'overview' ? <OverviewPage selectedRun={selectedRun} selectRun={setSelectedRun} /> : view.kind === 'decision' ? <DecisionPage id={view.id} key={view.id} /> : view.kind === 'comparison' ? <ComparisonPage id={view.id} key={view.id} /> : view.kind === 'plans' ? <PlanPage /> : <PVPage />}<footer className="page-footer"><span>电到为止 · 储能决策解释台</span><span>证据可追溯，假设可检查</span></footer></main></div></div>;
}
