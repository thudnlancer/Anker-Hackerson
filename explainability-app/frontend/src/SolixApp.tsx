import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { api, type Decision, type Explanation, type Job, type Overview, type PlanJob, type PVDaily, type Station } from './api';
import EvidenceApp from './EvidenceApp';
import { artificial, homeHealth, slotTime } from './homeDomain';
import { actionName, forecastCount, matchesDecision, money, newerSettlement, power, settlementMetric, stamp, statusName } from './solixDomain';
import battery from './solix-battery.svg?raw';
import './solix-reference.css';
import './solix.css';

const pages = [
  ['home', '⌂', 'HOME / 核心'], ['overview', '◉', '能源总览'], ['timeline', '⌁', '调度时间轴'],
  ['ai', '◇', 'AI 决策 / 证据'], ['plan', '▧', '用电计划'], ['analytics', '▥', '光伏 / 能源费用'], ['preferences', '☷', '家庭偏好'],
] as const;
type Page = typeof pages[number][0] | 'evidence';
function currentPage(): Page {
  const route = location.hash.replace(/^#\/?/, '').split('/')[0];
  if (['evidence', 'decisions', 'comparisons'].includes(route)) return 'evidence';
  const aliases: Record<string, Page> = { today: 'home', plans: 'plan', solar: 'analytics', costs: 'analytics', settings: 'preferences' };
  return pages.find(([p]) => p === route)?.[0] ?? aliases[route] ?? 'home';
}
function go(page: Page) { location.hash = `/${page}`; }
function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`hint ${error ? 'solix-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
function Panel({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return <section className="panel"><div className="panel-header"><div className="panel-title">{title}</div>{aside}</div><div className="solix-panel-body">{children}</div></section>;
}
function Metric({ title, value, note }: { title: string; value: ReactNode; note?: string }) {
  return <div className="datacell"><label>{title}</label><strong>{value}</strong>{note && <p className="micro">{note}</p>}</div>;
}

function useEnergy(run: string, site: string) {
  const [overview, setOverview] = useState<Overview>();
  const [detail, setDetail] = useState<Decision>();
  const [explanation, setExplanation] = useState<{ id: string; value: Explanation }>();
  const [error, setError] = useState(''); const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(true); const [refresh, setRefresh] = useState(0);
  const active = !run || overview?.run_id === run ? overview : undefined;
  const station = active?.stations.find(s => s.site_id === site) ?? active?.stations[0];
  useEffect(() => {
    const controller = new AbortController(); let alive = true; let timer: ReturnType<typeof setTimeout>;
    setLoading(true); setError('');
    async function read() {
      try {
        const next = await api.stations(run || undefined, controller.signal);
        if (alive) { setOverview(before => before?.run_id === next.run_id ? { ...next, settlement: newerSettlement(before.settlement, next.settlement) } : next); setError(''); }
      } catch (reason) { if (alive) setError((reason as Error).message); }
      finally { if (alive) { setLoading(false); timer = setTimeout(read, 10_000); } }
    }
    void read();
    return () => { alive = false; controller.abort(); clearTimeout(timer); };
  }, [run, refresh]);
  useEffect(() => {
    if (!station) return;
    let alive = true; const controller = new AbortController(); setDetailError('');
    void api.decision(station.id, controller.signal).then(next => {
      if (alive) { setDetail(next); setOverview(before => before?.run_id === next.run_id ? { ...before, settlement: newerSettlement(before.settlement, next.settlement) } : before); }
    }).catch(reason => { if (alive) setDetailError(reason.message); });
    void api.explanation(station.id, controller.signal).then(next => { if (alive) setExplanation({ id: station.id, value: next }); }).catch(() => { if (alive) setExplanation(undefined); });
    return () => { alive = false; controller.abort(); };
  }, [station]);
  const decision = matchesDecision(detail, station?.id, station?.run_id, station?.site_id)
    ? { ...detail!, settlement: newerSettlement(detail!.settlement, active?.settlement) } : undefined;
  // Explanation and detail are only used together for the selected immutable record.
  return { overview: active, runs: overview?.runs ?? [], station, decision, explanation: decision && explanation?.id === decision.id ? explanation.value : undefined, error, detailError, loading, reload: () => setRefresh(v => v + 1) };
}

function Forecast({ decision, compact = false }: { decision: Decision; compact?: boolean }) {
  const count = forecastCount(decision.snapshot);
  const [cursor, setCursor] = useState(0); const [start, setStart] = useState(0); const [end, setEnd] = useState(count - 1);
  if (!count) return <Notice>该记录缺少完整且对齐的电价、光伏和负载预测。</Notice>;
  const s = decision.snapshot; const first = compact ? 0 : start; const last = compact ? count - 1 : Math.max(first, end);
  const selected = Math.min(Math.max(cursor, first), last);
  const tracks = [
    { name: '购电价', unit: 'EUR/kWh', values: s.import_eur_kwh, color: '#e5ff28' },
    { name: '外送价', unit: 'EUR/kWh', values: s.export_eur_kwh, color: '#ffbc71' },
    { name: '光伏预测', unit: 'kW', values: s.pv_forecast_kw, color: '#f3f4f1' },
    { name: '家庭负载预测', unit: 'kW', values: s.load_forecast_kw, color: '#91ad9f' },
  ];
  const x = (i: number) => 120 + (i - first) / Math.max(1, last - first) * 600;
  return <div className="solix-forecast">
    <p className="micro">{count} 步 × {s.step_hours} 小时 · {stamp(s.device_time)} 起 · 按完整设备时间对齐</p>
    <svg viewBox="0 0 760 365" role="img" aria-label="记录快照中的分轨电价与功率预测">
      {tracks.map((track, row) => {
        const values = track.values.slice(first, last + 1); const min = Math.min(0, ...values); const max = Math.max(.001, ...values); const top = 25 + row * 80;
        const y = (v: number) => top + 48 - (v - min) / (max - min) * 48;
        return <g key={track.name}><text x="5" y={top + 12}>{track.name}</text><text x="5" y={top + 29} className="axis-unit">{track.unit}</text><text x="5" y={top + 45} fill={track.color}>{track.values[selected].toFixed(3)}</text>
          <line x1="120" x2="720" y1={top + 48} y2={top + 48} stroke="#465545"/>
          {min < 0 && <line x1="120" x2="720" y1={y(0)} y2={y(0)} stroke="#7b8980" strokeDasharray="3 5"/>}
          <path d={values.map((v, i) => `${i ? 'L' : 'M'}${x(i + first)},${y(v)}`).join(' ')} fill="none" stroke={track.color} strokeWidth="2"/>
          <circle cx={x(selected)} cy={y(track.values[selected])} r="4" fill={track.color}/>
        </g>;
      })}
      <line x1={x(selected)} x2={x(selected)} y1="18" y2="318" stroke="#e5ff28" strokeDasharray="4 6"/>
      <text x="120" y="352">{stamp(slotTime(s, first))}</text><text x="720" y="352" textAnchor="end">{stamp(slotTime(s, last))}</text>
    </svg>
    <label className="solix-range">查看时刻 <strong>{stamp(slotTime(s, selected))}</strong><input aria-label="查看预测时刻" className="range" type="range" min={first} max={last} value={selected} onChange={e => setCursor(Number(e.target.value))}/></label>
    {!compact && count > 1 && <div className="columns"><label className="solix-range">时间窗起点<input aria-label="时间窗起点" className="range" type="range" min="0" max={count - 1} value={start} onChange={e => { const n = Number(e.target.value); setStart(n); if (end < n) setEnd(n); }}/></label><label className="solix-range">时间窗终点<input aria-label="时间窗终点" className="range" type="range" min={start} max={count - 1} value={end} onChange={e => setEnd(Number(e.target.value))}/></label></div>}
    <p className="chart-note">各轨独立纵轴。仅展示保存的预测；完整调度与未来 SOC 未随此接口提供。</p>
  </div>;
}

function Core({ station, decision, explanation }: { station: Station; decision?: Decision; explanation?: Explanation }) {
  const [expanded, setExpanded] = useState(false); const [channel, setChannel] = useState(0);
  const nodes = [
    { name: '计划 / 模型费用', value: money(decision?.cost?.objective_eur), note: '模型目标，非已节省金额', page: 'plan' as Page },
    { name: '当前家庭负荷', value: power(station.load_kw), note: '所选记录中的负载观测', page: 'overview' as Page },
    { name: '光伏发电功率', value: power(station.pv_kw), note: '所选记录中的光伏观测', page: 'analytics' as Page },
    { name: '电池状态 / 建议', value: `${station.soc_percent.toFixed(1)}%`, note: `${actionName(station.action)} · ${power(station.action.power_kw)}`, page: 'ai' as Page },
    { name: '电网 / 购售电价', value: money(decision?.snapshot.import_eur_kwh[0]), note: 'EUR/kWh · 电网实测功率未提供', page: 'timeline' as Page },
  ];
  const node = nodes[channel];
  return <><div className="home-header"><div><div className="eyebrow">00 / COMMAND NEXUS · INTELLIGENT ENERGY INTERFACE</div><h1>SOLIX <em>HOME</em> CORE<span className="cursor">_</span></h1><p>让能源可见，让决策可理解。点击中央电池，展开家庭能源状态。</p></div><div className="home-time"><span className="pulse-dot"/>{artificial(station) ? 'DEMONSTRATION' : 'RECORDED SNAPSHOT'}<br/><small>{station.site_id} / {stamp(station.device_time)}</small></div></div>
    <div className="home-stage-grid"><div className={`home-universe ${expanded ? 'expanded' : ''}`}>
      <div className="home-crosshair"/><div className="home-label tl">FIG.00 / CENTRAL ENERGY SYSTEM<br/>AXIAL PROJECTION / 01:01</div><div className="home-label tr">{station.site_id}<br/>{artificial(station) ? '人工输入 · 演示' : '记录快照 · 核对新鲜度'}</div>
      <div className="orbit-system" aria-hidden="true">{[1, 2, 3, 4, 5].map(i => <div key={i} className={`ring ring${i}`}/>)}<div className="ring-sweep"/><i className="orbit-dot dot1"/><i className="orbit-dot dot2"/></div>
      <div className="home-battery-wrap"><button className="battery-trigger" aria-label="展开能源信息节点" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}><span dangerouslySetInnerHTML={{ __html: battery }}/><span className="battery-text">SOLIX BATTERY / {expanded ? 'COLLAPSE −' : 'CLICK TO EXPAND ＋'}</span></button></div>
      {nodes.map((n, i) => <button key={n.name} className={`home-node hnode${i + 1}`} onClick={() => setChannel(i)} tabIndex={expanded ? 0 : -1} aria-hidden={!expanded}><span className="hnum">NODE / 0{i + 1}</span><span className="hname">{n.name}</span><strong>{n.value}</strong><span className="hsub">{n.note}</span></button>)}
      <div className="home-footer"><span>◉ {expanded ? 'SELECT AN INFORMATION CHANNEL' : 'TAP TO INITIALIZE'}</span><span>连接为示意 · 建议与实测分别呈现</span></div>
    </div><div className="home-inspector" aria-live="polite"><div><span className="eyebrow">NODE / 0{channel + 1}</span><h2>{node.name}</h2><div className="channel-feature"><div className="head">{station.site_id} / SNAPSHOT</div><div className="value">{node.value}</div><div className="desc">{node.note}</div></div><p>{explanation?.summary ?? station.reason_summary}</p><div className="inspector-facts">SOC {station.soc_percent.toFixed(1)}%<br/>PV {power(station.pv_kw)}<br/>LOAD {power(station.load_kw)}<br/>{statusName(station.execution_status)}</div></div><button className="button" onClick={() => go(node.page)}>查看详细数据 ↗</button></div></div>
    <div className="home-summary"><div><span>05</span><small>INFO CHANNELS<br/>信息通道</small></div><div><span>{decision ? forecastCount(decision.snapshot) : '—'}</span><small>FORECAST STEPS<br/>记录预测步数</small></div><div><span>168<small>h</small></span><small>CONTEST WINDOW<br/>比赛设备小时</small></div><div><span>01</span><small>SELECTED SITE<br/>独立站点</small></div></div>
  </>;
}

function Recommendation({ station, decision, explanation }: { station: Station; decision?: Decision; explanation?: Explanation }) {
  return <Panel title="FIG.02 / CURRENT DECISION" aside={<span className="pill">{statusName(station.execution_status)}</span>}><div className="decision">
    <span className="eyebrow">MODEL RECOMMENDATION</span><h2>{actionName(station.action)}</h2><div className="bigval">{station.action.control_semantics === 'self_consumption' ? 'AUTO' : station.action.power_kw.toFixed(2)} <small>{station.action.control_semantics === 'self_consumption' ? 'MODE' : 'kW'}</small></div><p>{explanation?.summary ?? station.reason_summary}</p>
    <div className="data2"><Metric title="BATTERY SOC" value={`${station.soc_percent.toFixed(1)}%`}/><Metric title="SOLVER" value={statusName(station.solver_status)}/><Metric title="PV OBSERVED" value={power(station.pv_kw)}/><Metric title="LOAD OBSERVED" value={power(station.load_kw)}/></div>
    <Notice>{station.action.control_semantics === 'grid_export_target' ? '放电指令表示并网点输出目标，不能视为电池实测放电功率。' : station.action.control_semantics === 'self_consumption' ? '家庭自用模式按光伏与负荷自动调节，建议不代表电池实测功率。' : '功率为保存的模型建议。设备是否执行，需查看关联提交和回读证据。'}</Notice>
    {decision && <button className="button" onClick={() => { location.hash = `/decisions/${encodeURIComponent(decision.id)}`; }}>核对完整证据 ↗</button>}
  </div></Panel>;
}

function useJobPolling<T extends Job | PlanJob>(job: T | undefined, setJob: (job: T) => void, read: (id: string) => Promise<T>) {
  const [error, setError] = useState(''); const readRef = useRef(read); readRef.current = read;
  useEffect(() => {
    setError(''); if (!job || !['queued', 'running'].includes(job.status)) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>; const started = Date.now();
    async function poll() {
      try {
        const next = await readRef.current(job!.id);
        if (!alive) return;
        setJob(next);
        if (['queued', 'running'].includes(next.status)) {
          if (Date.now() - started > 120_000) { setError('任务仍未结束。请确认独立工作进程正在运行，稍后重新查询。'); return; }
          timer = setTimeout(poll, 1000);
        }
      } catch (reason) { if (alive) setError((reason as Error).message); }
    }
    timer = setTimeout(poll, 1000);
    return () => { alive = false; clearTimeout(timer); };
  }, [job?.id]); // A job remains pinned to its immutable decision while polling.
  return error;
}

function Intelligence({ decision, explanation }: { decision: Decision; explanation?: Explanation }) {
  const [question, setQuestion] = useState(''); const [answer, setAnswer] = useState(''); const [refs, setRefs] = useState<string[]>([]);
  const [job, setJob] = useState<Job>(); const [charge, setCharge] = useState(0); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const pollError = useJobPolling(job, setJob, api.job);
  const pending = busy || !!job && ['queued', 'running'].includes(job.status);
  async function ask(text: string) {
    if (!text.trim()) return; setBusy(true); setError('');
    try { const result = await api.question(decision.id, text); setAnswer(result.answer); setRefs(result.evidence_refs); if (result.job) setJob(result.job); }
    catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  async function compare(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { setJob(await api.compare(decision.id, charge)); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <><div className="layout"><Panel title="EXPLAINABLE OPTIMIZATION"><h2>{explanation?.title ?? '决策原因'}</h2><p>{explanation?.summary ?? '暂未取得解释记录。'}</p>{explanation?.facts.map(f => <div className="note" key={f.id}><b>{f.text}</b><div className="micro solix-wrap">{f.evidence_refs.join(' · ')}</div></div>)}<p className="micro">{explanation?.scope}</p><a className="button secondary" href={`#/decisions/${encodeURIComponent(decision.id)}`}>打开输入、执行与结算证据 ↗</a></Panel>
    <Panel title="CONVERSATIONAL EXPLANATION"><h2>追问系统</h2><div className="answer" aria-live="polite">{answer || '选择追问，查看当前决策的证据与模型结果。'}</div><div className="quick">{['为什么这样做', '首步不充会怎样', '为什么不能增加功率'].map(text => <button key={text} disabled={pending} onClick={() => void ask(text)}>{text}</button>)}</div><form className="chat-input" onSubmit={e => { e.preventDefault(); void ask(question); }}><input aria-label="追问当前决策" className="field" value={question} onChange={e => setQuestion(e.target.value)} placeholder="支持原因、首步不充、功率边界三类追问"/><button className="button" disabled={pending || !question.trim()}>发送 →</button></form><p className="micro solix-wrap">{refs.join(' · ')}</p><Notice>解释来自后端证据规则；替代方案由匹配版本的模型重新求解。费用差不代表实际节省。</Notice></Panel></div>
    <Panel title="COUNTERFACTUAL / 首步充电功率干预"><form onSubmit={compare} className="solix-inline"><label>替代首步充电功率（kW）<input className="field" aria-label="替代首步充电功率" type="number" min="0" max={decision.snapshot.max_charge_kw} step="0.1" value={charge} onChange={e => setCharge(Number(e.target.value))}/></label><button className="button" disabled={!decision.capabilities.counterfactual || pending}>重新求解并比较 →</button></form>{!decision.capabilities.counterfactual && <Notice>该记录没有匹配的反事实模型。</Notice>}{job && <div aria-live="polite"><p>比较任务：{statusName(job.status)}</p>{job.result && <><div className="chat-cards"><Metric title="原方案模型目标" value={money(job.result.baseline.cost.objective_eur)}/><Metric title="替代方案模型目标" value={money(job.result.alternative.cost.objective_eur)}/></div><div className="bigval">{money(job.result.delta_eur)}</div><p>替代净成本 − 原净成本；负值表示替代方案预计费用较低。</p>{job.result.assumptions.map(a => <p className="micro" key={a}>{a}</p>)}</>}{job.error && <Notice error>{job.error}</Notice>}</div>}{(error || pollError) && <Notice error>{error || pollError}</Notice>}{pollError && job && <button className="button secondary" onClick={() => { void api.job(job.id).then(setJob).catch(reason => setError(reason.message)); }}>查询任务结果</button>}</Panel>
  </>;
}

function BatteryPlan({ decision }: { decision: Decision }) {
  const count = forecastCount(decision.snapshot); const [target, setTarget] = useState(80); const [step, setStep] = useState(Math.min(4, count));
  const [job, setJob] = useState<PlanJob>(); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const pollError = useJobPolling(job, setJob, api.planJob);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { setJob(await api.plan(decision.id, target, slotTime(decision.snapshot, step))); }
    catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <Panel title="MODEL PLAN / 家庭储能截止约束"><Notice>检查所选家庭储能电池能否按时达到目标 SOC。车辆、洗衣等生活计划仍为本地建议；当前接口不控制家电。</Notice><form className="solix-inline" onSubmit={submit}><label>目标 SOC（%）<input className="field" aria-label="目标储能 SOC" type="number" min="0" max="100" value={target} onChange={e => setTarget(Number(e.target.value))}/></label><label>截止时间（设备时间）<select className="field" aria-label="计划截止时间" value={step} onChange={e => setStep(Number(e.target.value))}>{Array.from({ length: count }, (_, i) => <option key={i} value={i + 1}>{stamp(slotTime(decision.snapshot, i + 1))}</option>)}</select></label><button className="button" disabled={!count || busy || !!job && ['queued', 'running'].includes(job.status)}>检查模型可行性 →</button></form>
    {job && <div aria-live="polite"><h3>{statusName(job.status)}</h3>{job.result && <><h3>{statusName(job.result.status)}</h3><p>{job.result.message}</p><div className="data2"><Metric title="原模型费用" value={money(job.result.baseline_cost_eur)}/><Metric title="截止约束费用" value={money(job.result.planned_cost_eur)}/><Metric title="费用差" value={money(job.result.cost_delta_eur)}/><Metric title="截止前可达上界" value={job.result.max_reachable_energy_kwh == null ? '—' : `${job.result.max_reachable_energy_kwh.toFixed(2)} kWh`}/></div>{job.result.adjustments?.map((a, i) => <p key={i}>{a.kind === 'lower_target' ? `降低目标至 ${a.target_soc_percent}%` : `延长至 ${a.deadline}`} · {statusName(a.status)} · {money(a.cost_delta_eur)}</p>)}{job.result.assumptions?.map(a => <p className="micro" key={a}>{a}</p>)}</>}{job.error && <Notice error>{job.error}</Notice>}</div>}{(error || pollError) && <Notice error>{error || pollError}</Notice>}{pollError && job && <button className="button secondary" onClick={() => { void api.planJob(job.id).then(setJob).catch(reason => setError(reason.message)); }}>查询计划结果</button>}
  </Panel>;
}

function DecisionWorkspace({ decision, explanation, kind }: { decision?: Decision; explanation?: Explanation; kind: 'ai' | 'plan' }) {
  const [record, setRecord] = useState<{ decision: Decision; explanation?: Explanation } | undefined>(decision ? { decision, explanation } : undefined);
  useEffect(() => {
    if (decision) setRecord(current => !current ? { decision, explanation } : current.decision.id === decision.id && explanation && current.explanation !== explanation ? { ...current, explanation } : current);
  }, [decision?.id, explanation]);
  if (!record) return <Notice>等待当前站点决策证据…</Notice>;
  return <><Notice>本工作区固定使用设备时间 {stamp(record.decision.snapshot.device_time)} 的决策，自动刷新不会替换正在填写或计算的场景。
    {decision && record.decision.id !== decision.id && <button className="button secondary" onClick={() => setRecord({ decision, explanation })}>使用最新决策并重置工作区</button>}
  </Notice>{kind === 'ai' ? <Intelligence key={record.decision.id} decision={record.decision} explanation={record.explanation}/> : <BatteryPlan key={record.decision.id} decision={record.decision}/>}</>;
}

type LocalPlan = { id: string; site: string; name: string; energy: number; hours: number };
type Preference = { reserve: number; priority: string };
type LocalState = { plans: LocalPlan[]; homes: Record<string, Preference> };
const localKey = 'solix-v13-local-v1';
function readLocal(): LocalState {
  try {
    const value = JSON.parse(localStorage.getItem(localKey) ?? 'null');
    const plans = Array.isArray(value?.plans) ? value.plans.filter((p: LocalPlan) => p && typeof p.id === 'string' && typeof p.site === 'string' && typeof p.name === 'string' && Number.isFinite(p.energy) && p.energy > 0 && Number.isFinite(p.hours) && p.hours > 0) : [];
    const homes: Record<string, Preference> = {};
    for (const [site, p] of Object.entries(value?.homes ?? {}) as [string, Preference][]) if (p && Number.isFinite(p.reserve) && p.reserve >= 0 && p.reserve <= 80 && ['balanced', 'cost', 'solar', 'wear'].includes(p.priority)) homes[site] = p;
    return { plans, homes };
  } catch { return { plans: [], homes: {} }; }
}
function LifePlans({ site, local, save }: { site: string; local: LocalState; save: (state: LocalState) => void }) {
  const [name, setName] = useState(''); const [energy, setEnergy] = useState(10); const [hours, setHours] = useState(8); const [kw, setKw] = useState(3.5); const [checked, setChecked] = useState(false);
  const duration = energy / (kw * .95); const feasible = Number.isFinite(duration) && energy > 0 && kw > 0 && hours > 0 && duration <= hours;
  function check(e: FormEvent) { e.preventDefault(); setChecked(true); }
  return <Panel title="LIFE PLAN / 生活计划"><p>当前站点：{site} · 计划仅保存在当前浏览器，未接入设备或调度器。</p>{local.plans.filter(p => p.site === site).map(p => <div className="plan-item" key={p.id}><strong>{p.name}</strong><small>{p.energy} kWh / {p.hours} h 可用窗口 · 本地建议</small><button className="button secondary" onClick={() => save({ ...local, plans: local.plans.filter(item => item.id !== p.id) })}>移除 {p.name}</button></div>)}<form onSubmit={check} onChange={() => setChecked(false)}><div className="columns"><label>计划名称<input className="field" aria-label="生活计划名称" required maxLength={100} value={name} onChange={e => setName(e.target.value)}/></label><label>需求电量（kWh）<input className="field" type="number" min="0.1" max="200" step="0.1" value={energy} onChange={e => setEnergy(Number(e.target.value))}/></label><label>有效功率（kW）<input className="field" type="number" min="0.1" max="50" step="0.1" value={kw} onChange={e => setKw(Number(e.target.value))}/></label><label>可用窗口（小时）<input className="field" type="number" min="0.1" max="168" step="0.1" value={hours} onChange={e => setHours(Number(e.target.value))}/></label></div><button className="button">检查生活计划 →</button></form>{checked && <Notice>{feasible ? `时间与功率检查可行，按 95% 假设效率约需 ${duration.toFixed(2)} 小时。` : '当前时间或功率不足，请减少需求、延长窗口或增加有效功率。'} 未考虑家庭负荷、充电曲线和动态电价。</Notice>}{checked && feasible && name.trim() && <button className="button" onClick={() => { save({ ...local, plans: [...local.plans, { id: crypto.randomUUID(), site, name: name.trim(), energy, hours }] }); setChecked(false); setName(''); }}>确认保存本地建议</button>}</Panel>;
}
function Preferences({ site, local, save }: { site: string; local: LocalState; save: (state: LocalState) => void }) {
  const [draft, setDraft] = useState<Preference>(local.homes[site] ?? { reserve: 20, priority: 'balanced' });
  return <Panel title="HOME PREFERENCES / 家庭偏好"><Notice>偏好按站点保存在当前浏览器，尚不会修改运行中的策略或设备 SOC 限值。</Notice><div className="pref-choice-grid">{[['balanced', '均衡模式'], ['cost', '费用优先'], ['solar', '自用优先'], ['wear', '减少电池损耗']].map(([value, title]) => <button key={value} className={`pref-choice ${draft.priority === value ? 'chosen' : ''}`} aria-pressed={draft.priority === value} onClick={() => setDraft({ ...draft, priority: value })}>{draft.priority === value ? '◉' : '○'} {title}</button>)}</div><label className="solix-range">希望保留的电量 <strong>{draft.reserve}%</strong><input aria-label="希望保留的电量" className="range" type="range" min="0" max="80" step="5" value={draft.reserve} onChange={e => setDraft({ ...draft, reserve: Number(e.target.value) })}/></label><button className="button" onClick={() => save({ ...local, homes: { ...local.homes, [site]: draft } })}>保存家庭偏好</button></Panel>;
}

function Analytics({ decision }: { decision: Decision }) {
  const [arm, setArm] = useState('irradiance'); const [days, setDays] = useState<PVDaily[]>([]); const [error, setError] = useState(''); const [loading, setLoading] = useState(true); const [dayIndex, setDayIndex] = useState(0);
  useEffect(() => {
    let alive = true; setDays([]); setDayIndex(0); setLoading(true); setError('');
    void api.pvDaily(decision.site_id, arm).then(result => { if (alive) setDays(result.days.filter(d => d.site_id === decision.site_id && d.arm === arm)); }).catch(reason => { if (alive) setError(reason.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [decision.site_id, arm]);
  const report = days[dayIndex]; const generated = report?.totals.pv_generated_kwh ?? 0;
  const allocations = [['家庭直用', 'direct_self_use_kwh', '#e5ff28'], ['进入电池', 'pv_to_battery_kwh', '#ededeb'], ['光伏外送', 'pv_to_grid_kwh', '#586662']];
  let offset = 0;
  const saving = settlementMetric(decision.settlement, decision.site_id, 'saving_settled');
  return <><div className="layout"><Panel title="PV FLOW / 回放估算" aside={<select aria-label="光伏回放方案" className="field" value={arm} onChange={e => setArm(e.target.value)}><option value="irradiance">irradiance</option><option value="persistence">persistence</option><option value="self_consumption">self_consumption</option><option value="zero_flow">zero_flow</option></select>}>
    {loading ? <p>正在读取光伏日报…</p> : error ? <Notice error>{error}</Notice> : !report ? <Notice>当前站点和方案没有光伏日报，尚不能计算去向比例。</Notice> : <><label>回放原始日期<select aria-label="光伏日报日期" className="field" value={dayIndex} onChange={e => setDayIndex(Number(e.target.value))}>{days.map((day, i) => <option key={day.device_date} value={i}>{day.device_date}</option>)}</select></label><svg className="solix-donut" viewBox="0 0 330 260" role="img" aria-label="光伏电量去向估算"><circle cx="165" cy="125" r="85" fill="none" stroke="#29352d" strokeWidth="28"/>{allocations.map(([name, key, color]) => { const percentage = generated > 0 ? report.totals[key] / generated * 100 : 0; const start = offset; offset += percentage; return <circle key={key} cx="165" cy="125" r="85" fill="none" stroke={color} strokeWidth="28" pathLength="100" strokeDasharray={`${percentage} ${100 - percentage}`} strokeDashoffset={-start} transform="rotate(-90 165 125)"><title>{name}</title></circle>; })}<text x="165" y="122" textAnchor="middle" fill="#eff3eb">{generated.toFixed(2)} kWh</text><text x="165" y="145" textAnchor="middle" fill="#8d9b91">REPLAY ESTIMATE</text></svg><div className="legend">{allocations.map(([name, key, color]) => <span key={key}><i className="dot" style={{ background: color }}/>{name} {report.totals[key]?.toFixed(2) ?? '—'} kWh</span>)}</div><Notice>{report.caveat}</Notice><p className="micro">来源 {report.source_kind} · 弃光 {report.curtailment_status} · {report.steps} 步</p></>}
    </Panel><Panel title="MODEL COST / 记录窗口费用"><div className="bigval">{money(decision.cost?.objective_eur)}</div><p>模型目标，含电池损耗代理及期末电量估值。</p>{decision.cost && <div className="data2"><Metric title="购电成本" value={money(decision.cost.purchase_eur)}/><Metric title="外送收入" value={money(decision.cost.export_revenue_eur)}/><Metric title="电池损耗代理" value={money(decision.cost.degradation_eur)}/><Metric title="期末电量估值" value={money(decision.cost.terminal_credit_eur)}/></div>}<Notice>购电 − 外送收入 + 损耗代理 − 期末估值。外送收入可为负；模型费用不是已结算收益。</Notice><h3>当前站点结算</h3><p>{decision.settlement ? `来源：${String(decision.settlement.source_kind)} · 状态：${String(decision.settlement.status)}` : '未结算 · 尚无结算记录'}</p><div className="bigval">{money(saving)}</div><p>同站点结算节省额；缺少站点指标时显示空值。</p><a className="button secondary" href={`#/decisions/${encodeURIComponent(decision.id)}`}>核对结算原始证据 ↗</a></Panel></div></>;
}

export default function SolixApp() {
  const [page, setPage] = useState(currentPage); const [run, setRun] = useState(''); const [site, setSite] = useState(''); const [menu, setMenu] = useState(false);
  const [local, setLocal] = useState(readLocal); const [message, setMessage] = useState('');
  const energy = useEnergy(run, site); const { station, decision, explanation, overview } = energy;
  useEffect(() => { const changed = () => { setPage(currentPage()); window.scrollTo(0, 0); }; window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  useEffect(() => { document.title = 'SOLIX — 家庭能源智能调度'; document.documentElement.lang = 'zh-CN'; }, []);
  function save(value: LocalState) { setLocal(value); try { localStorage.setItem(localKey, JSON.stringify(value)); setMessage('已保存到当前浏览器 · 未写入设备'); } catch { setMessage('浏览器禁止存储；更改仅在本次打开期间有效'); } }
  if (page === 'evidence') return <><div className="solix-evidence-back"><button onClick={() => go('ai')}>← 返回 SOLIX 家庭能源</button></div><EvidenceApp/></>;
  const health = homeHealth(station, !!energy.error, overview?.collection, overview?.settlement);
  const healthText: Record<string, string> = { demo: '人工演示情景 · 非设备实时状态', attention: '数据或执行需要检查 · 请核对采集、记录新鲜度与回读', unknown: '记录新鲜度未知 · 当前设备状态有待核实', advice: '模型建议可供核对 · 尚未验证执行', verified: '当前记录的指令已核验 · 请结合观测时间', completed: '模拟赛已结束 · 当前为留档快照', empty: '尚无站点记录' };
  return <div className="solix-ui"><div className="app"><aside className="sidebar"><div><div className="logo mono">SOLIX</div><div className="brand-sub">HOME ENERGY<br/>CONTROL PLATFORM</div></div>
    <div className="site-global"><button className="site-global-toggle" aria-expanded={menu} aria-controls="solix-site-options" onClick={() => setMenu(v => !v)}><span className="site-global-top">CURRENT SITE<strong>{station?.site_id ?? '暂无站点'}</strong></span><span>⌄</span></button><div id="solix-site-options" className="site-global-options" hidden={!menu}>{overview?.stations.map(s => <button key={s.site_id} className={station?.site_id === s.site_id ? 'selected' : ''} onClick={() => { setSite(s.site_id); setMenu(false); setMessage(''); }}>{s.site_id}<span>{statusName(s.execution_status)}</span></button>)}</div></div>
    <nav className="nav" aria-label="主导航">{pages.map(([id, icon, title], i) => <button key={id} className={`${page === id ? 'active' : ''} ${id === 'home' ? 'home-nav' : ''}`} onClick={() => { go(id); setMessage(''); }} aria-current={page === id ? 'page' : undefined}><span className="index">{String(i).padStart(2, '0')}</span><span className="navname">{icon}　{title}</span></button>)}</nav>
    <div className="sidebar-bottom"><div className="status"><i className="led"/><span>{station && artificial(station) ? 'DEMO RECORD' : 'EVIDENCE API'}</span></div><div className="sidebar-small">EUR / kW / kWh<br/>SNAPSHOT DRIVEN<br/>SOLIX V1.3</div></div>
    </aside><main className="main"><div className="topbar"><div className="eyebrow solix-wrap">SOLIX / {station?.site_id ?? 'NO DATA'}</div><div className="topmeta"><span>{overview?.stations.length ?? 0} SITES / 05 CHANNELS</span><span className="chip">{station && artificial(station) ? '人工演示' : '记录快照'}</span></div></div>
      <div className="solix-toolbar"><label>数据场次<select className="field" aria-label="数据场次" value={run} onChange={e => { setRun(e.target.value); setSite(''); setMessage(''); }}><option value="">最新场次</option>{energy.runs.map(r => <option key={r.run_id} value={r.run_id}>{r.run_id}</option>)}</select></label><span className="micro">每 10 秒刷新 · 采集 {overview?.collection?.state ?? 'unknown'}</span><button className="button secondary" onClick={energy.reload}>刷新数据 ↻</button></div>
      {energy.error && <Notice error>刷新失败：{energy.error}。保留的快照可能已过期。</Notice>}
      {energy.detailError && <Notice error>详情读取失败：{energy.detailError}。详细证据可能未更新。</Notice>}
      {message && <Notice>{message}</Notice>}
      {energy.loading && !station && <Notice>正在读取站点记录…</Notice>}
      {!energy.loading && !station && <Notice>当前场次暂无决策记录。请由采集或导入模块提供数据后刷新。</Notice>}
      {station && <><Notice error={health === 'attention'}>{healthText[health]}<br/><span className="micro">设备时间 {stamp(station.device_time)} · 观测时间 {station.observed_at ? stamp(station.observed_at) : '未知'}</span></Notice>
        {page !== 'home' && <div className="heading"><div><div className="eyebrow">SOLIX / {page.toUpperCase()}</div><h1>{pages.find(([p]) => p === page)?.[2]}</h1></div><div className="heading-note">{station.site_id} · {artificial(station) ? '人工演示' : '导入快照'}<br/>功率 kW · 能量 kWh · 费用 EUR</div></div>}
        {page === 'home' && <Core key={station.site_id} station={station} decision={decision} explanation={explanation}/>}
        {page === 'overview' && <><div className="layout"><Panel title="FIG.01 / INDEPENDENT ENERGY SYSTEM"><div className="solix-flow"><div>☼ 光伏<strong>{power(station.pv_kw)}</strong></div><span>↘</span><div>⌂ 家庭负荷<strong>{power(station.load_kw)}</strong></div><div>▥ 电池 SOC<strong>{station.soc_percent.toFixed(1)}%</strong></div><span>↔</span><div>⌁ 电网实测<strong>快照未提供</strong></div></div><p className="micro">连接示意 · 每站独立计量 · 站点之间不传输电能</p><div className="data2"><Metric title="FORECAST STATUS" value={station.forecast_status}/><Metric title="RECORD FRESHNESS" value={station.record_freshness ?? 'unknown'}/></div></Panel><Recommendation station={station} decision={decision} explanation={explanation}/></div>{decision && <Panel title="FIG.03 / RECORDED FORECAST"><Forecast key={decision.id} decision={decision} compact/></Panel>}</>}
        {page === 'timeline' && (decision ? <><Recommendation station={station} decision={decision} explanation={explanation}/><Panel title="FIG.05 / MULTI-TRACK FORECAST"><Forecast key={decision.id} decision={decision}/></Panel></> : <Notice>等待当前站点详细预测记录…</Notice>)}
        {page === 'ai' && <DecisionWorkspace key={`${station.run_id}/${station.site_id}`} kind="ai" decision={decision} explanation={explanation}/>}
        {page === 'plan' && <><LifePlans key={`${station.run_id}/${station.site_id}`} site={station.site_id} local={local} save={save}/><DecisionWorkspace key={`${station.run_id}/${station.site_id}`} kind="plan" decision={decision}/></>}
        {page === 'analytics' && (decision ? <Analytics key={`${decision.run_id}/${decision.site_id}`} decision={decision}/> : <Notice>等待当前站点费用记录…</Notice>)}
        {page === 'preferences' && <Preferences key={station.site_id} site={station.site_id} local={local} save={save}/>}
      </>}
      <footer className="foot"><span>SOLIX / EXPLAINABLE HOME ENERGY</span><span>预测 → 建议 → 提交 → 回读 → 结算</span><a href="#/evidence">证据工作台 ↗</a></footer>
    </main></div></div>;
}
