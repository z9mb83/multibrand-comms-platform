"use client";

import { CheckCircle2, FileUp, Octagon, Play, Plus, RefreshCw, Sparkles, Star, Trash2 } from "lucide-react";
import { Fragment, useCallback, useEffect, useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand, Stat } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Settings = {
  enabled: boolean; emergency_stop: boolean; default_hourly_limit: number; default_daily_limit: number; minimum_gap_minutes: number;
  cc_email: string; sending_start_time: string; sending_end_time: string; brand_facts: string; website: string; phone: string;
  signature_name: string; signature_title: string; run_order: number;
};
type Global = {
  cross_brand_duplicate_days: number; max_drafts_per_brand_per_run: number; openai_model: string; reports_enabled: boolean;
  report_recipient: string; report_sender: string; last_run_at: string | null; last_run_summary: string;
  openai_configured: boolean; openai_key_hint: string; openai_key_from_server: boolean; live_sending: boolean;
};
type Overview = { settings: Settings; counts: Record<string, number>; approved_ready: number; sent_today: number;
  within_hours: boolean; local_time: string; role: string; global: Global };
type Lead = { id: number; person_name: string; email: string; details: string; subject: string; body: string; status: string;
  sent_at: string | null; error: string; sent_from: string; approval: string; follow_up_status: string };
type Mailbox = { sender_id: number; email: string; is_master: boolean; provider: string; connected: boolean; enabled: boolean; stage: string;
  daily_limit: number; hourly_limit: number; custom_limits: boolean; sent_today: number; sent_this_hour: number; clean_days: number;
  last_sent_at: string | null; next_eligible_at: string | null; paused: boolean; pause_reason: string; last_result: string; ready: string };
type LogRow = { id: number; logged_at: string; sender_email: string; lead_email: string; subject: string; gmail_message_id: string;
  result: string; error: string; daily_limit: number | null; stage: string; imported: boolean };
type RunResult = { brand: string; generated: number; sent: boolean; skipped: string; notes: string[]; errors: string[] };

const RANK: Record<string, number> = { viewer: 1, agent: 2, manager: 3, brand_admin: 4 };
const STATUS = ["Pending", "Generated", "Approved", "Scheduled", "Sent", "Skipped", "Failed", "Replied", "Unsubscribed", "Bounced"];
const TONE: Record<string, "green" | "red" | "amber" | "blue" | "gray"> = {
  Sent: "green", Replied: "green", Generated: "blue", Approved: "blue", Scheduled: "amber", Pending: "gray",
  Skipped: "gray", Failed: "red", Unsubscribed: "red", Bounced: "red", Sending: "amber",
};
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const TABS = ["Leads", "Mailboxes", "Send log", "Suppression", "Settings", "Automation"] as const;
type Tab = (typeof TABS)[number];

function useRunner() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }, []);
  return { busy, err, run, setErr };
}

function RunNote({ r }: { r: RunResult | null }) {
  if (!r) return null;
  return (
    <div className="mb-4 rounded-md border bg-muted/40 px-3 py-2 text-sm">
      <span className="font-medium">{r.brand}:</span>{" "}
      {r.sent ? "1 email sent." : r.skipped || "Nothing to do."}
      {r.generated > 0 && ` ${r.generated} draft(s) written.`}
      {[...r.notes, ...r.errors].map((n, i) => <div key={i} className="text-xs text-muted-foreground">{n}</div>)}
    </div>
  );
}

// ------------------------------------------------------------------ leads
function LeadEditor({ base, lead, canApprove, onSaved }: { base: string; lead: Lead; canApprove: boolean; onSaved: () => void }) {
  const [subject, setSubject] = useState(lead.subject);
  const [body, setBody] = useState(lead.body);
  const [follow, setFollow] = useState(lead.follow_up_status);
  const [status, setStatus] = useState(lead.status);
  const { busy, err, run } = useRunner();
  const sent = lead.status === "Sent";
  return (
    <div className="grid gap-3 bg-muted/30 p-4">
      {lead.details && <p className="text-xs text-muted-foreground">Details: {lead.details}</p>}
      <Field label="Subject"><Input value={subject} disabled={sent} onChange={(e) => setSubject(e.target.value)} /></Field>
      <Field label="Email body (signature included)"><Textarea value={body} disabled={sent} rows={10} onChange={(e) => setBody(e.target.value)} /></Field>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Status"><Select value={status} onChange={(e) => setStatus(e.target.value)}>{STATUS.map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Follow-up"><Select value={follow} onChange={(e) => setFollow(e.target.value)}>
          <option value="">—</option>{["Pending", "Replied", "Stopped", "Completed"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Button size="sm" disabled={busy} onClick={() => run(async () => {
          await api(`${base}/leads/${lead.id}`, { method: "PATCH", json: sent ? { status, follow_up_status: follow } : { subject, body, status, follow_up_status: follow } });
          onSaved();
        })}>Save</Button>
        {!sent && canApprove && (
          <Button size="sm" variant="outline" disabled={busy || !subject.trim() || !body.trim()} onClick={() => run(async () => {
            await api(`${base}/leads/${lead.id}`, { method: "PATCH", json: { subject, body, status: ["Generated", "Approved", "Scheduled"].includes(status) ? status : "Generated", approval: "Yes" } });
            onSaved();
          })}><CheckCircle2 className="h-4 w-4" />Save & approve</Button>
        )}
        {canApprove && (
          <Button size="sm" variant="ghost" className="ml-auto text-destructive" disabled={busy}
            onClick={() => run(async () => { await api(`${base}/leads/${lead.id}`, { method: "DELETE" }); onSaved(); })}>Delete lead</Button>
        )}
      </div>
      <ErrorNote error={err} />
    </div>
  );
}

function LeadsTab({ base, role, refreshOverview }: { base: string; role: string; refreshOverview: () => void }) {
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [nl, setNl] = useState({ person_name: "", email: "", details: "" });
  const [note, setNote] = useState<RunResult | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [allMatching, setAllMatching] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const PAGE = 100;
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  if (filter) params.set("status", filter);
  if (q) params.set("q", q);
  const { data, error, reload } = usePoll<{ total: number; items: Lead[] }>(`${base}/leads?${params}`, 20000);
  const { busy, err, run } = useRunner();
  const canEdit = RANK[role] >= 2;
  const canApprove = RANK[role] >= 3;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const clearSelection = () => { setSelected(new Set()); setAllMatching(false); setConfirmDelete(false); };
  const refresh = () => { reload(); refreshOverview(); };
  useEffect(() => { clearSelection(); setPage(0); }, [filter, q]);
  const toggle = (id: number) => {
    setAllMatching(false); setConfirmDelete(false);
    setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };
  const pageAllSelected = items.length > 0 && items.every((l) => selected.has(l.id));
  const togglePage = () => {
    setAllMatching(false); setConfirmDelete(false);
    setSelected((s) => { const n = new Set(s); items.forEach((l) => (pageAllSelected ? n.delete(l.id) : n.add(l.id))); return n; });
  };
  const selCount = allMatching ? total : selected.size;
  const doDelete = () => run(async () => {
    const r = await api<{ deleted: number }>(`${base}/leads/delete`, { method: "POST",
      json: allMatching ? { all_matching: true, status: filter || null, q: q || null } : { ids: [...selected] } });
    setInfo(`Deleted ${r.deleted} lead${r.deleted === 1 ? "" : "s"}. The Send Log is kept, so anyone already emailed won't be emailed again.`);
    clearSelection(); setPage(0); refresh();
  });
  const deleteOne = (id: number) => run(async () => {
    await api(`${base}/leads/${id}`, { method: "DELETE" });
    setSelected((s) => { const n = new Set(s); n.delete(id); return n; });
    setInfo("Lead deleted."); refresh();
  });

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select className="w-44" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All leads</option>
          <option value="Needs approval">Needs approval</option>
          <option value="Approved-ready">Approved, waiting to send</option>
          {STATUS.map((s) => <option key={s}>{s}</option>)}
        </Select>
        <Input className="w-56" placeholder="Search name, email, subject" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="ml-auto flex flex-wrap gap-2">
          {canEdit && <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)}><Plus className="h-4 w-4" />Add lead</Button>}
          {canApprove && <Button size="sm" variant="outline" onClick={() => setImporting((v) => !v)}><FileUp className="h-4 w-4" />Import from Sheet</Button>}
          {canApprove && <Button size="sm" variant="outline" disabled={busy} onClick={() => run(async () => {
            setNote(await api<RunResult>(`${base}/generate`, { method: "POST" })); refresh();
          })}><Sparkles className="h-4 w-4" />{busy ? "Working…" : "Write drafts now"}</Button>}
          {canApprove && <Button size="sm" disabled={!selected.size || allMatching || busy} onClick={() => run(async () => {
            await api(`${base}/leads/approve`, { method: "POST", json: { ids: [...selected], approval: "Yes" } });
            clearSelection(); refresh();
          })}><CheckCircle2 className="h-4 w-4" />Approve {selected.size && !allMatching ? selected.size : ""}</Button>}
          {canApprove && (confirmDelete ? (
            <>
              <Button size="sm" variant="destructive" disabled={busy} onClick={doDelete}><Trash2 className="h-4 w-4" />Yes, delete {selCount.toLocaleString()}</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            </>
          ) : (
            <Button size="sm" variant="outline" className="text-destructive" disabled={!selCount || busy} onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" />Delete {selCount ? selCount.toLocaleString() : ""}</Button>
          ))}
        </div>
      </div>
      {canApprove && pageAllSelected && total > items.length && (
        <div className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm">
          {allMatching ? (
            <>All <b>{total.toLocaleString()}</b> {filter || q ? "matching " : ""}leads are selected.{" "}
              <button className="text-primary hover:underline" onClick={clearSelection}>Clear selection</button></>
          ) : (
            <>All {items.length} leads on this page are selected.{" "}
              <button className="font-medium text-primary hover:underline" onClick={() => { setAllMatching(true); setConfirmDelete(false); }}>
                Select all {total.toLocaleString()} {filter || q ? "matching " : ""}leads</button></>
          )}
        </div>
      )}
      <RunNote r={note} />
      {info && <p className="mb-3 text-sm text-emerald-600">{info}</p>}
      <ErrorNote error={err || error} />

      {adding && (
        <Card className="mb-4"><CardContent className="grid gap-3 pt-6 md:grid-cols-[1fr_1fr_2fr_auto] md:items-end">
          <Field label="Person name"><Input value={nl.person_name} onChange={(e) => setNl({ ...nl, person_name: e.target.value })} /></Field>
          <Field label="Email"><Input type="email" value={nl.email} onChange={(e) => setNl({ ...nl, email: e.target.value })} /></Field>
          <Field label="Details (optional, used by the AI)"><Input value={nl.details} onChange={(e) => setNl({ ...nl, details: e.target.value })} /></Field>
          <Button disabled={busy || !nl.email} onClick={() => run(async () => {
            await api(`${base}/leads`, { method: "POST", json: nl }); setNl({ person_name: "", email: "", details: "" }); refresh();
          })}>Add</Button>
        </CardContent></Card>
      )}
      {importing && <ImportCard base={base} onDone={(m) => { setInfo(m); refresh(); }} />}

      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR>
              <TH className="w-8">{canApprove && items.length > 0 && (
                <input type="checkbox" checked={pageAllSelected} onChange={togglePage} aria-label="Select all on this page" />)}</TH>
              <TH>#</TH><TH>Lead</TH><TH>Status</TH><TH>Approval</TH><TH>Subject</TH><TH>Sent</TH><TH>Note</TH><TH className="w-8" />
            </TR></THead>
            <TBody>
              {items.map((l) => (
                <Fragment key={l.id}>
                  <TR className="cursor-pointer" onClick={() => setOpen(open === l.id ? null : l.id)}>
                    <TD onClick={(e) => e.stopPropagation()}>
                      {canApprove && <input type="checkbox" checked={allMatching || selected.has(l.id)} onChange={() => toggle(l.id)} aria-label="Select lead" />}
                    </TD>
                    <TD className="text-xs text-muted-foreground">{l.id}</TD>
                    <TD><div className="font-medium">{l.person_name || "—"}</div><div className="text-xs text-muted-foreground">{l.email}</div></TD>
                    <TD><Badge tone={TONE[l.status] ?? "gray"}>{l.status}</Badge>{l.follow_up_status && <div className="mt-1 text-xs text-muted-foreground">Follow-up: {l.follow_up_status}</div>}</TD>
                    <TD onClick={(e) => e.stopPropagation()}>
                      {canApprove && l.status !== "Sent" ? (
                        <button className={cn("rounded px-2 py-0.5 text-xs font-medium", l.approval === "Yes" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "border text-muted-foreground")}
                          disabled={!l.subject || !l.body}
                          title={!l.subject || !l.body ? "Needs a subject and body first" : ""}
                          onClick={() => run(async () => { await api(`${base}/leads/approve`, { method: "POST", json: { ids: [l.id], approval: l.approval === "Yes" ? "" : "Yes" } }); refresh(); })}>
                          {l.approval === "Yes" ? "Yes" : "Approve"}
                        </button>
                      ) : <span className="text-xs">{l.approval || "—"}</span>}
                    </TD>
                    <TD className="max-w-[260px] truncate">{l.subject || <span className="text-muted-foreground">—</span>}</TD>
                    <TD className="whitespace-nowrap text-xs">{l.sent_at ? <>{fmt(l.sent_at)}<div className="text-muted-foreground">{l.sent_from}</div></> : "—"}</TD>
                    <TD className="max-w-[240px] truncate text-xs text-muted-foreground" title={l.error}>{l.error}</TD>
                    <TD onClick={(e) => e.stopPropagation()}>
                      {canApprove && <button className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" title="Delete lead"
                        aria-label={`Delete ${l.email}`} disabled={busy} onClick={() => deleteOne(l.id)}><Trash2 className="h-4 w-4" /></button>}
                    </TD>
                  </TR>
                  {open === l.id && (
                    <tr><td colSpan={9}>
                      <LeadEditor base={base} lead={l} canApprove={canApprove} onSaved={() => { setOpen(null); refresh(); }} />
                    </td></tr>
                  )}
                </Fragment>
              ))}
              {!items.length && <TR><TD colSpan={9} className="py-8 text-center text-sm text-muted-foreground">No leads yet. Add one or import your Sheet&apos;s lead tab.</TD></TR>}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {total > PAGE && (
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{(page * PAGE + 1).toLocaleString()}–{(page * PAGE + items.length).toLocaleString()} of {total.toLocaleString()}</span>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      )}
    </>
  );
}

function ImportCard({ base, onDone, defaultKind = "leads" }: { base: string; onDone: (msg: string) => void; defaultKind?: string }) {
  const [kind, setKind] = useState(defaultKind);
  const { busy, err, run } = useRunner();
  const help: Record<string, string> = {
    leads: "The brand's lead tab (Person Name, Email, Details, Subject, Generated Email, Send Status, Approval…). Leads already here are skipped.",
    send_log: "The Send Log tab. Import this so people who were already emailed are never emailed again.",
    suppression: "The Suppression tab (email in the first column).",
  };
  return (
    <Card className="mb-4">
      <CardHeader><CardTitle className="text-base">Import from your Google Sheet</CardTitle>
        <CardDescription>In Google Sheets open the tab, then File → Download → CSV (or .xlsx), and upload it here.</CardDescription></CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-[200px_1fr] md:items-end">
        <Field label="Which tab"><Select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="leads">Lead tab</option><option value="send_log">Send Log</option><option value="suppression">Suppression</option></Select></Field>
        <Field label={help[kind]}>
          <Input type="file" accept=".csv,.xlsx" disabled={busy} onChange={(e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            run(async () => {
              const fd = new FormData(); fd.append("kind", kind); fd.append("file", f);
              const r = await api<{ added: number; skipped: number; problems: string[] }>(`${base}/import`, { method: "POST", body: fd });
              onDone(`Imported ${r.added}, skipped ${r.skipped}${r.problems.length ? ` (${r.problems.join("; ")})` : ""}.`);
            });
            e.target.value = "";
          }} />
        </Field>
        <div className="md:col-span-2"><ErrorNote error={err} /></div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ mailboxes
function MailboxRow({ base, m, canEdit, onSaved }: { base: string; m: Mailbox; canEdit: boolean; onSaved: () => void }) {
  const [d, setD] = useState({ daily: m.custom_limits ? String(m.daily_limit) : "", hourly: m.custom_limits ? String(m.hourly_limit) : "", stage: m.stage, reason: m.pause_reason });
  const { busy, err, run } = useRunner();
  const patch = (json: object) => run(async () => { await api(`${base}/mailboxes/${m.sender_id}`, { method: "PATCH", json }); onSaved(); });
  return (
    <TR>
      <TD><div className="flex items-center gap-1 font-medium">{m.is_master && <Star className="h-3 w-3 text-amber-500" />}{m.email}</div>
        <div className="text-xs text-muted-foreground">{m.provider === "mock" ? "simulated" : m.connected ? "connected" : "not connected"}</div>
        <ErrorNote error={err} /></TD>
      <TD><Badge tone={m.ready === "Ready" ? "green" : m.ready === "Not connected" || m.ready === "Paused" || m.ready === "Disabled" ? "red" : "amber"}>{m.ready}</Badge></TD>
      <TD className="tabular-nums">{m.sent_today}/{m.daily_limit}<div className="text-xs text-muted-foreground">{m.sent_this_hour}/{m.hourly_limit} this hour</div></TD>
      <TD>
        {canEdit ? (
          <div className="flex gap-1">
            <Input className="h-8 w-16" placeholder="day" value={d.daily} onChange={(e) => setD({ ...d, daily: e.target.value })} />
            <Input className="h-8 w-16" placeholder="hour" value={d.hourly} onChange={(e) => setD({ ...d, hourly: e.target.value })} />
          </div>
        ) : m.custom_limits ? "custom" : "brand default"}
      </TD>
      <TD>{canEdit ? <Input className="h-8 w-24" value={d.stage} onChange={(e) => setD({ ...d, stage: e.target.value })} /> : m.stage}</TD>
      <TD className="whitespace-nowrap text-xs">{fmt(m.last_sent_at)}<div className="text-muted-foreground">next {fmt(m.next_eligible_at)}</div></TD>
      <TD className="max-w-[200px] truncate text-xs text-muted-foreground" title={m.last_result}>{m.last_result || "—"}</TD>
      {canEdit && (
        <TD className="whitespace-nowrap text-right">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => patch(
            d.daily === "" && d.hourly === "" ? { clear_limits: true, stage: d.stage } :
              { daily_limit: d.daily === "" ? m.daily_limit : Number(d.daily), hourly_limit: d.hourly === "" ? m.hourly_limit : Number(d.hourly), stage: d.stage })}>Save</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch({ paused: !m.paused, pause_reason: m.paused ? "" : "Paused by admin" })}>{m.paused ? "Resume" : "Pause"}</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch({ enabled: !m.enabled })}>{m.enabled ? "Disable" : "Enable"}</Button>
        </TD>
      )}
    </TR>
  );
}

function MailboxesTab({ base, role }: { base: string; role: string }) {
  const { data, error, reload } = usePoll<Mailbox[]>(`${base}/mailboxes`, 30000);
  const canEdit = RANK[role] >= 4;
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        Each run sends from the least-used ready mailbox (sent today ÷ daily limit, then the one that sent longest ago).
        Leave the limits blank to use the brand defaults from Settings. Add or connect mailboxes on Brands &amp; Mailboxes.
      </p>
      <ErrorNote error={error} />
      <Card><CardContent className="p-0">
        <Table>
          <THead><TR><TH>Mailbox</TH><TH>Now</TH><TH>Sent</TH><TH>Limits (day / hour)</TH><TH>Warm-up stage</TH><TH>Last sent</TH><TH>Last result</TH>{canEdit && <TH />}</TR></THead>
          <TBody>
            {(data ?? []).map((m) => <MailboxRow key={`${m.sender_id}-${m.daily_limit}-${m.hourly_limit}-${m.stage}`} base={base} m={m} canEdit={canEdit} onSaved={reload} />)}
            {data && !data.length && <TR><TD colSpan={8} className="py-8 text-center text-sm text-muted-foreground">This brand has no email mailboxes yet.</TD></TR>}
          </TBody>
        </Table>
      </CardContent></Card>
    </>
  );
}

// ------------------------------------------------------------------ send log / suppression
function SendLogTab({ base }: { base: string }) {
  const { data, error } = usePoll<{ total: number; items: LogRow[] }>(`${base}/send-log?limit=500`, 30000);
  return (
    <>
      <ErrorNote error={error} />
      <Card><CardContent className="p-0">
        <Table>
          <THead><TR><TH>Date and time</TH><TH>Sender</TH><TH>Lead email</TH><TH>Subject</TH><TH>Result</TH><TH>Error</TH><TH>Limit / stage</TH></TR></THead>
          <TBody>
            {(data?.items ?? []).map((r) => (
              <TR key={r.id}>
                <TD className="whitespace-nowrap text-xs">{fmt(r.logged_at)}{r.imported && <div className="text-muted-foreground">from Sheet</div>}</TD>
                <TD className="text-xs">{r.sender_email}</TD><TD>{r.lead_email}</TD>
                <TD className="max-w-[240px] truncate">{r.subject}</TD>
                <TD><Badge tone={TONE[r.result] ?? "gray"}>{r.result === "Sending" ? "Uncertain" : r.result}</Badge></TD>
                <TD className="max-w-[240px] truncate text-xs text-muted-foreground" title={r.error}>{r.result === "Sending" ? "Check the Sent folder before resending" : r.error}</TD>
                <TD className="text-xs">{r.daily_limit ?? "—"} · {r.stage || "—"}</TD>
              </TR>
            ))}
            {data && !data.items.length && <TR><TD colSpan={7} className="py-8 text-center text-sm text-muted-foreground">Nothing sent yet.</TD></TR>}
          </TBody>
        </Table>
      </CardContent></Card>
    </>
  );
}

function SuppressionTab({ base, role }: { base: string; role: string }) {
  const { data, error, reload } = usePoll<{ id: number; email: string; reason: string; created_at: string }[]>(`${base}/suppression`, 0);
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const [info, setInfo] = useState<string | null>(null);
  const { busy, err, run } = useRunner();
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">These addresses are never emailed by this brand. A lead on this list is marked Unsubscribed instead of sent.</p>
      {RANK[role] >= 2 && (
        <div className="mb-4 flex flex-wrap items-end gap-2">
          <Field label="Email"><Input className="w-64" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Reason"><Input className="w-64" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <Button disabled={busy || !email} onClick={() => run(async () => {
            await api(`${base}/suppression`, { method: "POST", json: { email, reason } }); setEmail(""); setReason(""); reload();
          })}>Add</Button>
        </div>
      )}
      {RANK[role] >= 3 && <ImportCard base={base} defaultKind="suppression" onDone={(m) => { setInfo(m); reload(); }} />}
      {info && <p className="mb-3 text-sm text-emerald-600">{info}</p>}
      <ErrorNote error={err || error} />
      <Card><CardContent className="p-0">
        <Table>
          <THead><TR><TH>Email</TH><TH>Reason</TH><TH>Added</TH><TH /></TR></THead>
          <TBody>
            {(data ?? []).map((r) => (
              <TR key={r.id}><TD>{r.email}</TD><TD className="text-xs">{r.reason}</TD><TD className="text-xs">{fmt(r.created_at)}</TD>
                <TD className="text-right">{RANK[role] >= 4 && <Button size="sm" variant="ghost" onClick={() => run(async () => { await api(`${base}/suppression/${r.id}`, { method: "DELETE" }); reload(); })}>Remove</Button>}</TD></TR>
            ))}
          </TBody>
        </Table>
      </CardContent></Card>
    </>
  );
}

// ------------------------------------------------------------------ settings
function SettingsTab({ base, settings, role, onSaved }: { base: string; settings: Settings; role: string; onSaved: () => void }) {
  const [s, setS] = useState(settings);
  const [msg, setMsg] = useState<string | null>(null);
  const { busy, err, run } = useRunner();
  useEffect(() => setS(settings), [settings]);
  const canEdit = RANK[role] >= 4;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const num = (k: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value) as never);
  return (
    <form onSubmit={(e) => { e.preventDefault(); run(async () => { await api(`${base}/settings`, { method: "PUT", json: s }); setMsg("Saved"); onSaved(); }); }}
      className="grid gap-4">
      <Card><CardHeader><CardTitle className="text-base">Sending rules</CardTitle>
        <CardDescription>Same meaning as the settings block at the top of the brand&apos;s Email list tab.</CardDescription></CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <Field label="Default daily limit (per mailbox)"><Input type="number" min={1} disabled={!canEdit} value={s.default_daily_limit} onChange={num("default_daily_limit")} /></Field>
          <Field label="Default hourly limit (per mailbox)"><Input type="number" min={1} disabled={!canEdit} value={s.default_hourly_limit} onChange={num("default_hourly_limit")} /></Field>
          <Field label="Minimum gap between sends (minutes)"><Input type="number" min={0} disabled={!canEdit} value={s.minimum_gap_minutes} onChange={num("minimum_gap_minutes")} /></Field>
          <Field label="Sending start time"><Input type="time" disabled={!canEdit} value={s.sending_start_time} onChange={(e) => set("sending_start_time", e.target.value)} /></Field>
          <Field label="Sending end time"><Input type="time" disabled={!canEdit} value={s.sending_end_time} onChange={(e) => set("sending_end_time", e.target.value)} /></Field>
          <Field label="CC on every email"><Input type="email" disabled={!canEdit} value={s.cc_email} onChange={(e) => set("cc_email", e.target.value)} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!canEdit} checked={s.enabled} onChange={(e) => set("enabled", e.target.checked)} />Brand included in automatic runs</label>
          <Field label="Run order (lower goes first)"><Input type="number" disabled={!canEdit} value={s.run_order} onChange={num("run_order")} /></Field>
        </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">AI drafts and signature</CardTitle>
        <CardDescription>The AI may only use these approved facts plus the lead&apos;s name and details. The signature is added after the AI text, exactly as written.</CardDescription></CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <Field label="Approved brand facts" className="md:col-span-2"><Textarea disabled={!canEdit} value={s.brand_facts} onChange={(e) => set("brand_facts", e.target.value)}
            placeholder="Property Experts Group helps buyers, sellers and investors with property discovery, project guidance…" /></Field>
          <Field label="Signature name"><Input disabled={!canEdit} value={s.signature_name} onChange={(e) => set("signature_name", e.target.value)} /></Field>
          <Field label="Signature title"><Input disabled={!canEdit} value={s.signature_title} onChange={(e) => set("signature_title", e.target.value)} /></Field>
          <Field label="Phone"><Input disabled={!canEdit} value={s.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
          <Field label="Website"><Input disabled={!canEdit} value={s.website} onChange={(e) => set("website", e.target.value)} /></Field>
        </CardContent></Card>
      <ErrorNote error={err} />
      {canEdit && <div className="flex items-center gap-3"><Button disabled={busy}>Save settings</Button>{msg && <span className="text-sm text-emerald-600">{msg}</span>}</div>}
    </form>
  );
}

// ------------------------------------------------------------------ automation (Super Admin)
function OpenAIKeyCard({ global, onSaved }: { global: Global; onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const { busy, err, run } = useRunner();
  const save = (value: string | null) => run(async () => {
    await api("/outreach/openai-key", { method: "PUT", json: { key: value } });
    setKey(""); setMsg(value ? "Key saved. Drafts will be written on the next run, or click Write drafts now." : "Key removed."); onSaved();
  });
  return (
    <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base">AI email writing (OpenAI)
      <Badge tone={global.openai_configured ? "green" : "amber"}>{global.openai_configured ? "On" : "Needs a key"}</Badge></CardTitle>
      <CardDescription>
        Drafts are written with the same prompt as your Sheet automation: only the lead&apos;s name and details plus each brand&apos;s approved facts,
        80–130 words, one call to action, the opt-out line, then the brand&apos;s fixed signature.
        {global.openai_key_from_server ? " A key is set on the server." : global.openai_key_hint ? ` Saved key ends in …${global.openai_key_hint}.` : ""}
      </CardDescription></CardHeader>
      <CardContent className="flex flex-wrap items-end gap-2">
        {!global.openai_key_from_server && (
          <>
            <Field label="OpenAI API key (stored encrypted, never shown again)">
              <Input className="w-96" type="password" autoComplete="off" placeholder="sk-…" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
            <Button disabled={busy || !key.trim()} onClick={() => save(key.trim())}>Save key</Button>
            {global.openai_key_hint && <Button variant="ghost" disabled={busy} onClick={() => save(null)}>Remove key</Button>}
          </>
        )}
        {msg && <span className="text-sm text-emerald-600">{msg}</span>}
        <div className="w-full"><ErrorNote error={err} /></div>
      </CardContent></Card>
  );
}

function AutomationTab({ isSuper, global, onSaved }: { isSuper: boolean; global: Global; onSaved: () => void }) {
  const [g, setG] = useState(global);
  const [slot, setSlot] = useState<"morning" | "evening">("evening");
  const [preview, setPreview] = useState<{ subject: string; body: string; to: string; from: string } | null>(null);
  const [results, setResults] = useState<RunResult[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { busy, err, run } = useRunner();
  const history = usePoll<{ id: number; slot: string; report_date: string; status: string; subject: string; created_at: string }[]>(isSuper ? "/outreach/report-history" : null, 0);
  useEffect(() => setG(global), [global]);
  if (!isSuper) return <p className="text-sm text-muted-foreground">Only the Super Admin can change automation-wide settings.</p>;
  return (
    <div className="grid gap-4">
      <Card><CardHeader><CardTitle className="text-base">Automatic runs</CardTitle>
        <CardDescription>
          Every 15 minutes between 09:30 and 18:30 India time, each brand gets one pass (starting brand rotates): drafts are written, then at most
          one approved email is sent per brand. Last run: {global.last_run_at ? fmt(global.last_run_at) : "never"}.
        </CardDescription></CardHeader>
        <CardContent className="grid gap-3">
          {global.last_run_summary && <p className="text-xs text-muted-foreground">{global.last_run_summary}</p>}
          <div><Button disabled={busy} onClick={() => run(async () => { setResults(await api<RunResult[]>("/outreach/run", { method: "POST" })); onSaved(); })}>
            <Play className="h-4 w-4" />{busy ? "Running…" : "Run all brands now"}</Button></div>
          {results?.map((r, i) => <RunNote key={i} r={r} />)}
        </CardContent></Card>
      <OpenAIKeyCard global={global} onSaved={onSaved} />
      <Card><CardHeader><CardTitle className="text-base">Settings for all brands</CardTitle></CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <Field label="Cross-brand duplicate window (days, 0 = off)"><Input type="number" min={0} value={g.cross_brand_duplicate_days} onChange={(e) => setG({ ...g, cross_brand_duplicate_days: Number(e.target.value) })} /></Field>
          <Field label="Max AI drafts per brand per run"><Input type="number" min={0} value={g.max_drafts_per_brand_per_run} onChange={(e) => setG({ ...g, max_drafts_per_brand_per_run: Number(e.target.value) })} /></Field>
          <Field label="OpenAI model"><Input value={g.openai_model} onChange={(e) => setG({ ...g, openai_model: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={g.reports_enabled} onChange={(e) => setG({ ...g, reports_enabled: e.target.checked })} />Email the 9 AM and 6 PM reports</label>
          <Field label="Report goes to"><Input type="email" value={g.report_recipient} onChange={(e) => setG({ ...g, report_recipient: e.target.value })} /></Field>
          <Field label="Report sent from (a connected mailbox)"><Input type="email" value={g.report_sender} onChange={(e) => setG({ ...g, report_sender: e.target.value })} /></Field>
          <div className="md:col-span-3 flex items-center gap-3">
            <Button disabled={busy} onClick={() => run(async () => {
              await api("/outreach/settings", { method: "PUT", json: {
                cross_brand_duplicate_days: g.cross_brand_duplicate_days, max_drafts_per_brand_per_run: g.max_drafts_per_brand_per_run,
                openai_model: g.openai_model, reports_enabled: g.reports_enabled, report_recipient: g.report_recipient, report_sender: g.report_sender } });
              setMsg("Saved"); onSaved();
            })}>Save</Button>{msg && <span className="text-sm text-emerald-600">{msg}</span>}
          </div>
        </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">Daily reports</CardTitle>
        <CardDescription>09:00: the previous day. 18:00: today until 18:00. Sent once per day each; replies are checked in the Gmail conversations of sent emails.</CardDescription></CardHeader>
        <CardContent className="grid gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Report"><Select value={slot} onChange={(e) => setSlot(e.target.value as "morning" | "evening")}>
              <option value="morning">9 AM (previous day)</option><option value="evening">6 PM (today)</option></Select></Field>
            <Button variant="outline" disabled={busy} onClick={() => run(async () => setPreview(await api(`/outreach/report-preview?slot=${slot}`)))}>
              <RefreshCw className="h-4 w-4" />Preview (sends nothing)</Button>
          </div>
          {preview && (
            <div className="rounded-md border bg-muted/30 p-3 text-xs">
              <div className="mb-2 text-muted-foreground">To {preview.to} · from {preview.from}</div>
              <div className="mb-2 font-medium">{preview.subject}</div>
              <pre className="whitespace-pre-wrap font-mono">{preview.body}</pre>
            </div>
          )}
          {!!history.data?.length && (
            <Table><THead><TR><TH>Date</TH><TH>Report</TH><TH>Status</TH><TH>Subject</TH></TR></THead>
              <TBody>{history.data.map((h) => (
                <TR key={h.id}><TD>{h.report_date}</TD><TD>{h.slot === "morning" ? "9 AM" : "6 PM"}</TD>
                  <TD><Badge tone={h.status === "sent" ? "green" : "amber"}>{h.status === "sent" ? "Sent" : "Uncertain"}</Badge></TD><TD className="text-xs">{h.subject}</TD></TR>
              ))}</TBody></Table>
          )}
        </CardContent></Card>
      <ErrorNote error={err} />
    </div>
  );
}

// ------------------------------------------------------------------ page
export default function OutreachPage() {
  const { me, brandId, currentBrand } = useApp();
  const [tab, setTab] = useState<Tab>("Leads");
  const base = brandId === "all" ? null : `/brands/${brandId}/outreach`;
  const { data: ov, error, reload } = usePoll<Overview>(base, 20000);
  const [preview, setPreview] = useState<RunResult | null>(null);
  const { busy, err, run } = useRunner();

  if (brandId === "all") {
    return (<><PageHeader title="Email Outreach" description="AI-drafted, approved, rate-limited outreach from each brand's mailboxes." /><PickBrand /></>);
  }
  if (!ov || !base) return (<><PageHeader title="Email Outreach" /><ErrorNote error={error} /></>);
  const s = ov.settings;
  const admin = RANK[ov.role] >= 4;
  const toggleStop = () => run(async () => { await api(`${base}/settings`, { method: "PUT", json: { emergency_stop: !s.emergency_stop } }); reload(); });

  return (
    <>
      <PageHeader title={`Email Outreach · ${currentBrand?.name ?? ""}`}
        description="Add leads → AI writes drafts → you approve → one email per run from the least-used mailbox, within limits and sending hours."
        actions={<>
          <Button variant="outline" disabled={busy} onClick={() => run(async () => setPreview(await api<RunResult>(`${base}/preview`, { method: "POST" })))}>Preview next send</Button>
          {admin && <Button variant="outline" disabled={busy} onClick={() => run(async () => { setPreview(await api<RunResult>(`${base}/run`, { method: "POST" })); reload(); })}>
            <Play className="h-4 w-4" />Run this brand now</Button>}
        </>} />

      <div className={cn("mb-4 flex flex-wrap items-center gap-3 rounded-lg border p-3", s.emergency_stop ? "border-destructive/50 bg-destructive/10" : "border-emerald-500/40 bg-emerald-500/10")}>
        <Octagon className={cn("h-5 w-5", s.emergency_stop ? "text-destructive" : "text-emerald-600")} />
        <div className="min-w-0 flex-1 text-sm">
          <div className="font-medium">{s.emergency_stop ? "Emergency stop is ON — nothing is sent or drafted for this brand." : s.enabled ? "Sending is on." : "This brand is left out of automatic runs."}</div>
          <div className="text-xs text-muted-foreground">
            India time {ov.local_time} · {ov.within_hours ? "inside" : "outside"} sending hours ({s.sending_start_time}–{s.sending_end_time})
            {!ov.global.live_sending && " · Server is in simulation mode: emails are recorded but not delivered"}
            {!ov.global.openai_configured && " · No OpenAI key yet, so drafts can't be written (Super Admin: add it under Automation)"}
          </div>
        </div>
        {admin && <Button size="sm" variant={s.emergency_stop ? "default" : "destructive"} disabled={busy} onClick={toggleStop}>
          {s.emergency_stop ? "Turn sending on" : "Emergency stop"}</Button>}
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Needs approval" value={ov.counts.Generated ?? 0} />
        <Stat label="Approved, waiting" value={ov.approved_ready} />
        <Stat label="Sent today" value={ov.sent_today} />
        <Stat label="Sent (all time)" value={ov.counts.Sent ?? 0} />
        <Stat label="Failed / skipped" value={(ov.counts.Failed ?? 0) + (ov.counts.Skipped ?? 0)} />
      </div>
      <RunNote r={preview} />
      <ErrorNote error={err} />

      <div className="mb-4 flex flex-wrap gap-1 border-b">
        {TABS.filter((t) => t !== "Automation" || me?.is_super_admin).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>{t}</button>
        ))}
      </div>
      {tab === "Leads" && <LeadsTab base={base} role={ov.role} refreshOverview={reload} />}
      {tab === "Mailboxes" && <MailboxesTab base={base} role={ov.role} />}
      {tab === "Send log" && <SendLogTab base={base} />}
      {tab === "Suppression" && <SuppressionTab base={base} role={ov.role} />}
      {tab === "Settings" && <SettingsTab base={base} settings={s} role={ov.role} onSaved={reload} />}
      {tab === "Automation" && <AutomationTab isSuper={!!me?.is_super_admin} global={ov.global} onSaved={reload} />}
    </>
  );
}
