"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Campaign } from "@/lib/types";
import { fmtDate } from "@/lib/utils";

const ACTIONS: Record<string, string[]> = {
  draft: ["submit", "approve", "cancel"], pending_approval: ["approve", "cancel"], approved: ["launch", "pause", "cancel"],
  running: ["pause", "emergency_stop"], paused: ["resume", "emergency_stop", "cancel"],
};

export default function CampaignsPage() {
  const { brandId, me } = useApp();
  const b = brandId === "all" ? null : brandId;
  const { data, error, reload } = usePoll<Campaign[]>(b ? `/brands/${b}/campaigns` : null, 10000);
  const [open, setOpen] = useState<number | null>(null);
  const detail = usePoll<{ stats: Record<string, number> }>(b && open ? `/brands/${b}/campaigns/${open}` : null, 5000);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", channel: "email", subject: "", body: "" });

  async function act(id: number, action: string) {
    let reason: string | null = null;
    if (action === "emergency_stop") {
      reason = window.prompt("Reason for emergency stop?") ?? null;
      if (!reason) return;
    }
    try { await api(`/brands/${b}/campaigns/${id}/actions`, { method: "POST", json: { action, reason } }); setErr(null); reload(); detail.reload(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const c = await api<Campaign>(`/brands/${b}/campaigns`, { method: "POST", json: form });
      await api(`/brands/${b}/campaigns/${c.id}/recipients`, { method: "POST", json: { segment: { has_consent: true } } });
      setForm({ name: "", channel: "email", subject: "", body: "" }); reload(); setOpen(c.id);
    } catch (e2) { setErr((e2 as Error).message); }
  }
  async function stopAll() {
    const reason = window.prompt("Emergency stop ALL running campaigns in all your brands. Reason?");
    if (!reason) return;
    try { await api("/emergency-stop", { method: "POST", json: { reason } }); reload(); } catch (e) { setErr((e as Error).message); }
  }

  const header = <PageHeader title="Campaigns" description="Drafts need approval before launch. Pause, resume or emergency-stop at any time."
    actions={me?.is_super_admin ? <Button variant="destructive" onClick={stopAll}>Emergency stop (all brands)</Button> : undefined} />;
  if (!b) return (<>{header}<PickBrand /></>);

  return (
    <>
      {header}
      <ErrorNote error={error || err} />
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Name</TH><TH>Channel</TH><TH>Status</TH><TH>Scheduled</TH><TH>Created</TH><TH /></TR></THead>
            <TBody>
              {data?.map((c) => (
                <TR key={c.id} className={open === c.id ? "bg-muted/40" : ""}>
                  <TD><button className="font-medium hover:underline" onClick={() => setOpen(open === c.id ? null : c.id)}>{c.name}</button></TD>
                  <TD>{c.channel}</TD><TD><Badge>{c.status}</Badge></TD><TD className="text-xs">{fmtDate(c.scheduled_at)}</TD><TD className="text-xs">{fmtDate(c.created_at)}</TD>
                  <TD className="space-x-1 whitespace-nowrap text-right">
                    {(ACTIONS[c.status] ?? []).map((a) => (
                      <Button key={a} size="sm" variant={a === "emergency_stop" ? "destructive" : a === "approve" || a === "launch" ? "default" : "outline"} onClick={() => act(c.id, a)}>{a.replace("_", " ")}</Button>
                    ))}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {open && detail.data && (
        <Card className="mt-4">
          <CardHeader><CardTitle>Delivery status</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(detail.data.stats).map(([k, v]) => <Badge key={k}>{`${k}: ${v}`}</Badge>)}
            {!Object.keys(detail.data.stats).length && <span className="text-sm text-muted-foreground">No recipients yet.</span>}
          </CardContent>
        </Card>
      )}
      <Card className="mt-5 max-w-2xl">
        <CardHeader><CardTitle>New campaign</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
            <Field label="Channel"><Select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}><option>email</option><option>whatsapp</option><option>sms</option></Select></Field>
            {form.channel === "email" && <Field label="Subject" className="sm:col-span-2"><Input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></Field>}
            <Field label="Message" className="sm:col-span-2"><Textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} required /></Field>
            <p className="text-xs text-muted-foreground sm:col-span-2">Recipients: every contact in this brand with recorded consent for the channel. The campaign is created as a draft.</p>
            <Button className="sm:w-fit">Create draft</Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
