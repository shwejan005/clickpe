import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Activity, ArrowLeft, ArrowRight, Bell, Boxes, Check, CheckCircle2, Clock3, Database, Eye, FileSpreadsheet, FileText, Gauge, LayoutDashboard, LoaderCircle, LogOut, Menu, PackageSearch, Pencil, RefreshCw, Search, Shield, ShieldCheck, Upload, Users, Workflow, X } from "lucide-react";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { apiJson } from "./lib/api";
import { AlertBox, Badge, Button, Card, CardContent, CardHeader, ConfirmDialog, EmptyState, Input, LoadingBlock, PageTitle, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./components/ui";

type RunSummary = {
  batch_id: string; file_name: string; status: string; rows_total: number; rows_imported: number; rows_rejected: number;
  errors: { row: number; message: string }[]; created_at: string;
  match_counts: Record<string, number>; notification_counts: Record<string, number>;
  match_job: null | { job_id: string; status: string; attempt: number; progress_pairs: number; created_matches: number; needs_information_pairs: number; error?: string; created_at: string; started_at?: string; finished_at?: string };
};
type RunList = { items: RunSummary[]; page: number; page_size: number; total: number; pages: number };
type RunDetail = RunSummary & {
  jobs: { job_id: string; status: string; attempt: number; progress_pairs: number; created_matches: number; needs_information_pairs: number; criteria_fingerprint: string; error?: string; created_at: string; started_at?: string; finished_at?: string }[];
  products: { product_id: number; lender: string; product_name: string; rate_min: number | null; rate_max: number | null; rate_label: string | null; rate_url: string; eligibility_url: string; product_status: string; criteria_version: number; match_status: string; match_count: number }[];
  events: { id: number; stage: string; status: string; message: string; details: Record<string, unknown>; occurred_at: string; job_id?: string }[];
  row_errors: { row: number; message: string }[];
};
type Product = { id: number; lender: string; product_name: string; product_url: string; rate_url: string; eligibility_url: string; rate_min: number | null; rate_max: number | null; rate_label: string | null; criteria: Record<string, unknown>; criteria_version: number; status: string; last_checked_at: string | null; stale: boolean };
type Review = { product_id: number; lender: string; product_name: string; product_status: string; proposal_sha256: string; proposal: { model: string; status: string; source_url: string; criteria: { currency?: string; segments?: Record<string, Record<string, string | number | boolean>> }; evidence: { field: string; quote: string; source_url: string }[]; uncertainties: string[]; usage?: { totalTokenCount?: number } } };
type Summary = { metrics: Record<string, number>; active_jobs: { batch_id: string; job_id: string; status: string; attempt: number; progress_pairs: number; created_at: string }[]; recent_runs: RunSummary[] };

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: true } } });
function date(value?: string | null) { return value ? new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—"; }
function count(value?: number) { return (value || 0).toLocaleString("en-IN"); }
function statusTone(status?: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (["completed", "verified", "sent_to_local_test_inbox", "delivered", "approved"].includes(status || "")) return "success";
  if (["running", "queued", "sending"].includes(status || "")) return "info";
  if (["failed", "error", "stale", "rejected"].includes(status || "")) return status === "stale" ? "warning" : "danger";
  if (["needs_information", "pending", "pending_human_review", "completed_with_errors"].includes(status || "")) return "warning";
  return "neutral";
}
function stateLabel(status?: string) { return (status || "unknown").replaceAll("sent_to_local_test_inbox", "test inbox delivery").replaceAll("_", " "); }

function App() {
  return <QueryClientProvider client={queryClient}><TooltipProvider delayDuration={150}><AuthProvider><BrowserRouter><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<RequireAuth />}><Route element={<DashboardShell />}>
      <Route index element={<OverviewPage />} />
      <Route path="runs" element={<RunsPage />} />
      <Route path="runs/:batchId" element={<RunDetailPage />} />
      <Route path="uploads/new" element={<UploadPage />} />
      <Route path="products" element={<ProductsPage />} />
      <Route path="reviews" element={<ReviewPage />} />
      <Route path="operations" element={<OperationsPage />} />
      <Route element={<RequireWorkspaceAdmin />}><Route path="settings/team" element={<TeamPage />} /></Route>
    </Route></Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></BrowserRouter></AuthProvider></TooltipProvider></QueryClientProvider>;
}

function RequireAuth() {
  const { profile, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="grid min-h-screen place-items-center bg-slate-50"><LoadingBlock label="Restoring your secure workspace" /></div>;
  if (!profile) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

function LoginPage() {
  const { profile, firebaseReady, authError, signIn, loading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (loading) return <div className="grid min-h-screen place-items-center"><LoadingBlock /></div>;
  if (profile) return <Navigate to={(location.state as { from?: string } | null)?.from || "/"} replace />;
  async function login() {
    setBusy(true); setError("");
    try { await signIn(); navigate((location.state as { from?: string } | null)?.from || "/", { replace: true }); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not sign in."); }
    finally { setBusy(false); }
  }
  return <div className="login-layout">
    <section className="login-brand-panel"><Link to="/login" className="brand-lockup"><img src="/clickpe-mark.svg" alt="" /><span>ClickPe</span><small>LOAN OPS</small></Link><div className="login-hero"><Badge tone="success"><span className="size-1.5 rounded-full bg-emerald-500" /> PRIVATE OPERATIONS WORKSPACE</Badge><h1>Every run.<br />Every decision.<br /><span>In one place.</span></h1><p>Manage applicant uploads, monitor real workflow progress, and review lender criteria with complete source evidence.</p></div><div className="login-panel-foot"><ShieldCheck size={17} /> Workspace data is access-controlled.</div></section>
    <section className="login-form-panel"><div className="w-full max-w-sm"><div className="mb-8 grid size-12 place-items-center rounded-2xl bg-blue-50 text-blue-700"><Shield size={22} /></div><p className="mb-2 text-[10px] font-bold uppercase tracking-[.18em] text-blue-600">SECURE SIGN IN</p><h2 className="font-display text-3xl font-extrabold tracking-tight text-navy-950">Welcome back</h2><p className="mt-2 text-sm leading-6 text-slate-500">Sign in with the Firebase-enabled Google account invited to your workspace.</p>
      {(!firebaseReady || authError || error) && <AlertBox className="mt-6">{error || authError || "Firebase configuration is missing. Add the VITE_FIREBASE_* settings to .env and restart the frontend."}</AlertBox>}
      {!firebaseReady && <div className="mt-4 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">Firebase Web SDK configuration is not available in this frontend build.</div>}
      <Button size="lg" className="mt-7 w-full" disabled={!firebaseReady || busy} onClick={() => void login()}><GoogleMark />{busy ? "Connecting securely…" : "Continue with Google"}</Button>
      <p className="mt-5 text-center text-xs leading-5 text-slate-400">Access is granted by workspace invitation or the configured administrator account.</p>
      <div className="mt-8 border-t border-slate-100 pt-5 text-center text-[11px] text-slate-400">Firebase Authentication · Secure server session</div>
    </div></section>
  </div>;
}

function GoogleMark() { return <svg width="17" height="17" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3A12 12 0 1 1 31 15.1l5.7-5.7A20 20 0 1 0 44 24c0-1.2-.1-2.3-.4-3.5Z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8A12 12 0 0 1 31 15.1l5.7-5.7A20 20 0 0 0 6.3 14.7Z"/><path fill="#4CAF50" d="M24 44a20 20 0 0 0 13.5-5.2l-6.2-5.3A12 12 0 0 1 12.8 28l-6.5 5A20 20 0 0 0 24 44Z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3a12 12 0 0 1-4 5.5l6.2 5.3A20 20 0 0 0 44 24c0-1.2-.1-2.3-.4-3.5Z"/></svg>; }

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/uploads/new", label: "New upload", icon: Upload },
  { to: "/products", label: "Products", icon: Boxes },
  { to: "/reviews", label: "Criteria review", icon: ShieldCheck, capability: "can_review_criteria" as const },
  { to: "/operations", label: "Operations", icon: Workflow },
  { to: "/settings/team", label: "Team & access", icon: Users, capability: "can_manage_workspace" as const },
];

function RequireWorkspaceAdmin() {
  const { profile } = useAuth();
  return profile?.capabilities.can_manage_workspace ? <Outlet /> : <Navigate to="/" replace />;
}

function DashboardShell() {
  const { profile, signOutUser, switchWorkspace } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [signoutBusy, setSignoutBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const queryClient = useQueryClient();
  const workspaces = profile?.workspaces || [];
  async function logout() { setSignoutBusy(true); try { await signOutUser(); queryClient.clear(); } finally { setSignoutBusy(false); } }
  async function changeWorkspace(workspaceId: string) { setWorkspaceError(""); try { await switchWorkspace(workspaceId); queryClient.clear(); } catch (error) { setWorkspaceError(error instanceof Error ? error.message : "Could not switch workspace."); } }
  const items = nav.filter((item) => !item.capability || profile?.capabilities[item.capability]);
  const accountPanel = <div className="mt-auto border-t border-white/10 pt-4"><div className="flex min-w-0 items-center gap-3 px-2"><div className="grid size-9 shrink-0 place-items-center rounded-full bg-white/10 text-xs font-bold text-white">{(profile?.user.display_name || profile?.user.email || "U").slice(0,1).toUpperCase()}</div><div className="min-w-0"><div className="truncate text-xs font-semibold text-white">{profile?.user.display_name || profile?.user.email}</div><div className="truncate text-[10px] capitalize text-slate-400">{profile?.workspace_role?.replaceAll("_"," ")}</div></div></div><Button variant="ghost" className="mt-3 w-full justify-start text-slate-300 hover:bg-white/10 hover:text-white" disabled={signoutBusy} onClick={() => void logout()}>{signoutBusy?<LoaderCircle size={16} className="animate-spin"/>:<LogOut size={16}/>}Sign out</Button></div>;
  return <div className="min-h-screen bg-[#f5f7fb]">
    <aside className="sidebar hidden lg:flex">
      <Link to="/" className="brand-lockup"><img src="/clickpe-mark.svg" alt="" /><span>ClickPe</span></Link><div className="sidebar-label">MENU</div>
      <nav className="sidebar-nav">{items.map((item) => <NavItem key={item.to} item={item} onClick={() => setMobileOpen(false)} />)}</nav>
      {accountPanel}
    </aside>
    <div className="lg:pl-[250px]">
      <header className="dashboard-topbar"><button className="grid size-9 place-items-center rounded-lg border border-slate-200 bg-white text-slate-600 lg:hidden" onClick={() => setMobileOpen(!mobileOpen)} aria-label="Toggle navigation">{mobileOpen ? <X size={17} /> : <Menu size={17} />}</button>{workspaces.length > 1 && <select aria-label="Select workspace" className="max-w-52 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-navy-950" value={profile?.workspace?.id || ""} onChange={(event) => void changeWorkspace(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select>}{workspaces.length === 1 && <div className="text-xs font-semibold text-navy-950">{profile?.workspace?.name}</div>}{workspaceError && <span role="alert" className="ml-auto text-[10px] text-red-600">{workspaceError}</span>}</header>
      {mobileOpen && <div className="fixed inset-0 z-40 bg-navy-950/25 lg:hidden" onClick={() => setMobileOpen(false)}><nav className="flex h-full w-72 flex-col bg-navy-950 p-4 pt-20" onClick={(e) => e.stopPropagation()}>{items.map((item) => <NavItem key={item.to} item={item} onClick={() => setMobileOpen(false)} />)}{accountPanel}</nav></div>}
      <main className="dashboard-main"><Outlet /></main>
    </div>
  </div>;
}

function NavItem({ item, onClick }: { item: typeof nav[number]; onClick?: () => void }) {
  const Icon = item.icon;
  return <NavLink onClick={onClick} to={item.to} end={item.end} className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`}><Icon size={17} strokeWidth={1.8} /><span>{item.label}</span>{item.to === "/reviews" && <span className="ml-auto size-1.5 rounded-full bg-[#1cff7d]" />}</NavLink>;
}

function OverviewPage() {
  const { profile } = useAuth();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["dashboard"], queryFn: () => apiJson<Summary>("/api/dashboard/summary"), refetchInterval: (query) => query.state.data?.active_jobs.length ? 2500 : false });
  if (isLoading) return <LoadingBlock label="Loading dashboard metrics" />;
  if (error) return <PageError error={error} retry={() => void refetch()} />;
  const m = data?.metrics || {};
  return <>
    <PageTitle eyebrow="LOAN OPERATIONS" title="Overview" description={`Good to see you, ${profile?.user.display_name || profile?.user.email}. Here’s your workspace at a glance.`} actions={<Link to="/uploads/new"><Button><Upload size={15} /> Upload applicants</Button></Link>} />
    {!!data?.active_jobs.length && <Card className="mb-6 overflow-hidden border-blue-200 bg-gradient-to-r from-blue-50 to-white"><div className="flex flex-wrap items-center gap-4 p-5"><div className="grid size-11 place-items-center rounded-xl bg-blue-600 text-white"><Workflow size={20} /></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h2 className="font-display font-bold text-navy-950">Workflow in progress</h2><Badge tone="info"><span className="size-1.5 animate-pulse rounded-full bg-blue-600" /> Live</Badge></div><p className="mt-1 truncate text-xs text-slate-500">{data.active_jobs.length} matching job(s) · latest activity {date(data.active_jobs[0]?.created_at)}</p></div><Link to={`/runs/${data.active_jobs[0].batch_id}`}><Button variant="outline" size="sm">View run <ArrowRight size={14} /></Button></Link></div></Card>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Upload runs" value={m.runs} icon={<FileSpreadsheet size={18} />} color="blue" to="/runs" />
      <MetricCard label="Applicants imported" value={m.applicants_imported} icon={<Users size={18} />} color="green" to="/runs" />
      <MetricCard label="Potential matches" value={m.potential_matches} icon={<PackageSearch size={18} />} color="purple" to="/runs" hint={`${count(m.needs_information)} need more information`} />
      <MetricCard label="Needs attention" value={(m.criteria_awaiting_review || 0) + (m.pending_notifications || 0) + (m.stale_or_unverified_products || 0)} icon={<Bell size={18} />} color="amber" to={profile?.capabilities.can_review_criteria ? "/reviews" : "/operations"} hint={`${count(m.criteria_awaiting_review)} criteria · ${count(m.pending_notifications)} notifications`} />
    </div>
    <section className="mt-8"><div className="mb-4 flex items-end justify-between"><div><p className="section-kicker">ACTIVITY</p><h2 className="section-title">Recent runs</h2></div><Link className="text-xs font-semibold text-blue-700 hover:text-blue-800" to="/runs">All runs <ArrowRight className="ml-1 inline" size={13} /></Link></div><RunsTable items={data?.recent_runs || []} compact /></section>
    <div className="mt-8 grid gap-4 xl:grid-cols-2"><Card><CardHeader><div><p className="section-kicker">PIPELINE</p><h2 className="card-title">How a run moves</h2></div><Activity className="text-blue-600" size={18} /></CardHeader><CardContent><div className="grid grid-cols-3 gap-2 text-center">{[{n:"01",t:"Upload & validate"},{n:"02",t:"SQL matching"},{n:"03",t:"Test notifications"}].map((s,i)=><div key={s.n} className="rounded-xl bg-slate-50 p-3"><div className={`mx-auto mb-2 grid size-7 place-items-center rounded-full text-[10px] font-bold ${i===0?"bg-[#1cff7d] text-navy-950":"bg-blue-100 text-blue-700"}`}>{s.n}</div><div className="text-[10px] font-semibold leading-4 text-slate-600">{s.t}</div></div>)}</div><p className="mt-3 text-[10px] text-slate-400">Workflow A lender discovery runs separately; it is not triggered by every CSV upload.</p></CardContent></Card>
      <Card><CardHeader><div><p className="section-kicker">DATA QUALITY</p><h2 className="card-title">Product source health</h2></div><Database className="text-blue-600" size={18} /></CardHeader><CardContent><div className="flex items-center justify-between rounded-xl bg-amber-50 p-4"><div><div className="font-semibold text-navy-950">{count(m.stale_or_unverified_products)} stale or unverified</div><div className="mt-1 text-xs text-slate-500">Only fresh verified products enter new matches.</div></div><Link to="/products"><Button size="sm" variant="outline">View catalog</Button></Link></div></CardContent></Card></div>
  </>;
}

function MetricCard({ label, value, icon, color, to, hint }: { label: string; value?: number; icon: React.ReactNode; color: string; to: string; hint?: string }) {
  const colors: Record<string, string> = { blue: "bg-blue-50 text-blue-700", green: "bg-emerald-50 text-emerald-700", purple: "bg-violet-50 text-violet-700", amber: "bg-amber-50 text-amber-700" };
  return <Link to={to} className="metric-card group"><div className="flex items-center justify-between"><span className="text-xs font-medium text-slate-500">{label}</span><span className={`grid size-9 place-items-center rounded-xl ${colors[color]}`}>{icon}</span></div><div className="mt-4 font-display text-3xl font-extrabold tracking-tight text-navy-950 tabular-nums">{count(value)}</div><div className="mt-1 min-h-4 text-[10px] text-slate-400">{hint || "Across this workspace"}</div></Link>;
}

function RunsPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (search.trim()) params.set("query", search.trim());
  const query = useQuery({ queryKey: ["runs", page, search], queryFn: () => apiJson<RunList>(`/api/runs?${params}`) });
  return <><PageTitle eyebrow="WORKSPACE HISTORY" title="Runs" description="Every uploaded batch, its matching job, and the notification outcome." actions={<Link to="/uploads/new"><Button><Upload size={15} /> New upload</Button></Link>} />
    <div className="mb-4 flex items-center gap-2"><div className="relative max-w-sm flex-1"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15} /><Input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Search file name" className="pl-9" /></div><Button variant="outline" size="icon" title="Refresh" onClick={() => void query.refetch()}><RefreshCw size={15} /></Button></div>
    {query.isLoading ? <LoadingBlock label="Loading run history" /> : query.error ? <PageError error={query.error} retry={() => void query.refetch()} /> : <><RunsTable items={query.data?.items || []} />{(query.data?.pages || 0) > 1 && <div className="mt-4 flex items-center justify-between text-xs text-slate-500"><span>Page {page} of {query.data?.pages} · {count(query.data?.total)} runs</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page<=1} onClick={() => setPage(page-1)}>Previous</Button><Button variant="outline" size="sm" disabled={page>= (query.data?.pages || 1)} onClick={() => setPage(page+1)}>Next</Button></div></div>}</>}
  </>;
}

function RunsTable({ items, compact = false }: { items: RunSummary[]; compact?: boolean }) {
  if (!items.length) return <EmptyState icon={<FileText size={20} />} title="No runs yet" description="Upload a CSV to begin. Each batch will appear here with matching and notification history." action={<Link to="/uploads/new"><Button size="sm">Start an upload</Button></Link>} />;
  return <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><div className="overflow-x-auto"><table className="w-full min-w-[780px] text-left"><thead><tr className="border-b border-slate-100 bg-slate-50/80 text-[10px] font-bold uppercase tracking-wider text-slate-400"><th className="px-4 py-3">Upload</th><th className="px-4 py-3">Imported</th><th className="px-4 py-3">Matches</th><th className="px-4 py-3">Notifications</th><th className="px-4 py-3">Run state</th><th className="px-4 py-3">Created</th><th /></tr></thead><tbody>{items.map((run) => <tr key={run.batch_id} className="table-row border-b border-slate-50 last:border-0"><td className="px-4 py-3"><Link to={`/runs/${run.batch_id}`} className="block max-w-[240px] truncate text-xs font-semibold text-navy-950 hover:text-blue-700">{run.file_name}</Link><span className="mt-1 block font-mono text-[9px] text-slate-400">{run.batch_id.slice(0, 13)}…</span></td><td className="px-4 py-3"><div className="text-xs font-semibold text-slate-700">{count(run.rows_imported)}</div><div className="text-[10px] text-slate-400">{count(run.rows_rejected)} rejected</div></td><td className="px-4 py-3"><div className="text-xs font-semibold text-slate-700">{count(sum(run.match_counts))}</div><div className="text-[10px] text-slate-400">{count(run.match_counts?.needs_information)} need info</div></td><td className="px-4 py-3"><span className="text-xs font-semibold text-slate-700">{count(run.notification_counts?.sent_to_local_test_inbox)} sent</span><div className="text-[10px] text-slate-400">{count(run.notification_counts?.pending)} pending</div></td><td className="px-4 py-3"><Badge tone={statusTone(run.match_job?.status || run.status)}>{stateLabel(run.match_job?.status || run.status)}</Badge></td><td className="px-4 py-3 text-[10px] text-slate-500">{date(run.created_at)}</td><td className="px-4 py-3"><Link className="grid size-8 place-items-center rounded-lg text-slate-400 hover:bg-blue-50 hover:text-blue-700" to={`/runs/${run.batch_id}`} title="View run"><ArrowRight size={15} /></Link></td></tr>)}</tbody></table></div>{compact && <div className="border-t border-slate-100 px-4 py-3 text-right"><Link className="text-xs font-semibold text-blue-700" to="/runs">View complete history <ArrowRight className="ml-1 inline" size={13} /></Link></div>}</div>;
}
function sum(counts?: Record<string, number>) { return Object.values(counts || {}).reduce((total, value) => total + value, 0); }

function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const client = useQueryClient();
  const mutation = useMutation({ mutationFn: (selected: File) => uploadFile(selected, setProgress), onSuccess: (result: RunSummary & { duplicate_upload: boolean }) => {
    client.invalidateQueries({ queryKey: ["dashboard"] }); client.invalidateQueries({ queryKey: ["runs"] }); navigate(`/runs/${result.batch_id}`);
  }, onError: (e) => setError(e instanceof Error ? e.message : "Upload failed") });
  function pick(next: File | null) { setError(""); if (!next) return; if (!next.name.toLowerCase().endsWith(".csv")) { setError("Choose a .csv file."); return; } if (next.size > 25 * 1024 * 1024) { setError("This file exceeds the 25 MB upload limit."); return; } setFile(next); }
  return <><PageTitle eyebrow="NEW DATA BATCH" title="Upload applicants" description="Upload a CSV to validate applicants and automatically start the SQL matching workflow." actions={<Link to="/runs"><Button variant="outline"><ArrowLeft size={14} /> Run history</Button></Link>} />
    <div className="mx-auto grid max-w-5xl gap-5 xl:grid-cols-[1fr_300px]"><Card><CardContent className="p-6 sm:p-8"><div onDragEnter={(e) => { e.preventDefault(); setDragging(true); }} onDragOver={(e) => e.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0] || null); }} className={`upload-dropzone ${dragging ? "upload-dropzone-active" : ""}`} onClick={() => document.getElementById("csv-upload")?.click()} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") document.getElementById("csv-upload")?.click(); }}>
        <input id="csv-upload" className="hidden" type="file" accept=".csv,text/csv" onChange={(e) => pick(e.target.files?.[0] || null)} />{file ? <><div className="grid size-14 place-items-center rounded-2xl bg-emerald-50 text-emerald-600"><FileSpreadsheet size={25} /></div><div className="mt-4 max-w-full truncate font-semibold text-navy-950">{file.name}</div><div className="mt-1 text-xs text-slate-400">{(file.size / 1024).toFixed(0)} KB · Ready to upload</div></> : <><div className="grid size-14 place-items-center rounded-2xl bg-blue-50 text-blue-600"><Upload size={24} /></div><div className="mt-4 font-semibold text-navy-950">Drop your CSV here or browse</div><div className="mt-1 text-xs text-slate-400">Maximum file size 25 MB</div></>}
      </div>
      {file && <button className="mt-3 text-xs font-semibold text-blue-700 hover:underline" onClick={() => setFile(null)}>Choose a different file</button>}
      <div className="mt-6 rounded-xl border border-slate-100 bg-slate-50 p-4"><div className="flex items-center gap-2 text-xs font-semibold text-slate-700"><FileText size={15} className="text-blue-600" /> Required CSV columns</div><p className="mt-2 break-words font-mono text-[10px] leading-5 text-slate-500">user_id, name, email, monthly_income, credit_score, employment_status, age</p></div>
      {error && <AlertBox className="mt-4">{error}</AlertBox>}{mutation.isPending && <div className="mt-4"><div className="flex justify-between text-xs text-slate-500"><span>Uploading and saving rows</span><span>{progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-600 transition-[width] duration-200" style={{ width: `${progress}%` }} /></div><p className="mt-2 text-[10px] text-slate-400">Matching starts after the upload is committed. Follow the live run on its detail page.</p></div>}
      <Button size="lg" className="mt-5 w-full" disabled={!file || mutation.isPending} onClick={() => { setProgress(0); setError(""); if (file) mutation.mutate(file); }}>{mutation.isPending ? <><LoaderCircle size={16} className="animate-spin" /> Uploading…</> : <>Upload and start matching <ArrowRight size={16} /></>}</Button>
    </CardContent></Card>
      <Card><CardHeader><div><p className="section-kicker">BEFORE YOU START</p><h2 className="card-title">What happens next</h2></div></CardHeader><CardContent className="space-y-4">{[{icon:<CheckCircle2 size={16}/>,title:"Validate & save",desc:"Accepted rows are stored with a batch record; rejected rows are summarized."},{icon:<Workflow size={16}/>,title:"Match with SQL",desc:"Fresh verified criteria are checked by the database. No applicant data is sent to Gemini."},{icon:<Bell size={16}/>,title:"Test notification",desc:"Email is routed to the controlled test inbox, never the CSV address."}].map((step)=><div className="flex gap-3" key={step.title}><span className="mt-0.5 text-blue-600">{step.icon}</span><div><div className="text-xs font-semibold text-navy-950">{step.title}</div><p className="mt-1 text-[10px] leading-4 text-slate-500">{step.desc}</p></div></div>)}</CardContent></Card>
    </div>
  </>;
}

async function uploadFile(file: File, onProgress: (value: number) => void): Promise<RunSummary & { duplicate_upload: boolean }> {
  const csrf = document.cookie.split("; ").find((item) => item.trim().startsWith("clickpe_csrf="))?.split("=")[1];
  const form = new FormData(); form.append("file", file);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("POST", `${import.meta.env.VITE_API_URL || "http://localhost:8000"}/api/uploads`); xhr.withCredentials = true;
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", decodeURIComponent(csrf));
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
    xhr.onload = () => { const payload = JSON.parse(xhr.responseText || "{}"); if (xhr.status >= 200 && xhr.status < 300) resolve(payload); else reject(new Error(payload.detail || `Upload failed (${xhr.status})`)); };
    xhr.onerror = () => reject(new Error("Could not reach the upload service.")); xhr.send(form);
  });
}

function RunDetailPage() {
  const { batchId = "" } = useParams();
  const query = useQuery({ queryKey: ["run", batchId], queryFn: () => apiJson<RunDetail>(`/api/runs/${encodeURIComponent(batchId)}`), refetchInterval: (q) => ["running", "queued"].includes(q.state.data?.match_job?.status || "") ? 1500 : false });
  const client = useQueryClient();
  const notify = useMutation({ mutationFn: () => apiJson("/api/workflows/notifications/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch_id: batchId }) }), onSuccess: () => { client.invalidateQueries({ queryKey: ["run", batchId] }); } });
  const run = query.data;
  if (query.isLoading) return <LoadingBlock label="Loading run details" />;
  if (query.error || !run) return <PageError error={query.error || new Error("Run not found")} retry={() => void query.refetch()} />;
  const job = run.match_job;
  const isActive = ["running", "queued"].includes(job?.status || "");
  const sent = run.notification_counts?.sent_to_local_test_inbox || 0;
  const pending = run.notification_counts?.pending || 0;
  return <><div className="mb-5"><Link to="/runs" className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-blue-700"><ArrowLeft size={14} /> All runs</Link></div>
    <PageTitle eyebrow="RUN DETAIL" title={run.file_name} description={`Batch ${run.batch_id} · uploaded ${date(run.created_at)}`} actions={<Badge tone={statusTone(job?.status || run.status)}>{isActive && <span className="size-1.5 animate-pulse rounded-full bg-blue-600" />}{stateLabel(job?.status || run.status)}</Badge>} />
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><DetailStat label="Rows accepted" value={count(run.rows_imported)} foot={`${count(run.rows_rejected)} rejected`} /><DetailStat label="Pairs evaluated" value={count(job?.progress_pairs)} foot={`${count(job?.attempt)} matching attempt(s)`} /><DetailStat label="Potential matches" value={count(job?.created_matches)} foot={`${count(job?.needs_information_pairs)} need information`} /><DetailStat label="Test notification" value={`${count(sent)} sent`} foot={`${count(pending)} match rows pending`} /></div>
    {(run.rows_rejected > 0 || run.row_errors.length > 0) && <Card className="mt-5"><CardHeader><div><p className="section-kicker">INGESTION</p><h2 className="card-title">Rejected rows</h2></div><Badge tone="warning">{count(run.rows_rejected)} rows</Badge></CardHeader><CardContent><div className="max-h-56 overflow-auto divide-y divide-slate-100">{run.row_errors.map((item,index)=><div className="flex gap-4 py-2 text-xs" key={`${item.row}-${index}`}><span className="w-16 shrink-0 font-mono text-slate-400">Row {item.row}</span><span className="text-slate-600">{item.message}</span></div>)}</div></CardContent></Card>}
    <div className="mt-6 grid gap-5 xl:grid-cols-[1.1fr_.9fr]"><Card><CardHeader><div><p className="section-kicker">LIVE PROCESS</p><h2 className="card-title">Run timeline</h2></div>{isActive && <Badge tone="info"><span className="size-1.5 animate-pulse rounded-full bg-blue-600" /> Auto-updating</Badge>}</CardHeader><CardContent><div className="timeline">{timeline(run).map((step,index)=><div className="timeline-item" key={`${step.stage}-${step.timestamp}-${index}`}><div className={`timeline-node ${step.tone}`}><TimelineIcon stage={step.stage} /></div><div className="min-w-0 flex-1 pb-6"><div className="flex flex-wrap items-center justify-between gap-2"><div className="text-xs font-bold capitalize text-navy-950">{step.title}</div><span className="text-[10px] text-slate-400">{date(step.timestamp)}</span></div><p className="mt-1 text-xs leading-5 text-slate-500">{step.message}</p>{step.detail && <p className="mt-1 text-[10px] text-slate-400">{step.detail}</p>}</div></div>)}</div>
      {job?.error && <AlertBox className="mt-3">Matching failed: {job.error}</AlertBox>}</CardContent></Card>
      <div className="space-y-5"><Card><CardHeader><div><p className="section-kicker">MATCH RESULTS</p><h2 className="card-title">Products in this run</h2></div><PackageSearch size={18} className="text-blue-600" /></CardHeader><CardContent>{run.products.length ? <div className="space-y-3">{run.products.map((product,index)=><div className="rounded-xl border border-slate-100 p-3 transition hover:border-blue-200" key={`${product.product_id}-${product.criteria_version}-${product.match_status}`} style={{ animationDelay: `${index * 60}ms` }}><div className="flex items-start justify-between gap-3"><div><div className="text-xs font-bold text-navy-950">{product.lender}</div><div className="mt-0.5 text-[10px] text-slate-500">{product.product_name}</div></div><Badge tone={statusTone(product.match_status)}>{count(product.match_count)} · {stateLabel(product.match_status)}</Badge></div><div className="mt-3 flex flex-wrap gap-3 text-[10px] text-slate-500"><span>Rate: {product.rate_min != null && product.rate_max != null ? `${product.rate_min}%–${product.rate_max}% p.a.` : "See lender"}</span><span>Criteria v{product.criteria_version}</span></div><div className="mt-2 flex gap-3"><a className="text-[10px] font-semibold text-blue-700" href={product.rate_url} target="_blank" rel="noreferrer">Rate source</a><a className="text-[10px] font-semibold text-blue-700" href={product.eligibility_url} target="_blank" rel="noreferrer">Eligibility source</a></div></div>)}</div> : <p className="rounded-xl bg-slate-50 p-4 text-xs leading-5 text-slate-500">Matching has not produced any product rows yet. This may be a no-match run or the job is still starting.</p>}</CardContent></Card>
      <Card><CardHeader><div><p className="section-kicker">SAFE ACTIONS</p><h2 className="card-title">Notifications</h2></div><Bell size={17} className="text-blue-600" /></CardHeader><CardContent><p className="text-xs leading-5 text-slate-500">Delivery is routed to the controlled test inbox, never to email addresses from this CSV.</p><div className="mt-3 flex items-center justify-between"><span className="text-xs font-semibold text-slate-700">{count(sent)} match rows sent · {count(pending)} pending</span><Button size="sm" variant="outline" disabled={!pending || notify.isPending} onClick={() => notify.mutate()}>{notify.isPending ? "Starting…" : "Send next test page"}</Button></div>{notify.error && <AlertBox className="mt-3">{notify.error.message}</AlertBox>}</CardContent></Card></div>
    </div>
  </>;
}

function timeline(run: RunDetail) {
  const steps: { stage: string; title: string; status: string; message: string; timestamp?: string; detail?: string; tone: string }[] = [
    { stage:"upload", title:"Upload received and validated", status:"completed", message:`${count(run.rows_imported)} accepted from ${count(run.rows_total)} rows; ${count(run.rows_rejected)} rejected.`, timestamp:run.created_at, tone:"done" },
  ];
  if (run.events.length) {
    for (const event of run.events) {
      if (event.stage === "upload") continue;
      steps.push({ stage:event.stage, title:event.stage.replaceAll("_"," "), status:event.status, message:event.message,
        timestamp:event.occurred_at, detail:event.details && Object.keys(event.details).length ? Object.entries(event.details).map(([key,value])=>`${key.replaceAll("_"," ")}: ${String(value)}`).join(" · ") : undefined,
        tone:event.status === "failed" ? "failed" : ["running","queued"].includes(event.status) ? "active" : "done" });
    }
  } else if (run.match_job) {
    steps.push({ stage:"matching", title:"SQL matching", status:run.match_job.status, message:`${count(run.match_job.created_matches)} matches created across ${count(run.match_job.progress_pairs)} evaluated pairs.`, timestamp:run.match_job.finished_at || run.match_job.started_at || run.match_job.created_at, detail:`Attempt ${run.match_job.attempt}`, tone:run.match_job.status === "failed" ? "failed" : run.match_job.status === "running" ? "active" : "done" });
  } else {
    steps.push({ stage:"matching", title:"SQL matching", status:"queued", message:"Waiting for the matching workflow to create a job.", timestamp:undefined, tone:"active" });
  }
  const states = run.notification_counts || {};
  if (sum(states) > 0 || run.rows_imported > 0 && !["queued","running"].includes(run.match_job?.status || "")) {
    const delivered = states.sent_to_local_test_inbox || 0;
    const pending = states.pending || 0;
    steps.push({ stage:"notifications", title:"Controlled test notifications", status:pending ? delivered ? "partial" : "pending" : delivered ? "completed" : "not_sent", message:`${count(delivered)} match rows sent to Mailpit; ${count(pending)} pending.`, timestamp:undefined, detail:"Messages are routed to the configured test inbox.", tone:pending ? "active" : delivered ? "done" : "idle" });
  }
  return steps;
}
function TimelineIcon({ stage }: { stage: string }) { if (stage === "upload") return <Upload size={14} />; if (stage === "matching") return <Workflow size={14} />; if (stage === "notifications") return <Bell size={14} />; return <Check size={14} />; }
function DetailStat({ label, value, foot }: { label: string; value: string; foot: string }) { return <Card className="p-4"><div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</div><div className="mt-2 font-display text-2xl font-extrabold text-navy-950 tabular-nums">{value}</div><div className="mt-1 text-[10px] text-slate-500">{foot}</div></Card>; }

function ProductsPage() {
  const query = useQuery({ queryKey:["products"], queryFn:()=>apiJson<Product[]>("/api/products") });
  const products = query.data || [];
  const [filter,setFilter] = useState("All products");
  const categories = ["All products","Personal loans"];
  const visible=products.filter(p=>filter==="All products"||/personal loan/i.test(p.product_name));
  return <><PageTitle eyebrow="LENDER CATALOG" title="Products" description="Verified public lender products and the current source freshness used by matching." />
    <div className="mb-5 flex flex-wrap gap-2">{categories.map(category=><Button key={category} variant={filter===category?"default":"outline"} size="sm" onClick={()=>setFilter(category)}>{category}</Button>)}</div>
    {query.isLoading?<LoadingBlock label="Loading product catalog"/>:query.error?<PageError error={query.error} retry={()=>void query.refetch()}/>:visible.length===0?<EmptyState icon={<Boxes size={20}/>} title="No products found" description="There are no product records in this category yet."/>:<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visible.map((p,index)=><Card className="product-card group" key={p.id} style={{animationDelay:`${index*45}ms`}}><CardHeader><div className="flex size-10 items-center justify-center rounded-xl bg-blue-50 font-display text-sm font-extrabold text-blue-700">{p.lender.slice(0,1)}</div><Badge tone={p.status==="verified"&&!p.stale?"success":"warning"}>{p.stale?"Stale":stateLabel(p.status)}</Badge></CardHeader><CardContent><p className="text-[10px] font-bold uppercase tracking-wider text-blue-600">{p.lender}</p><h2 className="mt-1 font-display text-base font-bold text-navy-950">{p.product_name}</h2><div className="mt-4 rounded-xl bg-slate-50 p-3"><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Published rate</div><div className="mt-1 font-display text-xl font-extrabold text-navy-950">{p.rate_min!=null&&p.rate_max!=null?`${p.rate_min}% – ${p.rate_max}%`:"See lender"}<span className="ml-1 text-[10px] font-medium text-slate-500">p.a.</span></div><div className="mt-1 text-[9px] text-slate-400">{p.rate_label?.replaceAll("_"," ")||"Rate terms may vary"}</div></div><div className="mt-3 flex items-center justify-between text-[10px] text-slate-500"><span>Last checked</span><span>{date(p.last_checked_at)}</span></div><div className="mt-4 flex gap-3 border-t border-slate-100 pt-3"><a className="text-[10px] font-semibold text-blue-700 hover:underline" href={p.product_url} target="_blank" rel="noreferrer">Product page</a><a className="text-[10px] font-semibold text-blue-700 hover:underline" href={p.eligibility_url} target="_blank" rel="noreferrer">Eligibility</a><a className="text-[10px] font-semibold text-blue-700 hover:underline" href={p.rate_url} target="_blank" rel="noreferrer">Rates</a></div></CardContent></Card>)}</div>}
    <AlertBox tone="info" className="mt-6">Only personal-loan products with published source evidence are included in the eligibility engine. Other marketplace categories require their own verified product data and decision rules.</AlertBox>
  </>;
}

function ReviewPage() {
  const { profile } = useAuth(); const client=useQueryClient(); const [decision,setDecision]=useState<{item:Review;action:"approve"|"reject";criteria?:Review["proposal"]["criteria"]}|null>(null); const [details,setDetails]=useState<Review|null>(null); const [detailsEditing,setDetailsEditing]=useState(false); const [notice,setNotice]=useState("");
  const queue=useQuery({queryKey:["reviews"],queryFn:()=>apiJson<{count:number;items:Review[]}>("/api/products/review-queue"),enabled:!!profile?.capabilities.can_review_criteria});
  const mutation=useMutation({mutationFn:({item,action,criteria}:NonNullable<typeof decision>)=>apiJson(`/api/products/${item.product_id}/criteria-review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({decision:action,proposal_sha256:item.proposal_sha256,...(action==="approve"&&criteria?{criteria}:{})})}),onSuccess:(_result,variables)=>{setNotice(variables.criteria&&JSON.stringify(variables.criteria)!==JSON.stringify(variables.item.proposal.criteria)?"Edited criteria approved and saved. The product catalog now uses the reviewer-adjusted values.":"Decision recorded. The queue and product state have been refreshed.");setDecision(null);setDetails(null);client.invalidateQueries({queryKey:["reviews"]});client.invalidateQueries({queryKey:["products"]});client.invalidateQueries({queryKey:["dashboard"]});},onError:(e)=>setNotice(e instanceof Error?e.message:"Review decision failed.")});
  if (!profile?.capabilities.can_review_criteria) return <><PageTitle eyebrow="PRODUCT GOVERNANCE" title="Criteria review" description="This area is restricted to assigned platform criteria reviewers."/><AlertBox tone="info">Your account can use this workspace, but criteria-review permission has not been assigned. Ask the platform administrator to grant reviewer access.</AlertBox></>;
  return <><PageTitle eyebrow="PRODUCT GOVERNANCE" title="Criteria review" description="Review Gemini’s source-grounded proposals. Approval activates a versioned lender rule; it does not approve any applicant." />
    {notice&&<AlertBox className="mb-5" tone={mutation.isError?"error":"success"}>{notice}</AlertBox>}
    {queue.isLoading?<LoadingBlock label="Loading review queue"/>:queue.error?<PageError error={queue.error} retry={()=>void queue.refetch()}/>:!(queue.data?.items.length)?<EmptyState icon={<ShieldCheck size={20}/>} title="Nothing is waiting for review" description="New criteria proposals appear here only after deterministic lender-page parsing needs human help."/>:<ReviewTable items={queue.data.items} busy={mutation.isPending} onDetails={(item,edit)=>{setDetails(item);setDetailsEditing(edit);}} onDecision={(item,action)=>setDecision({item,action})}/>}
    <ConfirmDialog open={!!decision} onOpenChange={(open)=>!open&&setDecision(null)} title={decision?.action==="approve"?"Approve lender criteria?":"Reject this proposal?"} description={decision?.action==="approve"?<span>Activate the criteria for <strong>{decision.item.lender}</strong>. Any reviewer edits will be saved as the active criteria. Source freshness and reviewer permissions will be re-checked.</span>:<span>Mark the proposal for <strong>{decision?.item.lender}</strong> as rejected. It will remain out of matching.</span>} confirmText={decision?.action==="approve"?"Approve criteria":"Reject proposal"} destructive={decision?.action==="reject"} busy={mutation.isPending} onConfirm={()=>decision&&mutation.mutate(decision)} />
    <ReviewDetailsDialog item={details} editing={detailsEditing} onEditingChange={setDetailsEditing} onOpenChange={(open)=>!open&&setDetails(null)} onDecision={(item,criteria)=>{setDecision({item,action:"approve",criteria});setDetails(null);}} />
  </>;
}

function criteriaRows(item:Review) {
  return Object.entries(item.proposal.criteria.segments||{}).flatMap(([segment,rules])=>Object.entries(rules).map(([field,value])=>({segment,field,value,evidence:item.proposal.evidence.find(e=>e.field===`${segment}.${field}`)||item.proposal.evidence.find(e=>e.field.endsWith(`.${field}`))})));
}

const criteriaColumns = [
  {label:"Min. age", fields:["min_age"], suffix:" yrs"},
  {label:"Max. age", fields:["max_age"], suffix:" yrs"},
  {label:"Min. credit score", fields:["min_credit_score","credit_score_preferred"], suffix:""},
  {label:"Min. monthly income", fields:["minimum_monthly_income_inr"], suffix:""},
  {label:"Work experience", fields:["total_work_experience_years"], suffix:" yrs"},
];

function criteriaFieldLabel(field:string) {
  const labels:Record<string,string>={min_age:"Minimum age",max_age:"Maximum age",min_credit_score:"Minimum credit score",max_credit_score:"Maximum credit score",credit_score_preferred:"Preferred credit score",minimum_monthly_income_inr:"Minimum monthly income (INR)",total_work_experience_years:"Total work experience (years)",current_organisation_years:"Current organisation experience (years)",salary_account_required:"Salary account required"};
  return labels[field]||field.replaceAll("_"," ").replace(/\b\w/g,character=>character.toUpperCase());
}

function criteriaColumnValue(item:Review, fields:string[], suffix:string) {
  const bySegment=Object.values(item.proposal.criteria.segments||{}).map((rules)=>{
    const field=fields.find(key=>rules[key]!==undefined);
    if(!field)return null;
    const raw=rules[field];
    const value=field==="minimum_monthly_income_inr"?`INR ${Number(raw).toLocaleString("en-IN")}`:`${String(raw)}${suffix}${field==="credit_score_preferred"?" (preferred)":""}`;
    return value;
  }).filter(Boolean) as string[];
  const unique=[...new Set(bySegment)];
  return unique.length?unique.join(" / "):"—";
}

function reviewDialogRows(item:Review,draft:Review["proposal"]["criteria"]) {
  return Object.entries(draft.segments||{}).flatMap(([segment,rules])=>{
    const standardFields=["min_age","max_age",rules.credit_score_preferred!==undefined?"credit_score_preferred":"min_credit_score","minimum_monthly_income_inr","total_work_experience_years"];
    const fields=[...standardFields,...Object.keys(rules).filter(field=>!standardFields.includes(field))];
    return fields.map(field=>({segment,field,value:rules[field],evidence:item.proposal.evidence.find(e=>e.field===`${segment}.${field}`)||item.proposal.evidence.find(e=>e.field.endsWith(`.${field}`))}));
  });
}

function ReviewTable({items,busy,onDetails,onDecision}:{items:Review[];busy:boolean;onDetails:(item:Review,edit:boolean)=>void;onDecision:(item:Review,action:"approve"|"reject")=>void}) {
  return <Card className="overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4"><div><h2 className="font-display text-sm font-bold text-navy-950">Pending criteria</h2><p className="mt-1 text-[11px] text-slate-500">Compare the published criteria fields and inspect citations before review.</p></div><Badge tone="warning">{count(items.length)} awaiting review</Badge></div><div className="overflow-x-auto"><table className="w-full min-w-[1320px] text-center"><thead><tr className="border-b border-slate-100 bg-slate-50/80 text-[10px] font-bold uppercase tracking-wider text-slate-400"><th className="px-3 py-3">Lender / product</th><th className="px-3 py-3">Product status</th><th className="px-3 py-3">Segment</th>{criteriaColumns.map(col=><th key={col.label} className="px-3 py-3">{col.label}</th>)}<th className="px-3 py-3">Source review</th><th className="px-3 py-3">Actions</th></tr></thead><tbody>{items.map(item=>{const segments=[...new Set(criteriaRows(item).map(row=>row.segment))];const unresolved=item.proposal.uncertainties;return <tr key={`${item.product_id}-${item.proposal_sha256}`} className="table-row border-b border-slate-100 last:border-0"><td className="px-3 py-3 text-center"><div className="text-xs font-bold text-navy-950">{item.lender}</div><div className="mx-auto mt-0.5 max-w-[190px] text-[10px] leading-4 text-slate-500">{item.product_name}</div></td><td className="px-3 py-3 text-center"><Badge tone={item.product_status==="stale"?"warning":item.product_status==="verified"?"success":"neutral"}>{stateLabel(item.product_status)}</Badge></td><td className="px-3 py-3 text-center"><div className="flex flex-wrap justify-center gap-1">{segments.map(segment=><span key={segment} className="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium capitalize text-slate-600">{segment.replaceAll("-"," ")}</span>)}</div></td>{criteriaColumns.map(col=><td key={col.label} className="px-3 py-3 text-center text-[11px] font-medium leading-5 text-slate-700">{criteriaColumnValue(item,col.fields,col.suffix)}</td>)}<td className="px-3 py-3 text-center"><Tooltip><TooltipTrigger asChild><div className={`inline-flex flex-col items-center py-1 ${unresolved.length?"cursor-help":""}`} tabIndex={0} role="button" aria-label={unresolved.length?`Unresolved details: ${unresolved.length} open questions`:`${count(item.proposal.evidence.length)} citations`}><div className="text-[11px] font-semibold text-slate-700">{count(item.proposal.evidence.length)} citations</div><div className={unresolved.length?"mt-0.5 text-[10px] font-medium text-amber-700 underline decoration-dotted underline-offset-2":"mt-0.5 text-[10px] text-slate-400"}>{unresolved.length?`${count(unresolved.length)} open question(s)`:"No open questions"}</div></div></TooltipTrigger><TooltipContent side="top" align="center" className={unresolved.length?"max-w-md border-amber-200 bg-amber-50/95 p-3.5 text-left text-amber-950 shadow-xl backdrop-blur-sm":"max-w-xs p-3 text-left shadow-lg"}>{unresolved.length?(<div><div className="flex items-center gap-1.5 font-bold text-xs text-amber-900"><span>Unresolved details</span><span className="rounded-full bg-amber-200/70 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">{unresolved.length}</span></div><ul className="mt-2 space-y-1.5 text-[11px] leading-relaxed text-amber-900">{unresolved.map((note,idx)=><li key={idx} className="flex items-start gap-1.5"><span className="text-amber-600 font-bold select-none">•</span><span className="font-medium">{note}</span></li>)}</ul></div>):(<div><div className="font-bold text-xs text-navy-950">Source citations verified</div><p className="mt-1 text-[11px] text-slate-500">{count(item.proposal.evidence.length)} official lender citation(s) reviewed. No open questions or uncertainties detected.</p></div>)}</TooltipContent></Tooltip></td><td className="px-3 py-3"><div className="flex items-center justify-center gap-0.5"><Button size="icon" variant="ghost" className="size-8 text-slate-500 hover:bg-blue-50 hover:text-blue-700" aria-label={`View details for ${item.product_name}`} title="View details" onClick={()=>onDetails(item,false)}><Eye size={15}/></Button><Button size="icon" variant="ghost" className="size-8 text-blue-600 hover:bg-blue-50 hover:text-blue-800" aria-label={`Edit criteria for ${item.product_name}`} title="Edit criteria" onClick={()=>onDetails(item,true)}><Pencil size={14}/></Button><Button size="icon" variant="ghost" className="size-8 text-slate-500 hover:bg-red-50 hover:text-red-600" disabled={busy} aria-label={`Reject ${item.product_name}`} title="Reject" onClick={()=>onDecision(item,"reject")}><X size={15}/></Button><Button size="icon" variant="ghost" className="size-8 text-emerald-700 hover:bg-emerald-50" disabled={busy} aria-label={`Approve ${item.product_name}`} title="Approve" onClick={()=>onDecision(item,"approve")}><Check size={16}/></Button></div></td></tr>})}</tbody></table></div><div className="border-t border-slate-100 px-5 py-2.5 text-center text-[10px] text-slate-400">Approval activates the complete criteria set. Applicant eligibility remains a lender decision.</div></Card>;
}

function ReviewDetailsDialog({item,editing,onEditingChange,onOpenChange,onDecision}:{item:Review|null;editing:boolean;onEditingChange:(editing:boolean)=>void;onOpenChange:(open:boolean)=>void;onDecision:(item:Review,criteria:Review["proposal"]["criteria"])=>void}) {
  const [draft,setDraft]=useState<Review["proposal"]["criteria"]>(item?.proposal.criteria||{});
  useEffect(()=>{setDraft(item?.proposal.criteria||{});},[item]);
  const rows=item?reviewDialogRows(item,draft):[];
  function updateValue(segment:string,field:string,raw:string) {
    setDraft(previous=>{
      const segments={...(previous.segments||{})}; const rules={...(segments[segment]||{})};
      if(raw==="") delete rules[field]; else rules[field]=field==="salary_account_required"?raw==="true":Number(raw);
      segments[segment]=rules;
      return {...previous,segments} as Review["proposal"]["criteria"];
    });
  }
  return <DialogPrimitive.Root open={!!item} onOpenChange={onOpenChange}><DialogPrimitive.Portal><DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-navy-950/40 backdrop-blur-[2px]"/><DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[calc(100%-24px)] max-w-6xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"><div className="relative border-b border-slate-100 px-5 py-5 text-center sm:px-8"><div className="text-[10px] font-bold uppercase tracking-[.16em] text-blue-600">{item?.lender} - CRITERIA REVIEW</div><DialogPrimitive.Title className="mt-1 font-display text-xl font-bold text-navy-950">{item?.product_name}</DialogPrimitive.Title><DialogPrimitive.Description className="mt-1 text-xs text-slate-500">Review each value against its cited lender source. You can adjust the criteria before approval.</DialogPrimitive.Description><DialogPrimitive.Close aria-label="Close details" className="absolute right-4 top-4 rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X size={18}/></DialogPrimitive.Close>{item&&<div className="mt-3 flex flex-wrap justify-center gap-2"><Badge className="capitalize" tone={item.product_status==="stale"?"warning":"neutral"}>Product {stateLabel(item.product_status)}</Badge><Badge tone="info">{item.proposal.model}</Badge><Badge>{count(item.proposal.usage?.totalTokenCount)} model tokens</Badge><Button size="sm" variant={editing?"default":"outline"} onClick={()=>onEditingChange(!editing)}>{editing?<Check size={14}/>:<Pencil size={14}/>} {editing?"Done editing":"Edit criteria"}</Button></div>}</div>{item&&<div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6"><section><h3 className="mb-2 text-center text-xs font-bold uppercase tracking-wide text-navy-950">Eligibility criteria and source evidence</h3><div className="overflow-x-auto rounded-xl border border-slate-200"><table className="w-full min-w-[900px] text-center"><thead><tr className="bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-400"><th className="px-3 py-3">Applicant segment</th><th className="px-3 py-3">Criteria field</th><th className="px-3 py-3">{editing?"Editable value":"Proposed value"}</th><th className="px-3 py-3">Official page evidence</th><th className="px-3 py-3">Source</th></tr></thead><tbody>{rows.map(({segment,field,value,evidence})=><tr key={`${segment}.${field}`} className="border-t border-slate-100"><td className="px-3 py-3 text-center text-[11px] font-semibold capitalize text-slate-700">{segment.replaceAll("-"," ")}</td><td className="px-3 py-3 text-center text-[11px] font-medium text-slate-600">{criteriaFieldLabel(field)}</td><td className="px-3 py-3 text-center">{editing?<input aria-label={`${segment} ${criteriaFieldLabel(field)}`} className="mx-auto h-9 w-36 rounded-lg border border-slate-200 px-3 text-center text-xs font-semibold text-navy-950 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100" type={field==="salary_account_required"?"checkbox":"number"} checked={field==="salary_account_required"?Boolean(value):undefined} value={field==="salary_account_required"?undefined:value==null?"":String(value)} onChange={event=>updateValue(segment,field,field==="salary_account_required"?String(event.target.checked):event.target.value)}/>:<span className="text-xs font-bold text-navy-950">{value==null?"Not specified":field==="minimum_monthly_income_inr"?`INR ${Number(value).toLocaleString("en-IN")}`:field==="salary_account_required"?(value?"Required":"Not required"):String(value)}{field.endsWith("_years")?" years":""}{field==="credit_score_preferred"?" (preferred)":""}</span>}</td><td className="max-w-[390px] px-3 py-3 text-center text-[11px] leading-5 text-slate-600">{evidence?`"${evidence.quote}"`:<span className="text-amber-700">No exact field citation supplied</span>}</td><td className="px-3 py-3 text-center">{evidence?<a className="whitespace-nowrap text-[10px] font-semibold text-blue-700 hover:underline" href={evidence.source_url} target="_blank" rel="noreferrer">Open source</a>:<span className="text-[10px] text-slate-400">-</span>}</td></tr>)}</tbody></table></div>{editing&&<p className="mt-2 text-center text-[10px] leading-5 text-amber-700">Manual edits override the extracted values. Confirm every adjusted value against the lender page; source quotes remain unchanged.</p>}</section>{!!item.proposal.uncertainties.length&&<section className="mx-auto max-w-3xl rounded-xl border border-amber-200 bg-amber-50 p-4 text-center"><h3 className="text-xs font-bold text-amber-900">Unresolved details</h3><ul className="mt-2 list-inside list-disc space-y-1 text-xs leading-5 text-amber-900">{item.proposal.uncertainties.map(note=><li key={note}>{note}</li>)}</ul></section>}<div className="text-center"><a className="inline-flex text-xs font-semibold text-blue-700 hover:underline" href={item.proposal.source_url} target="_blank" rel="noreferrer">Open official eligibility page</a></div><div className="flex justify-center gap-2 border-t border-slate-100 pt-4"><Button variant="outline" onClick={()=>onOpenChange(false)}>Close</Button><Button variant="success" onClick={()=>onDecision(item,draft)}><Check size={15}/>Approve {editing?"edited ":""}criteria</Button></div><p className="text-center text-[10px] leading-5 text-slate-400">Approval makes these rules available to matching; it does not determine or guarantee applicant eligibility.</p></div>}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>;
}

function OperationsPage() {
  const health=useQuery({queryKey:["health"],queryFn:()=>apiJson<Record<string,string|boolean>>("/api/operations/health"),refetchInterval:30000});
  return <><PageTitle eyebrow="SYSTEM STATUS" title="Operations" description="Service health and the information needed to diagnose a stalled or degraded workflow." actions={<Button variant="outline" onClick={()=>void health.refetch()}><RefreshCw size={14}/> Refresh</Button>} />
    {health.isLoading?<LoadingBlock/>:health.error?<PageError error={health.error} retry={()=>void health.refetch()}/>:<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{Object.entries(health.data||{}).filter(([key])=>key.endsWith("_status")).map(([key,value])=><Card className="p-5" key={key}><div className="flex items-center justify-between"><div className="font-semibold capitalize text-navy-950">{key.replace("_status","").replaceAll("_"," ")}</div><Badge tone={value==="ok"?"success":value==="not_configured"?"warning":"danger"}>{String(value).replaceAll("_"," ")}</Badge></div><div className="mt-2 text-xs leading-5 text-slate-500">{operationHelp(key)}</div></Card>)}</div>}
    <Card className="mt-6"><CardHeader><div><p className="section-kicker">RUNTIME NOTES</p><h2 className="card-title">What this page reports</h2></div><Gauge size={18} className="text-blue-600"/></CardHeader><CardContent><p className="max-w-3xl text-xs leading-6 text-slate-500">Matching job state, attempts, product criteria versions, and notification counts are stored in PostgreSQL. n8n coordinates the workflows. This page shows service reachability only; a green service badge does not guarantee every previous run completed successfully. Open the run detail for per-batch results.</p></CardContent></Card>
  </>;
}
function operationHelp(key:string){if(key.includes("database"))return "The API can execute a database health query.";if(key.includes("firebase"))return "Firebase project settings and Admin SDK credentials used for secure sessions.";if(key.includes("n8n"))return "Workflow automation endpoint reachable from the API container.";if(key.includes("mailpit"))return "Configured email capture service used for test notifications.";return "API health check.";}

function TeamPage() {
  const { profile }=useAuth();
  const client=useQueryClient();const [email,setEmail]=useState("");const [role,setRole]=useState("analyst");const [reviewerNotice,setReviewerNotice]=useState("");
  const query=useQuery({queryKey:["members"],queryFn:()=>apiJson<{members:{user_id:number;email:string;display_name:string|null;role:string;platform_role:string|null;created_at:string}[];invites:{email:string;role:string;created_at:string}[]}>("/api/workspaces/members")});
  const invite=useMutation({mutationFn:()=>apiJson("/api/workspaces/invites",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,role})}),onSuccess:()=>{setEmail("");client.invalidateQueries({queryKey:["members"]});}});
  const reviewer=useMutation({mutationFn:({userId,enabled}:{userId:number;enabled:boolean})=>apiJson(`/api/platform/reviewers/${userId}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled})}),onSuccess:(_data,variables)=>{setReviewerNotice(variables.enabled?"Criteria reviewer access granted.":"Criteria reviewer access removed.");client.invalidateQueries({queryKey:["members"]});client.invalidateQueries({queryKey:["reviews"]});},onError:(error)=>setReviewerNotice(error instanceof Error?error.message:"Could not update reviewer access.")});
  return <><PageTitle eyebrow="WORKSPACE ACCESS" title="Team" description="Invite verified Firebase accounts and assign workspace-level permissions." />
    <Card className="mb-5"><CardHeader><div><p className="section-kicker">INVITE MEMBER</p><h2 className="card-title">Add a teammate</h2></div><Users size={18} className="text-blue-600"/></CardHeader><CardContent><form className="grid gap-3 sm:grid-cols-[1fr_160px_auto]" onSubmit={(e)=>{e.preventDefault();invite.mutate();}}><Input type="email" required value={email} onChange={(e)=>setEmail(e.target.value)} placeholder="name@company.com"/><select className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" value={role} onChange={(e)=>setRole(e.target.value)}><option value="analyst">Analyst</option><option value="viewer">Viewer</option></select><Button disabled={invite.isPending||!email}>{invite.isPending?"Inviting…":"Create invite"}</Button></form>{invite.error&&<AlertBox className="mt-3">{invite.error.message}</AlertBox>}{invite.isSuccess&&<AlertBox className="mt-3" tone="success">Invite created. The user can sign in using this verified Firebase email.</AlertBox>}</CardContent></Card>
    {reviewerNotice&&<AlertBox className="mb-4" tone={reviewer.isError?"error":"success"}>{reviewerNotice}</AlertBox>}{query.isLoading?<LoadingBlock/>:query.error?<PageError error={query.error} retry={()=>void query.refetch()}/>:<Card><CardHeader><div><p className="section-kicker">ACCESS</p><h2 className="card-title">Workspace members</h2></div></CardHeader><CardContent><div className="divide-y divide-slate-100">{query.data?.members.map(m=><div className="flex flex-wrap items-center gap-3 py-3" key={m.user_id}><div className="grid size-9 place-items-center rounded-full bg-blue-50 text-xs font-bold text-blue-700">{(m.display_name||m.email).slice(0,1).toUpperCase()}</div><div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold text-navy-950">{m.display_name||m.email}</div><div className="truncate text-[10px] text-slate-400">{m.email}</div></div><Badge>{m.role.replaceAll("_"," ")}</Badge>{m.platform_role&&<Badge tone={m.platform_role==="platform_admin"?"info":"success"}>{m.platform_role.replaceAll("_"," ")}</Badge>}{profile?.platform_role==="platform_admin"&&m.platform_role!=="platform_admin"&&<Button size="sm" variant="outline" disabled={reviewer.isPending} onClick={()=>reviewer.mutate({userId:m.user_id,enabled:m.platform_role!=="criteria_reviewer"})}>{m.platform_role==="criteria_reviewer"?"Remove reviewer":"Grant reviewer"}</Button>}</div>)}{query.data?.invites.map(inv=><div className="flex items-center gap-3 py-3" key={inv.email}><div className="grid size-9 place-items-center rounded-full bg-amber-50 text-amber-700"><Clock3 size={16}/></div><div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold text-navy-950">{inv.email}</div><div className="text-[10px] text-slate-400">Invitation awaiting sign-in · {date(inv.created_at)}</div></div><Badge tone="warning">{inv.role}</Badge></div>)}</div></CardContent></Card>}
  </>;
}

function PageError({error,retry}:{error:unknown;retry?:()=>void}){const e=error instanceof Error?error.message:"Unexpected error";return <div className="space-y-4"><AlertBox>{e}</AlertBox>{retry&&<Button variant="outline" onClick={retry}><RefreshCw size={14}/> Try again</Button>}</div>;}

export default App;
