"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand, Stat } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Sender } from "@/lib/types";
import { fmtDate } from "@/lib/utils";

type Overview = {
  active_senders: number; total_senders: number; next_sender: { id: number; address: string } | null;
  accounts: { id: number; eligible: boolean; reason: string | null; effective_daily_limit: number }[];
  history: { id: number; message_id: number | null; mode: string; outcome: string; sender: string | null; reason: string | null; at: string }[];
};

const TITLES: Record<string, string> = { email: "Email Accounts", whatsapp: "WhatsApp Accounts", sms: "SMS Accounts" };
const PROVIDERS: Record<string, string[]> = { email: ["gmail", "mock"], whatsapp: ["meta_cloud", "mock"], sms: ["sms_generic", "mock"] };

export default function AccountsPage() {
  const { channel } = useParams<{ channel: string }>();
  const { brandId } = useApp();
  const b = brandId === "all" ? null : brandId;
  const senders = usePoll<Sender[]>(b ? `/brands/${b}/senders?channel=${channel}` : null, 20000);
  const overview = usePoll<Overview>(b ? `/brands/${b}/senders/routing/overview?channel=${channel}` : null, 10000);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ address: "", display_name: "", provider: PROVIDERS[channel]?.[0] ?? "mock", daily_limit: 100, hourly_limit: 30, secret_ref: "" });

  if (!TITLES[channel]) return <p>Unknown channel</p>;
  if (!b) return (<><PageHeader title={TITLES[channel]} /><PickBrand /></>);

  const eligibility = new Map(overview.data?.accounts.map((a) => [a.id, a]) ?? []);
  const refresh = () => { senders.reload(); overview.reload(); };

  async function patch(id: number, body: Record<string, unknown>) {
    try { await api(`/brands/${b}/senders/${id}`, { method: "PATCH", json: body }); setErr(null); refresh(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function health(id: number) {
    try { await api(`/brands/${b}/senders/${id}/health-check`, { method: "POST" }); refresh(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api(`/brands/${b}/senders`, { method: "POST", json: { ...form, channel, secret_ref: form.secret_ref || null, rotation_order: (senders.data?.length ?? 0) + 1 } });
      setForm({ ...form, address: "", display_name: "", secret_ref: "" });
      setErr(null);
      refresh();
    } catch (e2) { setErr((e2 as Error).message); }
  }

  return (
    <>
      <PageHeader title={TITLES[channel]} description="Round-robin only rotates across connected, active accounts within their limits. Accounts are never rotated to get around provider limits." />
      <ErrorNote error={err || senders.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Eligible now" value={overview.data?.active_senders ?? "—"} hint={`of ${overview.data?.total_senders ?? 0} configured`} />
        <Stat label="Next eligible sender" value={<span className="block truncate text-base" title={overview.data?.next_sender?.address}>{overview.data?.next_sender?.address ?? "none"}</span>} />
        <Stat label="Sent today" value={senders.data?.reduce((s, x) => s + x.sent_today, 0) ?? "—"} />
        <Stat label="Unhealthy" value={senders.data?.filter((s) => s.health_status === "failing" || s.connection_status === "error").length ?? "—"} />
      </div>

      <Card className="mt-5">
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>#</TH><TH>Address</TH><TH>Status</TH><TH>Connection</TH><TH>Health</TH><TH>Today</TH><TH>Hour</TH><TH>Eligible</TH><TH /></TR></THead>
            <TBody>
              {senders.data?.map((s) => {
                const el = eligibility.get(s.id);
                return (
                  <TR key={s.id}>
                    <TD>{s.rotation_order}</TD>
                    <TD><div className="font-medium">{s.address}</div><div className="text-xs text-muted-foreground">{s.provider}{s.is_notification_sender ? " · notifications" : ""}</div></TD>
                    <TD><Badge>{s.status}</Badge></TD>
                    <TD><Badge>{s.connection_status}</Badge></TD>
                    <TD title={s.last_error ?? ""}><Badge>{s.health_status}</Badge></TD>
                    <TD className="tabular-nums">{s.sent_today}/{el?.effective_daily_limit ?? s.daily_limit}</TD>
                    <TD className="tabular-nums">{s.sent_this_hour}/{s.hourly_limit}</TD>
                    <TD>{s.is_notification_sender ? <span className="text-xs text-muted-foreground">n/a</span> : el?.eligible ? <Badge tone="green">yes</Badge> : <span className="text-xs text-muted-foreground">{el?.reason}</span>}</TD>
                    <TD className="space-x-1 whitespace-nowrap text-right">
                      {s.status === "active"
                        ? <Button size="sm" variant="outline" onClick={() => patch(s.id, { status: "paused", paused_reason: "manual" })}>Pause</Button>
                        : <Button size="sm" variant="outline" onClick={() => patch(s.id, { status: "active", paused_reason: null })}>Activate</Button>}
                      <Button size="sm" variant="ghost" onClick={() => health(s.id)}>Check</Button>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Routing history</CardTitle><CardDescription>Latest sender decisions for this brand</CardDescription></CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead><TR><TH>When</TH><TH>Msg</TH><TH>Mode</TH><TH>Outcome</TH><TH>Sender</TH></TR></THead>
              <TBody>
                {overview.data?.history.map((h) => (
                  <TR key={h.id}><TD className="text-xs">{fmtDate(h.at)}</TD><TD>{h.message_id ?? "—"}</TD><TD>{h.mode}</TD>
                    <TD><Badge tone={h.outcome === "assigned" ? "green" : "amber"}>{h.outcome}</Badge></TD>
                    <TD className="text-xs">{h.sender ?? h.reason}</TD></TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Add {channel} account</CardTitle><CardDescription>Credentials go in Secret Manager; store only the reference here (e.g. gsm:projects/…/secrets/…/versions/latest).</CardDescription></CardHeader>
          <CardContent>
            <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
              <Field label={channel === "email" ? "Email address" : channel === "whatsapp" ? "WhatsApp number (E.164)" : "DLT header"}>
                <Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} required />
              </Field>
              <Field label="Display name"><Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
              <Field label="Provider"><Select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>{PROVIDERS[channel].map((p) => <option key={p}>{p}</option>)}</Select></Field>
              <Field label="Secret reference"><Input value={form.secret_ref} onChange={(e) => setForm({ ...form, secret_ref: e.target.value })} placeholder="gsm:…" /></Field>
              <Field label="Daily limit"><Input type="number" min={0} value={form.daily_limit} onChange={(e) => setForm({ ...form, daily_limit: Number(e.target.value) })} /></Field>
              <Field label="Hourly limit"><Input type="number" min={0} value={form.hourly_limit} onChange={(e) => setForm({ ...form, hourly_limit: Number(e.target.value) })} /></Field>
              <Button className="sm:col-span-2 sm:w-fit">Add account (inactive until connected)</Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
