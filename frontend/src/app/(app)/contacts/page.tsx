"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader, PickBrand } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";

type C = { contact_id: number; contact_name: string | null; company_name: string | null; email: string | null; phone: string | null; status: string; consent: Record<string, string> };

export default function ContactsPage() {
  const { brandId } = useApp();
  const b = brandId === "all" ? null : brandId;
  const [q, setQ] = useState("");
  const { data, error, reload } = usePoll<{ total: number; items: C[] }>(b ? `/brands/${b}/contacts?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}` : null, 0);
  const [form, setForm] = useState({ contact_name: "", company_name: "", email: "", phone: "", email_consent: false, wa_consent: false });
  const [err, setErr] = useState<string | null>(null);
  if (!b) return (<><PageHeader title="Contacts" /><PickBrand /></>);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      const consent: Record<string, boolean> = {};
      if (form.email_consent) consent.email = true;
      if (form.wa_consent) consent.whatsapp = true;
      await api(`/brands/${b}/contacts`, { method: "POST", json: { ...form, email: form.email || null, phone: form.phone || null, consent, consent_source: "manual entry" } });
      setForm({ contact_name: "", company_name: "", email: "", phone: "", email_consent: false, wa_consent: false });
      setErr(null); reload();
    } catch (e2) { setErr((e2 as Error).message); }
  }
  async function unsub(id: number) {
    try { await api(`/brands/${b}/contacts/${id}/unsubscribe`, { method: "POST", json: {} }); reload(); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <>
      <PageHeader title="Contacts" description={`${data?.total ?? 0} contacts in this brand. Consent and unsubscribes apply to this brand only.`}
        actions={<Input className="w-64" placeholder="Search name, email, phone…" value={q} onChange={(e) => setQ(e.target.value)} />} />
      <ErrorNote error={error || err} />
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Name</TH><TH>Company</TH><TH>Email</TH><TH>Phone</TH><TH>Consent</TH><TH /></TR></THead>
            <TBody>
              {data?.items.map((c) => (
                <TR key={c.contact_id}>
                  <TD className="font-medium">{c.contact_name ?? "—"}</TD><TD>{c.company_name ?? "—"}</TD><TD className="text-xs">{c.email ?? "—"}</TD><TD className="text-xs">{c.phone ?? "—"}</TD>
                  <TD className="space-x-1">{Object.entries(c.consent).map(([ch, s]) => <Badge key={ch} tone={s === "granted" ? "green" : "red"}>{ch}</Badge>)}</TD>
                  <TD className="text-right"><Button size="sm" variant="ghost" onClick={() => unsub(c.contact_id)}>Unsubscribe</Button></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <Card className="mt-5 max-w-2xl">
        <CardHeader><CardTitle>Add contact</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><Input value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} /></Field>
            <Field label="Company"><Input value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} /></Field>
            <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Phone"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.email_consent} onChange={(e) => setForm({ ...form, email_consent: e.target.checked })} /> Email consent recorded</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.wa_consent} onChange={(e) => setForm({ ...form, wa_consent: e.target.checked })} /> WhatsApp consent recorded</label>
            <Button className="sm:w-fit">Save contact</Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
