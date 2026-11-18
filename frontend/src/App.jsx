import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Bell, Check, CheckCircle2,
  ChevronDown, CircleHelp, Clock3, Command, Download, FileSpreadsheet, Files,
  Filter, LayoutDashboard, ListChecks, LoaderCircle, Moon, MoreHorizontal,
  Play, Plus, RefreshCw, Search, Settings, ShieldCheck, Sun, TerminalSquare,
  X, XCircle, Zap,
} from 'lucide-react';

const NAV = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'automations', label: 'Automations', icon: Zap },
  { id: 'tasks', label: 'Task Manager', icon: ListChecks },
  { id: 'files', label: 'Files & Downloads', icon: Files },
  { id: 'activity', label: 'Activity Logs', icon: Activity },
  { id: 'settings', label: 'Settings', icon: Settings },
];
const CATALOG = [
  { id: 'allocation', title: 'Get Allocation', description: 'Retrieve current device allocations across selected markets', group: 'Inventory' },
  { id: 'amounts', title: 'Get IDOO Amounts', description: 'Fetch available IDOO balances by market', group: 'Finance' },
  { id: 'prices', title: 'Get Device Prices', description: 'Collect the latest device pricing information', group: 'Pricing' },
  { id: 'imei_export', title: 'Export IMEIs', description: 'Export IMEI inventory and order reports', group: 'Inventory' },
  { id: 'order_amounts', title: 'Fetch Order Amounts', description: 'Retrieve order totals from the dealer portal', group: 'Orders' },
  { id: 'tracking', title: 'Fetch Tracking', description: 'Collect shipment tracking information', group: 'Orders' },
  { id: 'order_details', title: 'Fetch Order Details', description: 'Retrieve detailed order and shipping information', group: 'Orders' },
  { id: 'fetch_both', title: 'Fetch Both', description: 'Run tracking and order details together', group: 'Orders' },
  { id: 'open_po', title: 'Open PO Tracking', description: 'Process open purchase order tracking updates', group: 'Orders' },
  { id: 'demo', title: 'Demo Automation', description: 'Test the queue, progress updates and task history without portal access', group: 'System' },
];
const ACTIVE = ['Pending', 'Queued', 'Starting', 'Running', 'Cancelling'];

function fmtDate(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
function bytes(value = 0) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
function statusTone(status = '') {
  if (['Completed'].includes(status)) return 'success';
  if (['Running', 'Starting', 'Cancelling'].includes(status)) return 'info';
  if (['Failed', 'Interrupted'].includes(status)) return 'danger';
  if (['Cancelled'].includes(status)) return 'muted';
  return 'warning';
}

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('pw-theme') || 'light');
  const [section, setSection] = useState('overview');
  const [config, setConfig] = useState(null);
  const [connection, setConnection] = useState('connecting');
  const [overview, setOverview] = useState({});
  const [automations, setAutomations] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [files, setFiles] = useState([]);
  const [activity, setActivity] = useState([]);
  const [selectedTask, setSelectedTask] = useState(null);
  const [showDemo, setShowDemo] = useState(false);
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('All');
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);

  const api = useCallback(async (path, options = {}) => {
    if (!config) throw new Error('The desktop backend is still starting.');
    const response = await fetch(`${config.baseUrl}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', 'X-App-Token': config.token, ...(options.headers || {}) },
    });
    const body = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail?.message || body?.detail || `Request failed (${response.status})`);
    return body;
  }, [config]);

  const refresh = useCallback(async () => {
    if (!config) return;
    try {
      const [o, a, t, f, l] = await Promise.all([
        api('/overview'), api('/automations'), api('/tasks?limit=100'), api('/files?limit=200'), api('/activity?limit=150'),
      ]);
      setOverview(o); setAutomations(a); setTasks(t); setFiles(f); setActivity(l); setConnection('online');
      if (selectedTask) {
        const latest = await api(`/tasks/${selectedTask.id}`);
        setSelectedTask(latest);
      }
    } catch (error) {
      setConnection('offline');
      setToast(error.message || 'Could not connect to the backend.');
    }
  }, [api, config, selectedTask?.id]);

  useEffect(() => {
    let alive = true;
    window.pwDesktop?.getBackendConfig().then((value) => { if (alive) setConfig(value); }).catch(() => setConnection('offline'));
    return () => { alive = false; };
  }, []);
  useEffect(() => { if (config) refresh(); }, [config]);
  useEffect(() => {
    if (!config) return undefined;
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [config, refresh]);
  useEffect(() => { localStorage.setItem('pw-theme', theme); }, [theme]);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(''), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  const runDemo = async (steps, delay) => {
    setBusy(true);
    try {
      const result = await api('/tasks', { method: 'POST', body: JSON.stringify({ automation: 'demo', params: { steps: Number(steps), delay_seconds: Number(delay) }, markets: [] }) });
      setToast(`Demo task queued · ${result.id.slice(0, 8)}`);
      setShowDemo(false); setSection('tasks'); await refresh();
    } catch (error) { setToast(error.message); }
    finally { setBusy(false); }
  };
  const cancelTask = async (id) => {
    try { await api(`/tasks/${id}/cancel`, { method: 'POST' }); setToast('Cancellation requested.'); await refresh(); }
    catch (error) { setToast(error.message); }
  };
  const retryTask = async (id) => {
    try { await api(`/tasks/${id}/retry`, { method: 'POST' }); setToast('Task added to the queue.'); await refresh(); }
    catch (error) { setToast(error.message); }
  };
  const openTask = async (task) => {
    try { setSelectedTask(await api(`/tasks/${task.id}`)); }
    catch (error) { setToast(error.message); }
  };
  const downloadFile = async (file) => {
    try {
      const response = await fetch(`${config.baseUrl}/files/${file.id}/download`, { headers: { 'X-App-Token': config.token } });
      if (!response.ok) throw new Error('Download failed. The file may no longer exist.');
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = file.name; link.click(); URL.revokeObjectURL(url);
    } catch (error) { setToast(error.message); }
  };

  const activeCount = tasks.filter((task) => ACTIVE.includes(task.status)).length;
  const completedCount = tasks.filter((task) => task.status === 'Completed').length;
  const failedCount = tasks.filter((task) => ['Failed', 'Interrupted'].includes(task.status)).length;
  const filteredTasks = useMemo(() => tasks.filter((task) => {
    const matchesSearch = `${task.id} ${task.automation} ${task.status}`.toLowerCase().includes(search.toLowerCase());
    return matchesSearch && (filterStatus === 'All' || task.status === filterStatus);
  }), [tasks, search, filterStatus]);

  return <div className="app-shell" data-theme={theme}>
    <header className="topbar">
      <div className="brand-lockup"><div className="brand-mark"><Command size={21} strokeWidth={2.3} /></div><div><div className="brand-name">PURELY <span>WIRELESS</span></div><div className="brand-sub">MARKET OPERATIONS</div></div></div>
      <div className="topbar-center"><span className="workspace-dot" /> Local workspace <span className="topbar-separator">/</span> Desktop app</div>
      <div className="topbar-actions"><div className={`connection-pill ${connection}`}><span className="connection-dot" />{connection === 'online' ? 'System online' : connection === 'connecting' ? 'Connecting' : 'Backend offline'}</div><button className="icon-button" aria-label="Refresh data" title="Refresh data" onClick={refresh}><RefreshCw size={17} /></button><button className="icon-button" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</button><div className="avatar">PW</div></div>
    </header>
    <div className="nav-wrap"><nav className="main-nav">{NAV.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${section === id ? 'active' : ''}`} onClick={() => setSection(id)}><Icon size={17} />{label}{id === 'tasks' && activeCount > 0 && <span className="nav-count">{activeCount}</span>}</button>)}</nav><div className="nav-meta"><span>v0.2.0 · Preview</span><span className="shortcut">LOCAL ONLY</span></div></div>
    <main className="main-content">
      {section === 'overview' && <Overview tasks={tasks} overview={overview} activeCount={activeCount} completedCount={completedCount} failedCount={failedCount} onNavigate={setSection} onOpenTask={openTask} />}
      {section === 'automations' && <Automations automations={automations} onRunDemo={() => setShowDemo(true)} />}
      {section === 'tasks' && <TaskManager tasks={filteredTasks} search={search} setSearch={setSearch} filterStatus={filterStatus} setFilterStatus={setFilterStatus} onOpen={openTask} onCancel={cancelTask} onRetry={retryTask} />}
      {section === 'files' && <FilesPage files={files} onDownload={downloadFile} />}
      {section === 'activity' && <ActivityPage activity={activity} />}
      {section === 'settings' && <SettingsPage theme={theme} setTheme={setTheme} connection={connection} />}
    </main>
    <footer className="footer"><span><ShieldCheck size={13} /> Your data stays on this device</span><span>Purely Wireless Desktop · Local-first operations</span></footer>
    {selectedTask && <TaskDrawer task={selectedTask} onClose={() => setSelectedTask(null)} onCancel={cancelTask} onRetry={retryTask} />}
    {showDemo && <DemoModal onClose={() => setShowDemo(false)} onRun={runDemo} busy={busy} />}
    {toast && <div className="toast"><span className="toast-dot" />{toast}<button aria-label="Dismiss notification" onClick={() => setToast('')}><X size={15} /></button></div>}
  </div>;
}

function PageHeader({ eyebrow, title, description, action }) {
  return <div className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action}</div>;
}
function MetricCard({ label, value, hint, icon: Icon, tone = 'neutral' }) {
  return <div className={`metric-card metric-${tone}`}><div className="metric-top"><span>{label}</span><div className="metric-icon"><Icon size={18} /></div></div><div className="metric-value">{value ?? 0}</div><div className="metric-hint">{hint}</div></div>;
}
function Overview({ tasks, overview, activeCount, completedCount, failedCount, onNavigate, onOpenTask }) {
  const recent = tasks.slice(0, 6);
  const progress = tasks.filter((t) => t.status === 'Running')[0];
  return <>
    <PageHeader eyebrow="WORKSPACE OVERVIEW" title="Good to see you." description="Your automation workspace at a glance. Monitor activity and keep operations moving." action={<button className="button button-primary" onClick={() => onNavigate('automations')}><Plus size={16} /> New automation</button>} />
    <div className="metric-grid"><MetricCard label="Active tasks" value={activeCount} hint="Queued or currently running" icon={LoaderCircle} tone="blue" /><MetricCard label="Completed" value={completedCount} hint="Successful task runs" icon={CheckCircle2} tone="green" /><MetricCard label="Needs attention" value={failedCount} hint="Failed or interrupted" icon={XCircle} tone="red" /><MetricCard label="Files generated" value={overview.files_generated || 0} hint="Available reports and exports" icon={FileSpreadsheet} tone="violet" /></div>
    <div className="overview-grid"><section className="panel recent-panel"><div className="panel-heading"><div><h2>Recent tasks</h2><p>Latest automation activity from this device</p></div><button className="text-button" onClick={() => onNavigate('tasks')}>View all <ArrowRight size={15} /></button></div>{recent.length ? <div className="task-table compact"><div className="table-head"><span>Automation</span><span>Status</span><span>Created</span><span /></div>{recent.map((task) => <button className="task-row" key={task.id} onClick={() => onOpenTask(task)}><span className="task-name-cell"><span className="task-symbol"><TerminalSquare size={16} /></span><span><b>{automationName(task.automation)}</b><small>{task.id.slice(0, 8)}</small></span></span><span><StatusBadge status={task.status} /></span><span className="date-cell">{fmtDate(task.created_at)}</span><span className="row-arrow"><ArrowUpRight size={15} /></span></button>)}</div> : <EmptyState icon={ListChecks} title="No tasks yet" text="Run your first automation to see its activity here." action={<button className="button button-secondary" onClick={() => onNavigate('automations')}>Explore automations</button>} />}</section>
      <section className="panel quick-panel"><div className="panel-heading"><div><h2>Quick launch</h2><p>Start a workflow in seconds</p></div><div className="small-live"><span /> READY</div></div><button className="quick-action" onClick={() => onNavigate('automations')}><div className="quick-icon"><Zap size={18} /></div><div><b>Browse automations</b><small>Explore available workflows</small></div><ArrowRight size={16} /></button><button className="quick-action" onClick={() => onNavigate('files')}><div className="quick-icon violet"><Files size={18} /></div><div><b>Open generated files</b><small>Access reports and exports</small></div><ArrowRight size={16} /></button><div className="system-note"><ShieldCheck size={17} /><div><b>Local-first by design</b><p>Tasks and generated data are stored on this computer.</p></div></div></section></div>
    <div className="bottom-strip"><div className="strip-icon"><Activity size={17} /></div><div><b>{progress ? `${automationName(progress.automation)} is running` : 'Queue is ready'}</b><span>{progress ? (progress.stage || 'Processing task') : `${activeCount} active · ${tasks.length} total tasks in recent history`}</span></div>{progress && <div className="mini-progress"><div style={{ width: `${progress.progress || 0}%` }} /></div>}<button className="text-button" onClick={() => onNavigate('tasks')}>Task manager <ArrowRight size={15} /></button></div>
  </>;
}
function automationName(id) { return CATALOG.find((x) => x.id === id)?.title || id.replaceAll('_', ' '); }
function Automations({ automations, onRunDemo }) {
  const enabled = new Set(automations.filter((a) => a.enabled).map((a) => a.id));
  return <>
    <PageHeader eyebrow="WORKFLOW LIBRARY" title="Automations" description="Choose a workflow to run. Tasks are processed one at a time in queue order." action={<div className="queue-indicator"><span /> SINGLE-TASK QUEUE</div>} />
    <div className="notice-banner"><div className="notice-icon"><CircleHelp size={17} /></div><div><b>Automation integration is being connected in stages</b><p>The desktop interface is ready. The current backend exposes the demo workflow; the portal automations will be enabled as their adapters are integrated.</p></div></div>
    <div className="automation-grid">{CATALOG.map((item, index) => {
      const ready = enabled.has(item.id);
      return <div className={`automation-card ${ready ? 'available' : 'locked'}`} key={item.id}><div className="automation-card-top"><div className={`automation-icon icon-${index % 5}`}><Zap size={18} /></div><span className={`availability ${ready ? 'ready' : ''}`}>{ready ? 'AVAILABLE' : 'INTEGRATION PENDING'}</span></div><div className="automation-group">{item.group}</div><h3>{item.title}</h3><p>{item.description}</p><div className="automation-card-bottom"><span><Clock3 size={14} /> Runs in queue</span>{ready ? <button className="button button-primary small" onClick={onRunDemo}><Play size={14} /> Run now</button> : <button className="button button-disabled small" disabled>Coming soon</button>}</div></div>;
    })}</div>
  </>;
}
function StatusBadge({ status }) { return <span className={`status-badge ${statusTone(status)}`}><i />{status}</span>; }
function TaskManager({ tasks, search, setSearch, filterStatus, setFilterStatus, onOpen, onCancel, onRetry }) {
  const statuses = ['All', 'Queued', 'Starting', 'Running', 'Completed', 'Failed', 'Cancelled', 'Interrupted'];
  return <><PageHeader eyebrow="EXECUTION CONTROL" title="Task manager" description="Track progress, inspect logs and manage queued or completed tasks." action={<div className="queue-indicator"><span /> FIFO · ONE AT A TIME</div>} />
    <div className="toolbar"><div className="search-box"><Search size={16} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tasks or automation..." /></div><div className="filter-select"><Filter size={15} /><select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>{statuses.map((s) => <option key={s}>{s}</option>)}</select><ChevronDown size={14} /></div></div>
    <section className="panel table-panel"><div className="panel-heading"><div><h2>Task history</h2><p>{tasks.length} tasks matching your filters</p></div><span className="table-live"><span /> AUTO REFRESH</span></div>{tasks.length ? <div className="task-table"><div className="table-head task-table-head"><span>Automation / Task ID</span><span>Status</span><span>Progress</span><span>Created</span><span>Actions</span></div>{tasks.map((task) => <div className="task-row full" key={task.id}><button className="task-name-cell clickable" onClick={() => onOpen(task)}><span className="task-symbol"><TerminalSquare size={16} /></span><span><b>{automationName(task.automation)}</b><small>{task.id}</small></span></button><span><StatusBadge status={task.status} /></span><span className="progress-cell"><div className="progress-track"><div style={{ width: `${task.progress || 0}%` }} /></div><small>{task.progress == null ? '—' : `${task.progress}%`}</small></span><span className="date-cell">{fmtDate(task.created_at)}</span><span className="action-cell">{ACTIVE.includes(task.status) ? <button className="mini-action danger-action" onClick={() => onCancel(task.id)} title="Cancel task"><X size={15} /></button> : ['Failed', 'Interrupted', 'Cancelled'].includes(task.status) ? <button className="mini-action" onClick={() => onRetry(task.id)} title="Retry task"><RefreshCw size={15} /></button> : <button className="mini-action" onClick={() => onOpen(task)} title="View details"><ArrowUpRight size={15} /></button>}</span></div>)}</div> : <EmptyState icon={Search} title="No matching tasks" text="Try changing your search or status filter." />}</section>
  </>;
}
function FilesPage({ files, onDownload }) {
  return <><PageHeader eyebrow="OUTPUT LIBRARY" title="Files & Downloads" description="Reports, spreadsheets and exports generated by your automation tasks." action={<div className="file-count"><Files size={16} /> {files.length} FILES</div>} /><section className="panel table-panel"><div className="panel-heading"><div><h2>Generated files</h2><p>Files registered by completed or running tasks</p></div></div>{files.length ? <div className="task-table"><div className="table-head file-head"><span>File</span><span>Automation</span><span>Size</span><span>Created</span><span /></div>{files.map((file) => <div className="task-row file-row" key={file.id}><span className="task-name-cell"><span className="file-icon"><FileSpreadsheet size={17} /></span><span><b>{file.name}</b><small>{file.task_id.slice(0, 8)}</small></span></span><span className="plain-cell">{automationName(file.automation)}</span><span className="plain-cell">{bytes(file.size)}</span><span className="date-cell">{fmtDate(file.created_at)}</span><span><button className="mini-action download-action" onClick={() => onDownload(file)} title="Download file"><Download size={15} /></button></span></div>)}</div> : <EmptyState icon={Files} title="No generated files yet" text="Files created by supported automations will appear here, ready to download." />}</section></>;
}
function ActivityPage({ activity }) {
  return <><PageHeader eyebrow="SYSTEM TIMELINE" title="Activity logs" description="A chronological view of task events, warnings and errors." action={<div className="file-count"><Activity size={16} /> LIVE FEED</div>} /><section className="panel activity-panel"><div className="panel-heading"><div><h2>Recent events</h2><p>Latest 150 task events · refreshes automatically</p></div></div>{activity.length ? <div className="activity-list">{activity.map((event) => <div className="activity-row" key={event.id}><div className={`activity-level level-${event.level.toLowerCase()}`}>{event.level === 'ERROR' ? <XCircle size={15} /> : event.level === 'WARNING' ? <Bell size={15} /> : <Check size={15} />}</div><div className="activity-message"><b>{event.message}</b><small>Task {event.task_id.slice(0, 8)} · {fmtDate(event.timestamp)}</small></div><span className={`level-label level-text-${event.level.toLowerCase()}`}>{event.level}</span></div>)}</div> : <EmptyState icon={Activity} title="Nothing to report" text="Task events will appear here as automations run." />}</section></>;
}
function SettingsPage({ theme, setTheme, connection }) {
  return <><PageHeader eyebrow="PREFERENCES & CONFIGURATION" title="Settings" description="Manage the desktop experience and check your local runtime." /><div className="settings-grid"><section className="panel settings-panel"><div className="settings-section-title"><div className="settings-icon"><Sun size={18} /></div><div><h2>Appearance</h2><p>Choose how Purely Wireless looks on this device.</p></div></div><div className="theme-options"><button className={`theme-choice ${theme === 'light' ? 'selected' : ''}`} onClick={() => setTheme('light')}><div className="theme-preview preview-light"><div /><span /><i /></div><div><b>Light</b><small>Clean and bright</small></div>{theme === 'light' && <CheckCircle2 size={17} />}</button><button className={`theme-choice ${theme === 'dark' ? 'selected' : ''}`} onClick={() => setTheme('dark')}><div className="theme-preview preview-dark"><div /><span /><i /></div><div><b>Dark</b><small>Easy on the eyes</small></div>{theme === 'dark' && <CheckCircle2 size={17} />}</button></div><div className="setting-footnote"><Check size={14} /> Your theme preference is saved automatically on this computer.</div></section>
    <section className="panel settings-panel"><div className="settings-section-title"><div className="settings-icon green"><ShieldCheck size={18} /></div><div><h2>Local runtime</h2><p>Connection and data storage status.</p></div></div><div className="runtime-list"><div><span>Backend connection</span><b className={connection === 'online' ? 'text-green' : 'text-red'}>{connection === 'online' ? 'Connected' : connection === 'connecting' ? 'Connecting' : 'Disconnected'}</b></div><div><span>Execution mode</span><b>Local · Sequential</b></div><div><span>Task processing</span><b>One task at a time</b></div><div><span>Data storage</span><b>This device</b></div><div><span>Application version</span><b>0.2.0 Preview</b></div></div></section>
    <section className="panel settings-panel wide-setting"><div className="settings-section-title"><div className="settings-icon violet"><Settings size={18} /></div><div><h2>Coming in the next build</h2><p>Additional configuration will be added as the automation adapters are integrated.</p></div></div><div className="coming-list"><span>Credential workbook import and replacement</span><span>Per-automation browser visibility</span><span>Default market preferences</span><span>Backup and restore controls</span></div></section></div></>;
}
function EmptyState({ icon: Icon, title, text, action }) { return <div className="empty-state"><div className="empty-icon"><Icon size={22} /></div><h3>{title}</h3><p>{text}</p>{action}</div>; }
function TaskDrawer({ task, onClose, onCancel, onRetry }) {
  return <div className="drawer-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}><aside className="task-drawer"><div className="drawer-header"><div><div className="eyebrow">TASK DETAILS</div><h2>{automationName(task.automation)}</h2></div><button className="icon-button" aria-label="Close task details" onClick={onClose}><X size={18} /></button></div><div className="drawer-id">{task.id}</div><div className="drawer-status"><StatusBadge status={task.status} /><span>{task.progress == null ? 'Progress not reported' : `${task.progress}% complete`}</span></div><div className="drawer-progress"><div style={{ width: `${task.progress || 0}%` }} /></div><div className="detail-grid"><div><small>Current stage</small><b>{task.stage || '—'}</b></div><div><small>Created</small><b>{fmtDate(task.created_at)}</b></div><div><small>Started</small><b>{fmtDate(task.started_at)}</b></div><div><small>Finished</small><b>{fmtDate(task.finished_at)}</b></div></div>{task.error && <div className="error-box"><b>Error</b><p>{task.error}</p></div>}<div className="drawer-section"><h3>Execution logs <span>{task.logs?.length || 0}</span></h3><div className="drawer-logs">{task.logs?.length ? task.logs.map((log, i) => <div className="drawer-log" key={`${log.timestamp}-${i}`}><time>{new Date(log.timestamp).toLocaleTimeString()}</time><span className={`log-level ${log.level.toLowerCase()}`}>{log.level}</span><p>{log.message}</p></div>) : <p className="muted-copy">No logs recorded.</p>}</div></div>{task.files?.length > 0 && <div className="drawer-section"><h3>Generated files</h3>{task.files.map((file) => <div className="drawer-file" key={file.id}><FileSpreadsheet size={16} /><span>{file.name}</span><small>{bytes(file.size)}</small></div>)}</div>}<div className="drawer-actions">{ACTIVE.includes(task.status) && <button className="button button-danger" onClick={() => onCancel(task.id)}><X size={15} /> Cancel task</button>}{['Failed', 'Interrupted', 'Cancelled'].includes(task.status) && <button className="button button-primary" onClick={() => onRetry(task.id)}><RefreshCw size={15} /> Retry task</button>}</div></aside></div>;
}
function DemoModal({ onClose, onRun, busy }) {
  const [steps, setSteps] = useState(5); const [delay, setDelay] = useState(1);
  return <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}><div className="modal"><div className="modal-header"><div><div className="eyebrow">QUEUE TEST</div><h2>Run demo automation</h2><p>Safely test task execution without logging into any portal.</p></div><button className="icon-button" aria-label="Close task details" onClick={onClose}><X size={18} /></button></div><label className="field-label">Number of steps <b>{steps}</b></label><input className="range-input" type="range" min="1" max="20" value={steps} onChange={(e) => setSteps(e.target.value)} /><div className="range-labels"><span>1 step</span><span>20 steps</span></div><label className="field-label delay-label">Delay per step <b>{delay}s</b></label><div className="delay-options">{[0, 0.5, 1, 2, 3].map((n) => <button key={n} className={Number(delay) === n ? 'selected' : ''} onClick={() => setDelay(n)}>{n}s</button>)}</div><div className="modal-note"><Clock3 size={15} /> Estimated duration: about {(steps * delay).toFixed(1)} seconds</div><div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={busy} onClick={() => onRun(steps, delay)}>{busy ? <LoaderCircle size={16} className="spin" /> : <Play size={15} />} Add to queue</button></div></div></div>;
}
