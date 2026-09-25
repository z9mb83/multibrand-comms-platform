"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Campaign } from "@/lib/types";
import { fmtDate } from "@/lib/utils";

type Rule = { id: number; name: string; campaign_id: number | null; steps: { after_days: number; mode: string }[]; max_attempts: number; stop_on_reply: boolean };
type FU = { id: number; contact_id: number; campaign_id: number | null; step_index: number; mode: string; due_at: string; status: string; cancel_reason: string | null };

export default function FollowUpsPage() {
  const { brandId } = useApp();
  const b = brandId === "all" ? null : brandId;
  const rules = usePoll<Rule[]>(b ? `/brands/${b}/follow-up-rules` : null, 0);
  const tasks = usePoll<FU[]>(b ? `/brands/${b}/follow-ups` : null, 15000);
  const camps = usePoll<Campaign[]>(b ? `/brands/${b}/campaigns` : null, 0);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "Standard", campaign_id: "", d1: 2, d2: 6, mode: "scheduled", stop_on_reply: true });
  if (!b) return (<><PageHeader title="Follow-ups" /><PickBrand /></>);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api(`/brands/${b}/follow-up-rules`, { method: "POST", json: {
        name: form.name, campaign_id: form.campaign_id ? Number(form.campaign_id) : null, max_attempts: 2, stop_on_reply: form.stop_on_reply,
        steps: [{ after_days: form.d1, mode: form.mode }, { after_days: form.d2, mode: form.mode }] } });
      rules.reload(); setErr(null);
    } catch (e2) { setErr((e2 as Error).message); }
  }
  async function approve(id: number) {
    const body = window.prompt("Follow-up message (leave blank to use the template):") ?? undefined;
    try { await api(`/brands/${b}/follow-ups/${id}/approve`, { method: "POST", json: { body: body || null } }); tasks.reload(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <>
      <PageHeader title="Follow-ups" description="Steps count from the initial send (e.g. Day 3 and Day 7). Any reply pauses pending follow-ups until reviewed." />
      <ErrorNote error={err || tasks.error} />
      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Scheduled follow-ups</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead><TR><TH>#</TH><TH>Contact</TH><TH>Step</TH><TH>Mode</TH><TH>Due</TH><TH>Status</TH><TH /></TR></THead>
              <TBody>
                {tasks.data?.map((t) => (
                  <TR key={t.id}><TD>{t.id}</TD><TD>{t.contact_id}</TD><TD>{t.step_index + 1}</TD><TD>{t.mode}</TD><TD className="text-xs">{fmtDate(t.due_at)}</TD>
                    <TD><Badge>{t.status}</Badge>{t.cancel_reason && <div className="text-xs text-muted-foreground">{t.cancel_reason}</div>}</TD>
                    <TD>{t.status === "awaiting_approval" && <Button size="sm" onClick={() => approve(t.id)}>Approve & send</Button>}</TD></TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Rules</CardTitle><CardDescription>{rules.data?.length ?? 0} configured</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {rules.data?.map((r) => (
              <div key={r.id} className="rounded-md border p-2 text-sm">
                <div className="font-medium">{r.name}</div>
                <div className="text-xs text-muted-foreground">{r.steps.map((s) => `+${s.after_days}d ${s.mode}`).join(" → ")} · max {r.max_attempts}{r.stop_on_reply ? " · stops on reply" : ""}</div>
              </div>
            ))}
            <form onSubmit={create} className="grid gap-2">
              <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Campaign"><Select value={form.campaign_id} onChange={(e) => setForm({ ...form, campaign_id: e.target.value })}><option value="">Select…</option>{camps.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="1st after (days)"><Input type="number" min={1} value={form.d1} onChange={(e) => setForm({ ...form, d1: Number(e.target.value) })} /></Field>
                <Field label="2nd after (days)"><Input type="number" min={1} value={form.d2} onChange={(e) => setForm({ ...form, d2: Number(e.target.value) })} /></Field>
              </div>
              <Field label="Mode"><Select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}><option value="scheduled">Create task</option><option value="reminder">Reminder only</option><option value="manual">Manual</option><option value="approval_send">Approval-required send</option></Select></Field>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.stop_on_reply} onChange={(e) => setForm({ ...form, stop_on_reply: e.target.checked })} /> Stop when contact replies</label>
              <Button disabled={!form.campaign_id}>Add rule</Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
