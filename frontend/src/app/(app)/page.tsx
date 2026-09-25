"use client";

import Link from "next/link";

import { usePoll, useApp } from "@/components/app-context";
import { ErrorNote, PageHeader, Stat } from "@/components/shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

type Row = { brand_id: number; brand: string; sent: number; failed_or_held: number; queued: number; replies: number;
  unanswered: number; open_tasks: number; follow_ups_due: number; running_campaigns: number; active_senders: number };
type Overview = { brands: Row[]; totals: Partial<Row> };

export default function OverviewPage() {
  const { brandId, setBrandId } = useApp();
  const q = brandId === "all" ? "" : `?brand_id=${brandId}`;
  const { data, error } = usePoll<Overview>(`/analytics/overview${q}`, 15000);
  const t = data?.totals ?? {};
  return (
    <>
      <PageHeader title="Overview" description={brandId === "all" ? "All brands · last 7 days · refreshes every 15s" : "Brand workspace · last 7 days"} />
      <ErrorNote error={error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Messages sent" value={t.sent ?? "—"} />
        <Stat label="New replies" value={t.replies ?? "—"} hint={`${t.unanswered ?? 0} unanswered`} />
        <Stat label="Follow-ups due" value={t.follow_ups_due ?? "—"} />
        <Stat label="Needs attention" value={t.failed_or_held ?? "—"} hint="failed / held sends" />
        <Stat label="Queued" value={t.queued ?? "—"} />
        <Stat label="Open tasks" value={t.open_tasks ?? "—"} />
        <Stat label="Running campaigns" value={t.running_campaigns ?? "—"} />
        <Stat label="Active senders" value={t.active_senders ?? "—"} />
      </div>
      <Card className="mt-5">
        <CardHeader><CardTitle>By brand</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Brand</TH><TH>Sent</TH><TH>Replies</TH><TH>Unanswered</TH><TH>FU due</TH><TH>Failed/held</TH><TH>Open tasks</TH><TH>Active senders</TH></TR></THead>
            <TBody>
              {data?.brands.map((b) => (
                <TR key={b.brand_id}>
                  <TD className="whitespace-nowrap"><button className="font-medium text-primary hover:underline" onClick={() => setBrandId(b.brand_id)}>{b.brand}</button></TD>
                  <TD>{b.sent}</TD><TD>{b.replies}</TD><TD>{b.unanswered}</TD><TD>{b.follow_ups_due}</TD>
                  <TD className={b.failed_or_held ? "text-destructive" : ""}>{b.failed_or_held}</TD><TD>{b.open_tasks}</TD><TD>{b.active_senders}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <p className="mt-4 text-xs text-muted-foreground">
        Quick links: <Link className="underline" href="/inbox">Unified Inbox</Link> · <Link className="underline" href="/tasks">Today&apos;s tasks</Link> · <Link className="underline" href="/import">Import a workbook</Link>
      </p>
    </>
  );
}
