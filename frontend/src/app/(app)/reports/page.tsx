"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, Stat } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, download } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

type Rep = { id: number; kind: string; report_date: string; status: string; generated_at: string; overall: Record<string, number | Record<string, number>>; action_items: string[] };
type Del = { id: number; report_id: number | null; channel: string; recipient: string; status: string; attempts: number; last_error: string | null; sent_at: string | null; next_attempt_at: string | null };
type Settings = { morning_time: string; evening_time: string; timezone: string; notification_channel: string; master_email: string | null };

export default function ReportsPage() {
  const { me } = useApp();
  const reps = usePoll<Rep[]>(me?.is_super_admin ? "/master/reports" : null, 30000);
  const dels = usePoll<Del[]>(me?.is_super_admin ? "/master/deliveries" : null, 30000);
  const settings = usePoll<Settings>(me?.is_super_admin ? "/master/settings" : null, 0);
  const [err, setErr] = useState<string | null>(null);
  const [html, setHtml] = useState<string | null>(null);
  if (me && !me.is_super_admin) return (<><PageHeader title="Master Reports" /><p className="text-sm text-muted-foreground">Super Admin only.</p></>);

  async function gen(kind: string, deliver: boolean) {
    try { await api("/master/reports/generate", { method: "POST", json: { kind, deliver } }); reps.reload(); dels.reload(); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }
  async function view(id: number) {
    try {
      const blob = await api<Blob>(`/master/reports/${id}/html`);
      setHtml(await blob.text());
    } catch (e) { setErr((e as Error).message); }
  }
  const s = settings.data;

  return (
    <>
      <PageHeader title="Master Reports" description="Consolidated morning and evening reports across all brands, with per-brand breakdowns."
        actions={<>
          <Button variant="outline" onClick={() => gen("morning", false)}>Generate morning</Button>
          <Button variant="outline" onClick={() => gen("evening", false)}>Generate evening</Button>
          <Button onClick={() => gen("evening", true)}>Generate & deliver</Button>
        </>} />
      <ErrorNote error={err || reps.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Morning report" value={s?.morning_time ?? "—"} hint={s?.timezone} />
        <Stat label="Evening report" value={s?.evening_time ?? "—"} hint={s?.timezone} />
        <Stat label="Channel" value={<span className="text-base">{s?.notification_channel ?? "—"}</span>} />
        <Stat label="Master email" value={<span className="break-all text-base">{s?.master_email ?? "not set"}</span>} />
      </div>
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Report history</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead><TR><TH>Date</TH><TH>Kind</TH><TH>Sent</TH><TH>Replies</TH><TH>Status</TH><TH /></TR></THead>
              <TBody>
                {reps.data?.map((r) => (
                  <TR key={r.id}>
                    <TD>{r.report_date}</TD><TD>{r.kind}</TD><TD>{String(r.overall?.messages_sent ?? 0)}</TD><TD>{String(r.overall?.new_replies ?? 0)}</TD><TD><Badge>{r.status}</Badge></TD>
                    <TD className="space-x-1 whitespace-nowrap text-right">
                      <Button size="sm" variant="ghost" onClick={() => view(r.id)}>View</Button>
                      <Button size="sm" variant="ghost" onClick={() => download(`/master/reports/${r.id}/xlsx`, `${r.kind}-${r.report_date}.xlsx`)}>XLSX</Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Delivery history</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead><TR><TH>Report</TH><TH>Channel</TH><TH>Recipient</TH><TH>Status</TH><TH>Tries</TH></TR></THead>
              <TBody>
                {dels.data?.map((d) => (
                  <TR key={d.id}>
                    <TD>{d.report_id}</TD><TD>{d.channel}</TD><TD className="text-xs">{d.recipient}</TD>
                    <TD><Badge>{d.status}</Badge>{d.last_error && <div className="max-w-[16rem] truncate text-xs text-muted-foreground" title={d.last_error}>{d.last_error}</div>}
                      {d.status === "retrying" && <div className="text-xs text-muted-foreground">next {fmtDate(d.next_attempt_at)}</div>}</TD>
                    <TD>{d.attempts}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      {html && (
        <Card className="mt-5">
          <CardHeader className="flex-row items-center justify-between"><CardTitle>Report preview</CardTitle><Button size="sm" variant="ghost" onClick={() => setHtml(null)}>Close</Button></CardHeader>
          <CardContent><iframe title="Report" className="h-[600px] w-full rounded border bg-white" srcDoc={html} sandbox="" /></CardContent>
        </Card>
      )}
    </>
  );
}
