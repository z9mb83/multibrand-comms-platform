"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Sender } from "@/lib/types";

type C = { contact_id: number; contact_name: string | null; email: string | null; phone: string | null };
type Msg = { id: number; status: string; hold_reason: string | null; sender_account_id: number | null };

export default function ComposePage() {
  const { brandId } = useApp();
  const b = brandId === "all" ? null : brandId;
  const contacts = usePoll<{ items: C[] }>(b ? `/brands/${b}/contacts?limit=500` : null, 0);
  const [channel, setChannel] = useState("email");
  const senders = usePoll<Sender[]>(b ? `/brands/${b}/senders?channel=${channel}` : null, 0);
  const [contactId, setContactId] = useState("");
  const [mode, setMode] = useState("AUTO");
  const [senderId, setSenderId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [result, setResult] = useState<Msg | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!b) return (<><PageHeader title="Compose" /><PickBrand /></>);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const r = await api<Msg>(`/brands/${b}/compose`, { method: "POST", json: {
        contact_id: Number(contactId), channel, subject: subject || null, body, routing_mode: mode,
        sender_id: mode === "FIXED" && senderId ? Number(senderId) : null, send_now: true } });
      setResult(r);
    } catch (e2) { setErr((e2 as Error).message); }
  }
  const senderAddr = (id: number | null) => senders.data?.find((s) => s.id === id)?.address ?? "—";

  return (
    <>
      <PageHeader title="Compose" description="Send one message. Consent, suppression and sender eligibility are checked before dispatch." />
      <ErrorNote error={err} />
      <Card className="max-w-2xl">
        <CardContent className="p-4">
          <form onSubmit={send} className="grid gap-3 sm:grid-cols-2">
            <Field label="Channel"><Select value={channel} onChange={(e) => { setChannel(e.target.value); setSenderId(""); }}><option>email</option><option>whatsapp</option><option>sms</option></Select></Field>
            <Field label="Recipient">
              <Select value={contactId} onChange={(e) => setContactId(e.target.value)} required>
                <option value="">Select contact…</option>
                {contacts.data?.items.filter((c) => (channel === "email" ? c.email : c.phone)).map((c) => (
                  <option key={c.contact_id} value={c.contact_id}>{c.contact_name ?? ""} {channel === "email" ? c.email : c.phone}</option>))}
              </Select>
            </Field>
            <Field label="Routing mode"><Select value={mode} onChange={(e) => setMode(e.target.value)}><option>AUTO</option><option>STICKY</option><option>FIXED</option></Select></Field>
            {mode === "FIXED" && (
              <Field label="Sender">
                <Select value={senderId} onChange={(e) => setSenderId(e.target.value)} required>
                  <option value="">Select sender…</option>
                  {senders.data?.filter((s) => !s.is_notification_sender).map((s) => <option key={s.id} value={s.id}>{s.address} ({s.status})</option>)}
                </Select>
              </Field>
            )}
            {channel === "email" && <Field label="Subject" className="sm:col-span-2"><Input value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>}
            <Field label="Message" className="sm:col-span-2"><Textarea value={body} onChange={(e) => setBody(e.target.value)} required /></Field>
            <Button className="sm:w-fit">Send</Button>
          </form>
          {result && (
            <div className="mt-4 rounded-md border p-3 text-sm">
              Message #{result.id}: <Badge>{result.status}</Badge> {result.hold_reason && <span className="text-muted-foreground">— {result.hold_reason}</span>}
              {result.sender_account_id && <div className="mt-1 text-xs text-muted-foreground">Sent from {senderAddr(result.sender_account_id)}</div>}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
