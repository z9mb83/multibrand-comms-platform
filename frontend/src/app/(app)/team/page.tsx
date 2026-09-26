"use client";

import { UserPlus } from "lucide-react";
import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";

type Member = { id: number; email: string; full_name: string; is_super_admin: boolean; google_linked: boolean; is_you: boolean;
  brands: { brand_id: number; brand: string | null; role: string }[] };

const ROLES = [
  { v: "brand_admin", l: "Brand admin" }, { v: "manager", l: "Manager" }, { v: "agent", l: "Agent" }, { v: "viewer", l: "Viewer" },
];
const roleLabel = (r: string) => ROLES.find((x) => x.v === r)?.l ?? r;

export default function TeamPage() {
  const { me, brands } = useApp();
  const { data, error, reload } = usePoll<Member[]>(me?.is_super_admin ? "/users/team" : null, 0);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [access, setAccess] = useState<"super" | "brand">("brand");
  const [brandId, setBrandId] = useState("");
  const [role, setRole] = useState("manager");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (me && !me.is_super_admin) {
    return (<><PageHeader title="Team" /><p className="text-sm text-muted-foreground">Only the Super Admin can manage the team.</p></>);
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setMsg(null); setBusy(true);
    try {
      const body = access === "super"
        ? { email, full_name: name, is_super_admin: true }
        : { email, full_name: name, brand_id: Number(brandId || brands[0]?.id), role };
      if (access === "brand" && !body.brand_id) throw new Error("Create a brand first");
      await api("/users", { method: "POST", json: body });
      setMsg(`${email} can now sign up on the login page (Sign up tab) with Google or an email code.`);
      setEmail(""); setName(""); reload();
    } catch (e2) { setErr((e2 as Error).message); } finally { setBusy(false); }
  }

  async function remove(m: Member) {
    setErr(null); setMsg(null);
    try { await api(`/users/${m.id}`, { method: "DELETE" }); setMsg(`${m.email} was removed.`); reload(); }
    catch (e2) { setErr((e2 as Error).message); }
  }

  return (
    <>
      <PageHeader title="Team" description="Add people to your brand group. They sign up with the exact email you enter here." />
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Add a person</CardTitle>
          <CardDescription>They get access right away and create their account from the Sign up tab on the login page.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={invite} className="grid gap-3 md:grid-cols-2">
            <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@99marketingservices.com" required /></Field>
            <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>
            <Field label="Access">
              <Select value={access} onChange={(e) => setAccess(e.target.value as "super" | "brand")}>
                <option value="brand">One brand</option>
                <option value="super">Super Admin (all brands)</option>
              </Select>
            </Field>
            {access === "brand" && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Brand">
                  <Select value={brandId} onChange={(e) => setBrandId(e.target.value)}>
                    {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                  </Select>
                </Field>
                <Field label="Role">
                  <Select value={role} onChange={(e) => setRole(e.target.value)}>
                    {ROLES.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
                  </Select>
                </Field>
              </div>
            )}
            <div className="md:col-span-2 flex items-center gap-3">
              <Button disabled={busy}><UserPlus className="mr-2 h-4 w-4" />{busy ? "Adding…" : "Add person"}</Button>
              {msg && <p className="text-sm text-emerald-600">{msg}</p>}
            </div>
          </form>
          <ErrorNote error={err} />
        </CardContent>
      </Card>

      <ErrorNote error={error} />
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Name</TH><TH>Email</TH><TH>Access</TH><TH>Google</TH><TH /></TR></THead>
            <TBody>
              {(data ?? []).map((m) => (
                <TR key={m.id}>
                  <TD className="font-medium">{m.full_name}{m.is_you && <span className="ml-1 text-xs text-muted-foreground">(you)</span>}</TD>
                  <TD>{m.email}</TD>
                  <TD>
                    {m.is_super_admin ? <Badge tone="blue">Super Admin</Badge>
                      : m.brands.length ? m.brands.map((b) => <Badge key={b.brand_id} tone="gray" className="mr-1">{b.brand} · {roleLabel(b.role)}</Badge>)
                        : <span className="text-xs text-muted-foreground">No brand yet</span>}
                  </TD>
                  <TD>{m.google_linked ? <Badge tone="green">Linked</Badge> : <span className="text-xs text-muted-foreground">Not yet</span>}</TD>
                  <TD className="text-right">
                    {!m.is_you && <Button size="sm" variant="ghost" onClick={() => remove(m)}>Remove</Button>}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
