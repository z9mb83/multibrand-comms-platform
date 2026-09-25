"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { cn, fmtDate } from "@/lib/utils";

type Item = { direction: "in" | "out"; at: string; id: number; body: string | null; subject: string | null; status?: string; classification?: string | null; classification_source?: string | null };
const CLASSES = ["Interested", "Needs information", "Callback requested", "Not interested", "Unsubscribe", "Other"];

export default function InboxPage() {
  const { brandId } = useApp();
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams();
  if (brandId !== "all") qs.set("brand_id", String(brandId));
  if (status) qs.set("status", status);
  const list = usePoll<Conversation[]>(`/inbox?${qs}`, 10000);
  const [sel, setSel] = useState<number | null>(null);
  const detail = usePoll<{ conversation: Conversation; timeline: Item[] }>(sel ? `/inbox/${sel}` : null, 10000);
  const [reply, setReply] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const current = list.data?.find((c) => c.id === sel);

  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); list.reload(); detail.reload(); } catch (e) { setErr((e as Error).message); }
  };

  return (
    <>
      <PageHeader title="Unified Inbox" description="Replies from every connected mailbox, WhatsApp number and SMS header. Refreshes every 10s."
        actions={<Select className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All conversations</option><option value="needs_reply">Needs reply</option><option value="replied">Replied</option><option value="closed">Closed</option>
        </Select>} />
      <ErrorNote error={list.error || err} />
      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardContent className="max-h-[70vh] overflow-y-auto p-0">
            {list.data?.length === 0 && <p className="p-6 text-sm text-muted-foreground">No replies yet.</p>}
            {list.data?.map((c) => (
              <button key={c.id} onClick={() => setSel(c.id)} className={cn("block w-full border-b p-3 text-left hover:bg-muted/40", sel === c.id && "bg-primary/5")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{c.contact_name || c.contact_email || c.contact_phone}</span>
                  {c.unread_count > 0 && <span className="rounded-full bg-primary px-1.5 text-xs text-primary-foreground">{c.unread_count}</span>}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                  <Badge tone="blue">{c.brand_name}</Badge><span>{c.channel}</span><span>· {c.sender_address}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{c.last_message}</p>
                <div className="mt-1 flex gap-1">{c.classification && <Badge>{c.classification}</Badge>}<Badge>{c.status}</Badge></div>
              </button>
            ))}
          </CardContent>
        </Card>
        <Card className="lg:col-span-3">
          <CardContent className="flex max-h-[70vh] flex-col p-0">
            {!sel && <p className="p-6 text-sm text-muted-foreground">Select a conversation.</p>}
            {sel && current && (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b p-3 text-sm">
                  <span className="font-medium">{current.contact_name || current.contact_email || current.contact_phone}</span>
                  <Badge tone="blue">{current.brand_name}</Badge>
                  <span className="text-xs text-muted-foreground">via {current.sender_address}</span>
                  <div className="ml-auto flex gap-1">
                    {current.followups_paused && <Button size="sm" variant="outline" onClick={() => run(() => api(`/inbox/${sel}/resume-followups`, { method: "POST" }))}>Resume follow-ups</Button>}
                    <Button size="sm" variant="outline" onClick={() => run(() => api(`/inbox/${sel}`, { method: "PATCH", json: { status: "closed" } }))}>Close</Button>
                  </div>
                </div>
                <div className="flex-1 space-y-3 overflow-y-auto p-3">
                  {detail.data?.timeline.map((m) => (
                    <div key={`${m.direction}-${m.id}`} className={cn("max-w-[85%] rounded-lg p-3 text-sm", m.direction === "in" ? "bg-muted" : "ml-auto bg-primary/10")}>
                      {m.subject && <div className="mb-1 text-xs font-medium">{m.subject}</div>}
                      <div className="whitespace-pre-wrap">{m.body}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        {fmtDate(m.at)} {m.status && <Badge>{m.status}</Badge>}
                        {m.direction === "in" && (
                          <Select className="h-7 w-44 text-xs" value={m.classification ?? ""} onChange={(e) => run(() => api(`/inbox/messages/${m.id}/classification`, { method: "POST", json: { classification: e.target.value } }))}>
                            {CLASSES.map((c) => <option key={c}>{c}</option>)}
                          </Select>
                        )}
                        {m.classification_source && m.direction === "in" && <span>({m.classification_source})</span>}
                      </div>
                    </div>
                  ))}
                </div>
                <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); run(async () => { await api(`/inbox/${sel}/reply`, { method: "POST", json: { body: reply } }); setReply(""); }); }}>
                  <Textarea className="min-h-[44px]" placeholder="Reply from the original sender…" value={reply} onChange={(e) => setReply(e.target.value)} required />
                  <Button>Send</Button>
                </form>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
