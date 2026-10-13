import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { api, type CollectionStatus, type Cost, type Decision, type Explanation, type Job, type Overview, type Provenance, type QuestionAnswer, type RecordFreshness, type Scenario, type Settlement, type Station } from './api';

type View = { kind: 'overview' } | { kind: 'decision'; id: string } | { kind: 'comparison'; id: string };
const actionNames: Record<string, string> = { charge: '充电', discharge: '放电', hold: '保持策略', self_consumption: '自用模式' };
const statusNames: Record<string, string> = {
  candidate: '候选建议', candidate_only: '候选建议', submitted: '已提交', confirmed: '回读已确认', confirmation_unverified: '回读记录待核验', failed: '失败',
  optimal: '求解成功', feasible: '可行解', ready: '可用', unavailable: '预测不可用', api_first: 'API 优先', api_ready: 'API 可用',
  model_unready: '模型未就绪', persistence: '当前观测延续', persistence_fallback: '已回退', fallback: '已回退',
  stale_history: '历史已过期', history_stale: '历史已过期', historical: '历史模型', demo: '人工演示',
  queued: '等待计算', running: '计算中', infeasible: '约束不可行', timeout: '计算超时', unsupported: '能力不可用',
};
function label(value: string) { return statusNames[value] ?? value; }
function timelineStatus(event: Decision['execution_events'][number], source: string) {
  return source === 'external' && event.kind === 'confirmed' && event.verification_status === 'unverified' ? 'confirmation_unverified' : event.kind;
}
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
function time(value: string | null | undefined, full = false, seconds = false) {
  if (!value) return '未提供';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    ...(full ? { year: 'numeric' } : {}),
    ...(seconds ? { second: '2-digit' } : {}),
  }).format(parsed);
}
function recordAge(serverTime?: string | null, observedAt?: string | null) {
  if (!serverTime || !observedAt) return '未提供';
  const elapsed = Date.parse(serverTime) - Date.parse(observedAt);
  if (!Number.isFinite(elapsed) || elapsed < 0) return '时钟待核验';
  const seconds = Math.floor(elapsed / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
  return `${Math.floor(seconds / 86400)} 天 ${Math.floor(seconds % 86400 / 3600)} 小时`;
}
function currentView(): View {
  const segments = window.location.hash.replace(/^#\/?/, '').split('/');
  if (segments[0] === 'decisions' && segments[1]) return { kind: 'decision', id: decodeURIComponent(segments[1]) };
  if (segments[0] === 'comparisons' && segments[1]) return { kind: 'comparison', id: decodeURIComponent(segments[1]) };
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
  const inferred = /failed|infeasible|timeout/.test(value) ? 'red' : /fallback|stale|unready|submitted|unsupported|unverified/.test(value) ? 'amber' : /optimal|ready|confirmed/.test(value) ? 'green' : 'muted';
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
function FreshnessBadge({ value }: { value?: RecordFreshness | null }) {
  const names: Record<RecordFreshness, string> = { fresh: '记录新鲜', stale: '记录过期', unknown: '新鲜度未知', clock_unverified: '采集时钟待核验' };
  return <Badge value={value ? names[value] : '新鲜度未提供'} tone={value === 'fresh' ? 'green' : value === 'stale' || value === 'clock_unverified' ? 'amber' : 'muted'} />;
}
function CollectionInfo({ collection }: { collection?: CollectionStatus | null }) {
  const names: Record<CollectionStatus['state'], string> = { running: '采集器运行中', stopped: '采集器已停止', degraded: '采集器状态异常', unknown: '采集器状态未知' };
  return <section className="collection-bar" aria-label="持续采集状态"><div className="collection-main"><Icon name="clock" size={17} /><Badge value={collection ? names[collection.state] : '采集状态未提供'} tone={collection?.state === 'running' ? 'green' : collection?.state === 'degraded' ? 'amber' : 'muted'} /><span>三站整轮最近成功（UTC）：<strong>{time(collection?.last_success_at, true, true)}</strong></span></div>{collection?.poll_interval_seconds != null && <span className="collection-policy">采集轮询 {collection.poll_interval_seconds} 秒{collection.freshness_threshold_seconds != null ? ` · 记录有效期 ${collection.freshness_threshold_seconds} 秒` : ''}</span>}{collection?.updated_at && <p>采集状态更新（UTC）：{time(collection.updated_at, true, true)}</p>}{collection?.last_error && <p className="collection-error">最近采集错误（UTC）：{time(collection.last_error_at, true, true)} · {collection.last_error}</p>}</section>;
}
function StationCard({ station, index, settlement, serverTime }: { station: Station; index: number; settlement: Settlement | null; serverTime?: string | null }) {
  return <article className="station-card">
    <div className="station-card-top"><span className="site-label">SITE {String(index + 1).padStart(2, '0')}</span><SourceBadge source={station.source_kind} sources={station.sources} provenance={station.provenance} /></div>
    <h3>{station.site_id}</h3>
    <p className="device-time"><Icon name="clock" size={14} />设备时钟（UTC）：{time(station.device_time, true, true)}</p>
    <div className="record-meta"><span>采集墙钟（UTC）：{time(station.observed_at, true, true)}</span><div><span>记录年龄（响应时）：{recordAge(serverTime, station.observed_at)}</span><FreshnessBadge value={station.record_freshness} /></div></div>
    <div className="station-energy"><BatteryRing percent={station.soc_percent} /><div className="energy-readings"><div><Icon name="sun" /><span>快照光伏<strong>{power(station.pv_kw)}</strong></span></div><div><Icon name="home" /><span>输入负载<strong>{power(station.load_kw)}</strong></span></div></div></div>
    <div className="recommendation"><span>当前建议</span><strong>{actionNames[station.action.kind] ?? station.action.kind}<em>{power(station.action.power_kw)}</em></strong></div>
    <p className="control-note" style={{ margin: '7px 0 0' }}>功率含义：{controlLabel(station.action.control_semantics)}</p>
    <p className="reason-summary">{station.reason_summary || '等待策略模块提供决策依据。'}</p>
    <StatusGrid solver={station.solver_status} forecast={station.forecast_status} execution={station.execution_status} settlement={settlement} />
    <button className="card-link" onClick={() => navigate(`/decisions/${encodeURIComponent(station.id)}`)}>查看决策依据<Icon name="arrow" size={17} /></button>
  </article>;
}
function OverviewPage({ selectedRun, selectRun, automatic, setAutomatic }: { selectedRun: string; selectRun: (run: string) => void; automatic: boolean; setAutomatic: (value: boolean) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const automaticRef = useRef(automatic);
  automaticRef.current = automatic;
  const controls = useRef<{ refresh: () => void; automatic: (enabled: boolean) => void } | null>(null);
  useEffect(() => {
    let active = true; let inFlight = false; let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    setError('');
    function schedule() {
      clearTimeout(timer);
      if (active && automaticRef.current && !document.hidden) timer = setTimeout(() => void load(), 10_000);
    }
    async function load() {
      if (!active || inFlight) return;
      clearTimeout(timer); inFlight = true; setLoading(true);
      controller = new AbortController();
      try {
        const next = await api.stations(selectedRun || undefined, controller.signal);
        if (active) { setData(next); setError(''); setLastUpdated(new Date().toISOString()); if (!selectedRun && next.run_id) selectRun(next.run_id); }
      } catch (reason) { if (active) setError((reason as Error).message); }
      finally { inFlight = false; if (active) { setLoading(false); schedule(); } }
    }
    controls.current = {
      refresh: () => void load(),
      automatic: (enabled) => { automaticRef.current = enabled; clearTimeout(timer); if (enabled && !document.hidden) void load(); },
    };
    const visible = () => { clearTimeout(timer); if (!document.hidden && automaticRef.current) void load(); };
    document.addEventListener('visibilitychange', visible);
    if (!document.hidden) void load(); else setLoading(false);
    return () => { active = false; clearTimeout(timer); controller?.abort(); controls.current = null; document.removeEventListener('visibilitychange', visible); };
  }, [selectedRun, selectRun]);
  const refresh = () => controls.current?.refresh();
  const toggleAutomatic = () => { const next = !automatic; setAutomatic(next); controls.current?.automatic(next); };
  const activeData = !selectedRun || data?.run_id === selectedRun ? data : null;
  const stations = activeData?.stations ?? [];
  const confirmed = stations.filter((station) => station.execution_status === 'confirmed').length;
  const fallback = stations.filter((station) => /fallback|stale|unready|persistence/.test(station.forecast_status)).length;
  const modelValidation = stations.length > 0 && stations.every((station) => isModelValidation(station.source_kind, station.sources, station.provenance));
  return <>
    <PageTitle eyebrow="ENERGY DECISION OBSERVATORY" title="让每一度电都有理由" description="看懂当前建议，比较替代方案，追踪真实执行。" action={<div className="refresh-controls"><button className={`button secondary ${automatic ? 'automatic-on' : ''}`} onClick={toggleAutomatic} aria-label={automatic ? '暂停自动刷新' : '开启自动刷新'} aria-pressed={automatic}><Icon name="clock" size={17} />{automatic ? '自动刷新 · 10秒' : '自动刷新已暂停'}</button><button className="button secondary" onClick={refresh} disabled={loading}><Icon name="refresh" size={17} />刷新记录</button></div>} />
    <div className="context-bar"><div className="run-select"><label htmlFor="run-select">当前场次</label><select id="run-select" value={selectedRun} onChange={(event) => selectRun(event.target.value)}>{!selectedRun && <option value="">最近场次</option>}{data?.runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id}{isDemo(run.source_kind) ? ' · 人工演示' : ''}</option>)}</select></div><div className="context-note"><Icon name="shield" size={17} />{modelValidation ? '人工输入验证模型接入 · 执行与结算依据独立记录' : stations.length && stations.every((station) => isDemo(station.source_kind)) ? '人工场景 · 未连接设备控制 · 无官方结算' : '页面依据导入记录展示状态'}</div></div>
    <p className="page-update">页面最近更新（UTC）：{time(lastUpdated, true, true)}{activeData ? ' · 状态与年龄以此次响应为准' : ''}{loading && activeData ? ' · 正在读取新记录…' : ''}{error && activeData ? ' · 刷新失败，显示上次成功记录' : ''}</p>
    {error && <ErrorBox message={error} retry={refresh} />}
    {activeData && <CollectionInfo collection={activeData.collection} />}
    {!activeData ? loading ? <Loading /> : error ? null : <div className="empty-state"><h2>尚未读取站点记录</h2><p>页面隐藏时暂停自动读取，可切回页面或手动刷新。</p></div> : <>
      <div className="summary-strip"><div><span className="metric-icon"><Icon name="grid" /></span><span>站点记录<strong>{stations.length}<small>个</small></strong></span></div><div><span className="metric-icon"><Icon name="check" /></span><span>回读确认<strong>{confirmed}<small>个</small></strong></span></div><div><span className="metric-icon amber"><Icon name="info" /></span><span>回退或延续估计<strong>{fallback}<small>个</small></strong></span></div><div><span className="metric-icon"><Icon name="sun" /></span><span>快照光伏合计<strong>{stations.reduce((total, station) => total + station.pv_kw, 0).toFixed(2)}<small>kW</small></strong></span></div></div>
      <div className="section-heading"><h2>站点决策</h2><span>每条建议都关联独立快照</span></div>
      {stations.length ? <div className="station-grid">{stations.map((station, index) => <StationCard station={station} index={index} settlement={activeData?.settlement ?? null} serverTime={activeData?.server_time} key={station.id} />)}</div> : <div className="empty-state"><Icon name="battery" size={32} /><h2>这个场次还没有决策</h2><p>通过决策导入接口添加记录，或运行演示数据初始化命令。</p></div>}
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
function ReadbackTable({ decision }: { decision: Decision }) {
  const readbacks = decision.execution_evidence?.readbacks ?? [];
  const statuses = { verified: '提交后遥测一致', pending: '待回读', mismatch: '不一致', unsupported: '缺少支持证据' };
  const reasons: Record<string, string> = {
    missing_submission: '缺少对应提交回执', awaiting_readback: '等待回读', awaiting_device_tick: '等待提交后的设备时刻',
    awaiting_fresh_readback: '等待提交后的新回读', stale_readback: '回读记录过期', clock_unverified: '时钟待核验',
    identity_mismatch: '指令或决策身份不一致', command_state_mismatch: '控制模式或方向不一致', setpoint_mismatch: '控制目标不一致',
    power_balance_mismatch: '功率收支不一致', unexpected_grid_import: '发生非预期购电', battery_mutual_exclusion: '电池同时充放电',
    grid_mutual_exclusion: '并网点同时购售电', opposite_battery_flow: '电池出现相反方向功率', power_mismatch: '实际功率与指令不一致',
  };
  return <section className="panel readback-panel"><div className="panel-heading"><Icon name="shield" /><h2>关联指令的回读证据</h2><span className="outline-label">只读</span></div>{!readbacks.length ? <p className="muted">尚无关联指令的回读证据</p> : <><div className="table-scroll"><table className="readback-table"><caption className="sr-only">关联指令的回读状态与验证原因</caption><thead><tr><th>指令</th><th>回读状态</th><th>采集墙钟（UTC）</th><th>验证原因</th></tr></thead><tbody>{readbacks.map((entry, index) => <tr key={`${entry.observation.observation_id ?? 'observation'}-${index}`}><td>{entry.observation.instruction_id ?? '未提供'}</td><td><Badge value={statuses[entry.verification.status] ?? entry.verification.status} tone={entry.verification.status === 'verified' ? 'green' : entry.verification.status === 'mismatch' ? 'red' : 'amber'} /></td><td>{time(entry.observation.observed_at, true, true)}</td><td>{entry.verification.reasons?.length ? entry.verification.reasons.map((code) => reasons[code] ?? code).join('；') : '无附加原因'}</td></tr>)}</tbody></table></div><p className="scope-note">遥测一致表示提交后的观测与指令相符，不代表设备返回了指令的硬件确认。</p></>}</section>;
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
    <CollectionInfo collection={data.collection} />
    <p className="detail-status-snapshot">采集状态为读取详情时的快照（服务响应 UTC 时间：{time(data.server_time, true, true)}）；返回总览刷新当前状态。</p>
    <div className="detail-layout"><div className="detail-main">
      <section className="panel"><div className="panel-heading"><Icon name="shield" /><h2>为什么这样做</h2><span className="outline-label">规则生成 · 有证据</span></div><div className="fact-list">{explanation.facts.map((fact, index) => <article className="fact" key={fact.id}><span className="fact-number">{String(index + 1).padStart(2, '0')}</span><div><p>{fact.text}</p><EvidenceRefs values={fact.evidence_refs} /></div></article>)}</div><p className="scope-note">{explanation.scope}</p></section>
      <section className="panel"><div className="panel-heading"><Icon name="sun" /><h2>数据与假设</h2></div><div className="data-summary"><div><span>输入光伏</span><strong>{power(snapshot.pv_kw)}</strong></div><div><span>输入负载</span><strong>{power(snapshot.load_kw)}</strong></div><div><span>充电上限</span><strong>{power(snapshot.max_charge_kw)}</strong></div><div><span>步长</span><strong>{snapshot.step_hours} 小时</strong></div></div><SeriesChart values={snapshot.import_eur_kwh} title="购电价格" /><div className="source-grid">{Object.entries(snapshot.sources).map(([key, value]) => <div key={key}><span>{sourceLabel(key)}</span><strong>{sourceValueLabel(value)}</strong></div>)}<div><span>历史模式</span><strong>{historyLabel(snapshot.history_mode)}</strong></div><div><span>历史尾部（UTC）</span><strong>{time(snapshot.cache_tail, true)}</strong></div></div>{snapshot.fallback_reason && <div className="fallback-note"><Icon name="info" /><div><strong>当前使用降级方案</strong><p>{snapshot.fallback_reason}</p></div></div>}</section>
      <section className="panel"><div className="panel-heading"><Icon name="clock" /><h2>执行时间线</h2><Badge value={data.execution_status} /></div><div className="timeline"><div className="timeline-item"><span className="timeline-dot" /><div><strong>候选决策已生成</strong><p>{time(data.created_at, true)} UTC · {data.decision_id}</p></div></div>{data.execution_events.map((event) => <div className={`timeline-item ${timelineStatus(event, data.source_kind)}`} key={event.event_id}><span className="timeline-dot" /><div><strong>{label(timelineStatus(event, data.source_kind))}</strong><p>{time(event.observed_at ?? event.issued_at, true)} UTC · 指令 {event.instruction_id}</p>{event.device_time && <p>设备时间 {time(event.device_time, true)} UTC</p>}{event.note && <p>{event.note}</p>}{event.observed_mode && <p>回读：{event.observed_mode}{event.observed_power_kw != null ? ` · ${power(event.observed_power_kw)}` : ''}</p>}</div></div>)}</div>{!data.execution_events.length && <p className="scope-note">目前只有建议，尚无指令提交或设备回读记录。</p>}</section>
      <ReadbackTable decision={data} />
      <QuestionBox decision={data} />
    </div><aside className="detail-aside"><CompareForm decision={data} /><section className="panel"><div className="panel-heading"><h2>预计费用</h2></div><CostBreakdown cost={data.cost} /><p className="scope-note">费用依据本次输入及模型假设计算。{settlementLabel(data.settlement)}。</p>{data.settlement && <pre className="settlement-record">{JSON.stringify(data.settlement, null, 2)}</pre>}</section><section className="panel provenance-panel"><h2>证据留档</h2><dl><dt>采集墙钟（UTC）</dt><dd>{time(snapshot.observed_at, true, true)}</dd><dt>记录年龄（响应时）</dt><dd>{recordAge(data.server_time, snapshot.observed_at)}</dd><dt>入库墙钟（UTC）</dt><dd>{time(data.created_at, true, true)}</dd><dt>模型版本</dt><dd>{data.model_version}</dd><dt>策略版本</dt><dd>{data.policy_version}</dd><dt>决策 ID</dt><dd>{data.decision_id}</dd><dt>快照 SHA-256</dt><dd className="hash">{data.snapshot_hash}</dd></dl></section></aside></div>
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
  const deltaEur = job?.result?.delta_eur;
  const effectivelyEqual = deltaEur !== undefined && Math.abs(deltaEur) <= 1e-9;
  return <>
    <button className="back-link" onClick={() => job ? navigate(`/decisions/${encodeURIComponent(job.decision_id)}`) : navigate('/')}><Icon name="back" size={16} />返回决策依据</button>
    <PageTitle eyebrow="COUNTERFACTUAL COMPARISON" title="如果换一种做法" description="同一输入快照、同一模型，固定首步动作后重新优化。" action={source && source !== 'unknown' ? <SourceBadge source={source} sources={inputSources} provenance={provenance} /> : <span className="source-badge">{source === 'unknown' ? '记录来源待核验' : '正在读取记录来源'}</span>} />
    {error && <ErrorBox message={error} retry={() => setRefresh((n) => n + 1)} />}
    {!job && !error && <Loading text="正在读取替代方案任务…" />}
    {job && <><div className="comparison-context"><div><Icon name="compare" /><span>首步电池充电固定为 <strong>{power(job.first_charge_kw)}</strong></span></div><Badge value={job.status === 'ready' ? '计算完成' : job.status} tone={job.status === 'ready' ? 'green' : undefined} /></div>
      {(job.status === 'queued' || job.status === 'running') && <div className="job-pending" role="status"><span className="spinner" /><h2>{job.status === 'queued' ? '任务已入队，等待模型工作进程' : '正在重新优化后续计划'}</h2><p>页面会自动更新。普通原因解释仍可直接查看。</p></div>}
      {job.status !== 'queued' && job.status !== 'running' && job.status !== 'ready' && <div className="job-message" role="status"><Icon name="info" size={30} /><h2>{label(job.status)}</h2><p>{job.error || '此次比较没有可展示的模型结果。'}</p>{job.status === 'timeout' && <p>计算超时无法判断替代方案是否可行。</p>}{job.status === 'unsupported' && <p>需要为这条真实记录注册对应模型适配器。</p>}</div>}
      {job.status === 'ready' && job.result && <><div className={`delta-card ${!effectivelyEqual && job.result.delta_eur < 0 ? 'negative' : ''}`}><div><p>替代方案费用 − 原方案费用</p><h2>{effectivelyEqual ? '≈€0.000000' : `${job.result.delta_eur >= 0 ? '+' : '−'}${money(Math.abs(job.result.delta_eur), 6)}`}</h2><span>{effectivelyEqual ? '在计算精度内，两种方案费用相同。' : job.result.delta_eur > 0 ? '在本次假设下，替代方案费用更高。' : '在本次假设下，替代方案费用更低。'}</span></div><div className="delta-icon"><Icon name="compare" size={34} /></div></div><div className="scenario-grid"><ScenarioPanel title="原方案" scenario={job.result.baseline} /><ScenarioPanel title="替代方案" scenario={job.result.alternative} alternative /></div><section className="panel assumptions-panel"><div className="panel-heading"><Icon name="info" /><h2>比较成立的假设</h2></div><ul>{job.result.assumptions.map((assumption, index) => <li key={index}>{assumption}</li>)}</ul><p className="scope-note">这是模型费用比较，不代表实际执行收益或官方比赛成绩。0 kW 干预仅约束电池首步充电，不能据此推断 HA 控制模式。</p></section></>}
    </>}
  </>;
}
export default function App() {
  const [view, setView] = useState<View>(currentView); const [selectedRun, setSelectedRun] = useState(''); const [automatic, setAutomatic] = useState(true);
  useEffect(() => { const listener = () => { setView(currentView()); window.scrollTo?.(0, 0); }; window.addEventListener('hashchange', listener); return () => window.removeEventListener('hashchange', listener); }, []);
  return <div className="app-shell"><a className="skip-link" href="#main" onClick={(event) => { event.preventDefault(); document.getElementById('main')?.focus(); }}>跳转到内容</a><aside className="sidebar"><a className="brand" href="#/"><span className="brand-mark"><Icon name="bolt" size={25} /></span><span>电到为止<small>ENERGY EXPLAINABILITY</small></span></a><span className="nav-caption">工作空间</span><nav aria-label="主导航"><a href="#/" className={view.kind === 'overview' ? 'active' : ''}><Icon name="grid" />站点总览</a><span className={view.kind === 'decision' ? 'active current-nav' : 'current-nav'}><Icon name="shield" />决策依据</span><span className={view.kind === 'comparison' ? 'active current-nav' : 'current-nav'}><Icon name="compare" />替代方案</span></nav><div className="sidebar-foot"><span className="live-dot" /><strong>独立解释框架</strong><p>事实留档 · 模型比较<br />执行追踪</p><span className="version">LOCAL / v0.1</span></div></aside><div className="app-body"><header className="topbar"><span className="breadcrumb">家庭储能<span>/</span>{view.kind === 'overview' ? '站点总览' : view.kind === 'decision' ? '决策依据' : '替代方案'}</span><div><span className="topbar-note">决策透明，安心用电</span><span className="topbar-pill"><span className="live-dot" />本地解释台</span></div></header><main id="main" tabIndex={-1}>{view.kind === 'overview' ? <OverviewPage selectedRun={selectedRun} selectRun={setSelectedRun} automatic={automatic} setAutomatic={setAutomatic} /> : view.kind === 'decision' ? <DecisionPage id={view.id} key={view.id} /> : <ComparisonPage id={view.id} key={view.id} />}<footer className="page-footer"><span>电到为止 · 储能决策解释台</span><span>证据可追溯，假设可检查</span></footer></main></div></div>;
}
