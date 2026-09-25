"use client";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

type O = { brands: { brand_id: number; brand: string; sent: number; replies: number; failed_or_held: number }[]; daily: { date: string; channel: string; count: number }[] };
const COLORS: Record<string, string> = { email: "bg-blue-500", whatsapp: "bg-emerald-500", sms: "bg-amber-500" };

export default function AnalyticsPage() {
  const { brandId } = useApp();
  const { data, error } = usePoll<O>(`/analytics/overview?days=14${brandId === "all" ? "" : `&brand_id=${brandId}`}`, 60000);
  const days = Array.from(new Set(data?.daily.map((d) => d.date) ?? [])).sort();
  const max = Math.max(1, ...days.map((d) => data!.daily.filter((x) => x.date === d).reduce((s, x) => s + x.count, 0)));
  return (
    <>
      <PageHeader title="Analytics" description="Provider-accepted sends by day and channel, last 14 days." />
      <ErrorNote error={error} />
      <Card>
        <CardHeader><CardTitle>Messages sent per day</CardTitle></CardHeader>
        <CardContent>
          {days.length === 0 && <p className="text-sm text-muted-foreground">No sends in this period.</p>}
          <div className="flex h-48 items-end gap-2 overflow-x-auto">
            {days.map((d) => {
              const items = data!.daily.filter((x) => x.date === d);
              const total = items.reduce((s, x) => s + x.count, 0);
              return (
                <div key={d} className="flex min-w-[36px] flex-1 flex-col items-center gap-1">
                  <span className="text-xs tabular-nums">{total}</span>
                  <div className="flex w-full flex-col-reverse overflow-hidden rounded" style={{ height: `${(total / max) * 150}px` }}>
                    {items.map((x) => <div key={x.channel} className={COLORS[x.channel]} style={{ height: `${(x.count / total) * 100}%` }} title={`${x.channel}: ${x.count}`} />)}
                  </div>
                  <span className="text-[10px] text-muted-foreground">{d.slice(5)}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex gap-4 text-xs">{Object.entries(COLORS).map(([k, c]) => <span key={k} className="flex items-center gap-1"><span className={`h-2.5 w-2.5 rounded ${c}`} />{k}</span>)}</div>
        </CardContent>
      </Card>
      <Card className="mt-5">
        <CardHeader><CardTitle>Reply rate by brand</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Brand</TH><TH>Sent</TH><TH>Replies</TH><TH>Reply rate</TH><TH>Failed/held</TH></TR></THead>
            <TBody>
              {data?.brands.map((b) => (
                <TR key={b.brand_id}><TD>{b.brand}</TD><TD>{b.sent}</TD><TD>{b.replies}</TD><TD>{b.sent ? `${((b.replies / b.sent) * 100).toFixed(1)}%` : "—"}</TD><TD>{b.failed_or_held}</TD></TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
