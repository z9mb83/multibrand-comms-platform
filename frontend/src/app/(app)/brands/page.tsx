"use client";

import { CheckCircle2, Globe, Mail, Pencil, Phone, Plus, Star, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Brand } from "@/lib/types";
import { cn } from "@/lib/utils";

type Mailbox = { id: number; address: string; role: "master" | "sub"; status: string; connection_status: string; provider: string;
  verified: boolean; verified_via: string | null; google_connected: boolean; sent_today: number; daily_limit: number; last_error: string | null };
type Profile = { id: number; name: string; primary_color: string | null; master_email: string | null; description: string; phone: string;
  website: string; cc_email: string; signature_name: string; signature_title: string };

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || `brand-${Date.now()}`;
}

async function startGoogle(brandId: number, mailboxId: number) {
  const r = await api<{ url: string }>(`/brands/${brandId}/mailboxes/${mailboxId}/google/start`, { method: "POST" });
  window.location.href = r.url;
}

// ------------------------------------------------------------------ one mailbox
function MailboxRow({ brandId, m, google, canEdit, onChange }: { brandId: number; m: Mailbox; google: boolean; canEdit: boolean; onChange: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const base = `/brands/${brandId}/mailboxes/${m.id}`;
  const run = async (fn: () => Promise<void>) => { setErr(null); setBusy(true); try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const demo = m.provider === "mock";
  const status = demo ? <Badge tone="gray">Demo · can&apos;t send</Badge>
    : m.connection_status === "connected" ? <Badge tone="green">Connected · sending</Badge>
      : <Badge tone="amber">Not connected</Badge>;

  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mail className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{m.address}</span>
        {m.role === "master" && <Badge tone="blue"><Star className="mr-1 h-3 w-3" />Master</Badge>}
        {status}
        {m.connection_status === "connected" && !demo && <span className="text-xs text-muted-foreground">{m.sent_today}/{m.daily_limit} today</span>}
        {canEdit && (
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {!demo && m.connection_status !== "connected" && (
              <Button size="sm" disabled={!google || busy} title={google ? "" : "Google isn't set up on the server yet"}
                onClick={() => run(() => startGoogle(brandId, m.id))}>Connect with Google</Button>
            )}
            {m.role !== "master" && !demo && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(async () => { await api(`${base}/make-master`, { method: "POST" }); onChange(); })}>Make master</Button>
            )}
            {m.connection_status === "connected" && !demo && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(async () => { await api(`${base}/disconnect`, { method: "POST" }); onChange(); })}>Disconnect</Button>
            )}
            {confirm ? (
              <>
                <Button size="sm" variant="destructive" disabled={busy} onClick={() => run(async () => { await api(`/brands/${brandId}/senders/${m.id}`, { method: "DELETE" }); onChange(); })}>Remove</Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
              </>
            ) : (
              <button className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" title="Remove mailbox"
                aria-label={`Remove ${m.address}`} onClick={() => setConfirm(true)}><Trash2 className="h-4 w-4" /></button>
            )}
          </div>
        )}
      </div>
      {m.last_error && !demo && <p className="mt-2 text-xs text-destructive">{m.last_error}</p>}
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ brand profile form (create + edit)
type Form = { name: string; master_email: string; description: string; phone: string; website: string; signature_name: string; signature_title: string; primary_color: string };
const EMPTY: Form = { name: "", master_email: "", description: "", phone: "", website: "", signature_name: "", signature_title: "", primary_color: "#2563eb" };

function ProfileFields({ f, set, creating }: { f: Form; set: (f: Form) => void; creating: boolean }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Brand name *"><Input value={f.name} placeholder="Property Experts Group" onChange={(e) => set({ ...f, name: e.target.value })} required /></Field>
      {creating
        ? <Field label="Master mailbox * (the brand's main Gmail / Google Workspace address)"><Input type="email" value={f.master_email} placeholder="info@propertyexpertsgroup.com" onChange={(e) => set({ ...f, master_email: e.target.value })} required /></Field>
        : <Field label="CC on outreach emails"><Input type="email" value={f.master_email} onChange={(e) => set({ ...f, master_email: e.target.value })} /></Field>}
      <Field label="What the brand does * (the AI may only use these facts)" className="md:col-span-2">
        <Textarea value={f.description} required rows={3} onChange={(e) => set({ ...f, description: e.target.value })}
          placeholder="Property Experts Group helps buyers, sellers and investors with property discovery, project guidance, site visits and real-estate consultation in Chandigarh and Mohali." />
      </Field>
      <Field label="Phone"><Input value={f.phone} placeholder="+91 80808 03736" onChange={(e) => set({ ...f, phone: e.target.value })} /></Field>
      <Field label="Website"><Input value={f.website} placeholder="https://propertyexpertsgroup.com" onChange={(e) => set({ ...f, website: e.target.value })} /></Field>
      <Field label="Signature name"><Input value={f.signature_name} placeholder="Ajay Veer" onChange={(e) => set({ ...f, signature_name: e.target.value })} /></Field>
      <Field label="Signature title"><Input value={f.signature_title} placeholder="Sales Executive" onChange={(e) => set({ ...f, signature_title: e.target.value })} /></Field>
      <Field label="Colour"><Input type="color" className="w-16 p-1" value={f.primary_color} onChange={(e) => set({ ...f, primary_color: e.target.value })} /></Field>
    </div>
  );
}

function NewBrand({ google, onCancel }: { google: boolean; onCancel: () => void }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const create = async (connect: boolean) => {
    setErr(null); setBusy(true);
    try {
      const b = await api<{ id: number }>("/brands", { method: "POST", json: {
        name: f.name.trim(), slug: slugify(f.name), master_email: f.master_email.trim(), primary_color: f.primary_color,
        description: f.description, phone: f.phone, website: f.website,
        signature_name: f.signature_name || null, signature_title: f.signature_title || null } });
      if (connect) {
        const boxes = await api<Mailbox[]>(`/brands/${b.id}/mailboxes`);
        const master = boxes.find((m) => m.role === "master");
        if (master) { await startGoogle(b.id, master.id); return; }
      }
      window.location.href = `/brands?created=${encodeURIComponent(f.name.trim())}`;
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  return (
    <Card className="mb-5">
      <CardHeader><CardTitle>New brand</CardTitle>
        <CardDescription>Fill in the brand&apos;s details, then connect its master mailbox with Google so email can be sent from it. You can add sub mailboxes afterwards.</CardDescription></CardHeader>
      <CardContent>
        <form onSubmit={(e) => { e.preventDefault(); create(google); }} className="grid gap-4">
          <ProfileFields f={f} set={setF} creating />
          <ErrorNote error={err} />
          <div className="flex flex-wrap gap-2">
            {google && <Button disabled={busy}>{busy ? "Creating…" : "Create brand & connect master mailbox with Google"}</Button>}
            <Button type="button" variant={google ? "outline" : "default"} disabled={busy} onClick={(e) => {
              const form = (e.currentTarget as HTMLButtonElement).form;
              if (form && !form.reportValidity()) return;
              create(false);
            }}>Create brand only</Button>
            <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ one brand
function BrandCard({ brand, google, isSuper }: { brand: Brand; google: boolean; isSuper: boolean }) {
  const [list, setList] = useState<Mailbox[] | null>(null);
  const [p, setP] = useState<Profile | null>(null);
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState<Form>(EMPTY);
  const [addr, setAddr] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = isSuper || brand.role === "brand_admin";
  const load = () => {
    api<Mailbox[]>(`/brands/${brand.id}/mailboxes`).then(setList).catch((e) => setErr(e.message));
    api<Profile>(`/brands/${brand.id}/profile`).then(setP).catch(() => {});
  };
  useEffect(load, [brand.id]);
  const run = async (fn: () => Promise<void>) => { setErr(null); setBusy(true); try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };

  const startEdit = () => {
    if (!p) return;
    setF({ name: p.name, master_email: p.cc_email, description: p.description, phone: p.phone, website: p.website,
      signature_name: p.signature_name, signature_title: p.signature_title, primary_color: p.primary_color || "#2563eb" });
    setEditing(true);
  };
  const saveEdit = () => run(async () => {
    setP(await api<Profile>(`/brands/${brand.id}/profile`, { method: "PUT", json: {
      name: f.name, primary_color: f.primary_color, cc_email: f.master_email, description: f.description, phone: f.phone,
      website: f.website, signature_name: f.signature_name, signature_title: f.signature_title } }));
    setEditing(false);
    if (f.name !== brand.name) window.location.reload();
  });
  const addBox = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const r = await api<{ id: number }>(`/brands/${brand.id}/mailboxes`, { method: "POST", json: { address: addr, role: hasMaster ? "sub" : "master" } });
    setAddr("");
    if (google) await startGoogle(brand.id, r.id); else load();
  }); };
  const demoCount = list?.filter((m) => m.provider === "mock").length ?? 0;
  const connected = list?.filter((m) => m.connection_status === "connected" && m.provider !== "mock").length ?? 0;
  const hasMaster = list?.some((m) => m.role === "master" && m.provider !== "mock");

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: p?.primary_color || brand.primary_color || "#999" }} />
              {p?.name ?? brand.name}
            </CardTitle>
            <CardDescription className="mt-1">
              {p?.description ? <span className="line-clamp-2">{p.description}</span> : canEdit && <span className="text-amber-600">Add what this brand does (Edit) so AI emails can describe it.</span>}
            </CardDescription>
            <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
              {p?.phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{p.phone}</span>}
              {p?.website && <span className="flex items-center gap-1"><Globe className="h-3 w-3" />{p.website.replace(/^https?:\/\//, "")}</span>}
              <span>{list ? `${connected} of ${list.length - demoCount} mailbox${list.length - demoCount === 1 ? "" : "es"} connected` : "Loading…"}</span>
            </div>
          </div>
          {canEdit && !editing && <Button size="sm" variant="outline" onClick={startEdit}><Pencil className="h-4 w-4" />Edit</Button>}
          {isSuper && !editing && <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setDeleting((v) => !v)}><Trash2 className="h-4 w-4" />Delete</Button>}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {deleting && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm">
            <p className="mb-2">This permanently deletes <b>{brand.name}</b> with all its mailboxes, leads, contacts, campaigns and messages. Type the brand name to confirm.</p>
            <div className="flex flex-wrap gap-2">
              <Input className="max-w-xs" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={brand.name} />
              <Button variant="destructive" disabled={busy || typed !== brand.name} onClick={() => run(async () => {
                await api(`/brands/${brand.id}/delete`, { method: "POST", json: { confirm_name: typed } }); window.location.reload();
              })}>Delete brand</Button>
              <Button variant="ghost" onClick={() => { setDeleting(false); setTyped(""); }}>Cancel</Button>
            </div>
          </div>
        )}
        {editing && (
          <div className="mb-2 rounded-md border p-3">
            <ProfileFields f={f} set={setF} creating={false} />
            <div className="mt-3 flex gap-2">
              <Button disabled={busy} onClick={saveEdit}>Save</Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          </div>
        )}
        {list?.filter((m) => m.provider !== "mock").map((m) => <MailboxRow key={m.id} brandId={brand.id} m={m} google={google} canEdit={canEdit} onChange={load} />)}
        {demoCount > 0 && canEdit && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed p-3 text-xs text-muted-foreground">
            {demoCount} demo mailbox{demoCount === 1 ? "" : "es"} (sample data that can&apos;t send) are hidden here.
            <Button size="sm" variant="outline" disabled={busy} onClick={() => run(async () => {
              for (const m of list!.filter((x) => x.provider === "mock")) await api(`/brands/${brand.id}/senders/${m.id}`, { method: "DELETE" });
              load();
            })}>Remove demo mailboxes</Button>
          </div>
        )}
        {list && list.length - demoCount === 0 && <p className="text-sm text-muted-foreground">No mailboxes yet. Add the brand&apos;s master mailbox first.</p>}
        {canEdit && (
          <form onSubmit={addBox} className="mt-1 flex flex-wrap gap-2">
            <Input className="max-w-sm" type="email" placeholder={hasMaster ? "Sub mailbox, e.g. linkwithproperty1@gmail.com" : "Master mailbox, e.g. info@yourbrand.com"}
              value={addr} onChange={(e) => setAddr(e.target.value)} required />
            <Button variant="outline" disabled={busy}><Plus className="h-4 w-4" />Add {hasMaster ? "sub" : "master"} mailbox{google ? " & connect with Google" : ""}</Button>
          </form>
        )}
        {err && <p className="text-sm text-destructive">{err}</p>}
      </CardContent>
    </Card>
  );
}

export default function BrandsPage() {
  const { me, brandId } = useApp();
  const { data, error } = usePoll<Brand[]>("/brands", 0);
  const [google, setGoogle] = useState(false);
  const [adding, setAdding] = useState(false);
  const [banner, setBanner] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api<{ google: boolean }>("/auth/providers").then((p) => setGoogle(p.google)).catch(() => {});
    const q = new URLSearchParams(window.location.search);
    if (q.get("connected")) setBanner({ ok: true, text: `${q.get("connected")} is connected with Google and can now send.` });
    if (q.get("created")) setBanner({ ok: true, text: `${q.get("created")} was created.` });
    if (q.get("error")) setBanner({ ok: false, text: q.get("error") as string });
    if (q.toString()) window.history.replaceState(null, "", "/brands");
  }, []);

  const roles = new Map((me?.brands ?? []).map((b) => [b.id, b.role]));
  const shown = (data ?? []).filter((b) => brandId === "all" || b.id === brandId).map((b) => ({ ...b, role: roles.get(b.id) }));

  return (
    <>
      <PageHeader title="Brands & Mailboxes"
        description="Each brand has a profile, a master mailbox and optional sub mailboxes. Emails are sent from mailboxes connected with Google."
        actions={me?.is_super_admin && !adding ? <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />New brand</Button> : undefined} />
      {banner && (
        <div className={cn("mb-4 flex items-center gap-2 rounded-md border px-3 py-2 text-sm", banner.ok ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "border-destructive/40 bg-destructive/10 text-destructive")}>
          {banner.ok && <CheckCircle2 className="h-4 w-4" />}{banner.text}
        </div>
      )}
      <ErrorNote error={error} />
      {!google && <p className="mb-4 text-xs text-amber-600">&quot;Connect with Google&quot; is unavailable until the Google OAuth client is configured on the server.</p>}
      {adding && <NewBrand google={google} onCancel={() => setAdding(false)} />}
      <div className="flex flex-col gap-4">
        {shown.map((b) => <BrandCard key={b.id} brand={b} google={google} isSuper={!!me?.is_super_admin} />)}
        {data && data.length === 0 && !adding && (
          <div className="rounded-lg border border-dashed p-10 text-center">
            <h2 className="text-base font-semibold">No brands yet</h2>
            <p className="mt-1 text-sm text-muted-foreground">Create your first brand, then connect its master mailbox with Google.</p>
            {me?.is_super_admin && <Button className="mt-4" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />New brand</Button>}
          </div>
        )}
      </div>
    </>
  );
}
