"use client";

import { CheckCircle2, Mail, Plus, Star } from "lucide-react";
import { useEffect, useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Brand } from "@/lib/types";
import { cn } from "@/lib/utils";

type Mailbox = { id: number; address: string; role: "master" | "sub"; status: string; connection_status: string;
  verified: boolean; verified_via: string | null; google_connected: boolean; sent_today: number; daily_limit: number; last_error: string | null };

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
}

function MailboxRow({ brandId, m, google, onChange }: { brandId: number; m: Mailbox; google: boolean; onChange: (l: Mailbox[]) => void }) {
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const base = `/brands/${brandId}/mailboxes/${m.id}`;
  const run = async (fn: () => Promise<void>) => { setErr(null); setBusy(true); try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };

  const status = m.connection_status === "connected"
    ? <Badge tone="green">Connected · sending</Badge>
    : m.verified ? <Badge tone="blue">Verified · not connected</Badge> : <Badge tone="amber">Not verified</Badge>;

  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mail className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{m.address}</span>
        {m.role === "master" && <Badge tone="blue"><Star className="mr-1 h-3 w-3" />Master</Badge>}
        {status}
        {m.connection_status === "connected" && <span className="text-xs text-muted-foreground">{m.sent_today}/{m.daily_limit} today</span>}
        <div className="ml-auto flex flex-wrap gap-1">
          {m.connection_status !== "connected" && (
            <Button size="sm" disabled={!google || busy} title={google ? "" : "Google isn't set up on the server yet"}
              onClick={() => run(async () => { const r = await api<{ url: string }>(`${base}/google/start`, { method: "POST" }); window.location.href = r.url; })}>
              Connect with Google
            </Button>
          )}
          {!m.verified && !challenge && (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => run(async () => { const r = await api<{ challenge_id: string }>(`${base}/verify/start`, { method: "POST" }); setChallenge(r.challenge_id); setCode(""); })}>
              Verify with code
            </Button>
          )}
          {m.role !== "master" && (
            <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => run(async () => onChange(await api<Mailbox[]>(`${base}/make-master`, { method: "POST" })))}>Make master</Button>
          )}
          {m.connection_status === "connected" && (
            <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => run(async () => onChange(await api<Mailbox[]>(`${base}/disconnect`, { method: "POST" })))}>Disconnect</Button>
          )}
        </div>
      </div>
      {challenge && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Enter the code sent to {m.address}:</span>
          <Input className="w-32" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          <Button size="sm" disabled={code.length !== 6 || busy}
            onClick={() => run(async () => { onChange(await api<Mailbox[]>(`${base}/verify/confirm`, { method: "POST", json: { challenge_id: challenge, code } })); setChallenge(null); })}>
            Confirm
          </Button>
        </div>
      )}
      {m.last_error && <p className="mt-2 text-xs text-destructive">{m.last_error}</p>}
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
    </div>
  );
}

function BrandMailboxes({ brand, google }: { brand: Brand; google: boolean }) {
  const [list, setList] = useState<Mailbox[] | null>(null);
  const [addr, setAddr] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Mailbox[]>(`/brands/${brand.id}/mailboxes`).then(setList).catch((e) => setErr(e.message)); }, [brand.id]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api(`/brands/${brand.id}/mailboxes`, { method: "POST", json: { address: addr, role: list?.length ? "sub" : "master" } });
      setAddr("");
      setList(await api<Mailbox[]>(`/brands/${brand.id}/mailboxes`));
    } catch (e2) { setErr((e2 as Error).message); }
  }
  const connected = list?.filter((m) => m.connection_status === "connected").length ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: brand.primary_color || "#999" }} />
          {brand.name}
        </CardTitle>
        <CardDescription>
          {list ? `${list.length} mailbox${list.length === 1 ? "" : "es"} · ${connected} connected for sending` : "Loading…"} · Campaign email is sent through the master and sub mailboxes in rotation.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {list?.map((m) => <MailboxRow key={m.id} brandId={brand.id} m={m} google={google} onChange={setList} />)}
        {list?.length === 0 && <p className="text-sm text-muted-foreground">No mailboxes yet. Add the master mailbox first.</p>}
        <form onSubmit={add} className="mt-1 flex flex-wrap gap-2">
          <Input className="max-w-sm" type="email" placeholder={list?.length ? "Sub mailbox, e.g. linkwithproperty1@gmail.com" : "Master mailbox, e.g. info@yourbrand.com"}
            value={addr} onChange={(e) => setAddr(e.target.value)} required />
          <Button variant="outline"><Plus className="h-4 w-4" />Add {list?.length ? "sub" : "master"} mailbox</Button>
        </form>
        {err && <p className="text-sm text-destructive">{err}</p>}
      </CardContent>
    </Card>
  );
}

export default function BrandsPage() {
  const { me, brandId } = useApp();
  const { data, error } = usePoll<Brand[]>("/brands", 0);
  const [google, setGoogle] = useState(false);
  const [form, setForm] = useState({ name: "", master_email: "", primary_color: "#2563eb" });
  const [msg, setMsg] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api<{ google: boolean }>("/auth/providers").then((p) => setGoogle(p.google)).catch(() => {});
    const q = new URLSearchParams(window.location.search);
    if (q.get("connected")) setBanner({ ok: true, text: `${q.get("connected")} is connected and can now send.` });
    if (q.get("error")) setBanner({ ok: false, text: q.get("error") as string });
    if (q.toString()) window.history.replaceState(null, "", "/brands");
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/brands", { method: "POST", json: { name: form.name, slug: slugify(form.name), master_email: form.master_email || null, primary_color: form.primary_color } });
      setForm({ name: "", master_email: "", primary_color: "#2563eb" });
      setMsg(null);
      window.location.reload(); // refresh the brand switcher too
    } catch (err) { setMsg((err as Error).message); }
  }

  const shown = (data ?? []).filter((b) => brandId === "all" || b.id === brandId);

  return (
    <>
      <PageHeader title="Brands & mailboxes" description="Each brand has a master mailbox and sub mailboxes. Connect a mailbox with Google to send from it; verifying with a code only proves you own it." />
      {banner && (
        <div className={cn("mb-4 flex items-center gap-2 rounded-md border px-3 py-2 text-sm", banner.ok ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700" : "border-destructive/40 bg-destructive/10 text-destructive")}>
          {banner.ok && <CheckCircle2 className="h-4 w-4" />}{banner.text}
        </div>
      )}
      <ErrorNote error={error || msg} />
      {!google && <p className="mb-4 text-xs text-amber-600">&quot;Connect with Google&quot; is unavailable until the Google OAuth client is configured on the server.</p>}

      {me?.is_super_admin && (
        <Card className="mb-5">
          <CardHeader><CardTitle>Add a brand</CardTitle><CardDescription>You can add sub mailboxes after creating it.</CardDescription></CardHeader>
          <CardContent>
            <form onSubmit={create} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end">
              <Field label="Brand name"><Input value={form.name} placeholder="Property Experts Group" onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
              <Field label="Master mailbox"><Input type="email" value={form.master_email} placeholder="info@propertyexpertsgroup.com" onChange={(e) => setForm({ ...form, master_email: e.target.value })} required /></Field>
              <Field label="Colour"><Input type="color" className="w-16 p-1" value={form.primary_color} onChange={(e) => setForm({ ...form, primary_color: e.target.value })} /></Field>
              <Button>Create brand</Button>
            </form>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-4">
        {shown.map((b) => <BrandMailboxes key={b.id} brand={b} google={google} />)}
        {data && data.length === 0 && <p className="text-sm text-muted-foreground">No brands yet.</p>}
      </div>
    </>
  );
}
