"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Brand } from "@/lib/types";

export default function BrandsPage() {
  const { me, setBrandId } = useApp();
  const { data, error, reload } = usePoll<Brand[]>("/brands", 0);
  const [form, setForm] = useState({ slug: "", name: "", primary_color: "#2563eb" });
  const [msg, setMsg] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/brands", { method: "POST", json: form });
      setForm({ slug: "", name: "", primary_color: "#2563eb" });
      setMsg(null);
      reload();
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <>
      <PageHeader title="Brands" description="Each brand is an isolated workspace with its own contacts, senders, templates, campaigns and reports." />
      <ErrorNote error={error || msg} />
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Brand</TH><TH>Slug</TH><TH>Status</TH><TH>Timezone</TH><TH /></TR></THead>
            <TBody>
              {data?.map((b) => (
                <TR key={b.id}>
                  <TD><span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: b.primary_color || "#999" }} />{b.name}</TD>
                  <TD className="font-mono text-xs">{b.slug}</TD>
                  <TD><Badge>{b.status}</Badge></TD>
                  <TD>{b.timezone}</TD>
                  <TD className="text-right"><Button size="sm" variant="outline" onClick={() => setBrandId(b.id)}>Open workspace</Button></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {me?.is_super_admin && (
        <Card className="mt-5 max-w-xl">
          <CardHeader><CardTitle>Add brand</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={create} className="grid gap-3 sm:grid-cols-3">
              <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
              <Field label="Slug"><Input value={form.slug} pattern="[a-z0-9-]{2,64}" onChange={(e) => setForm({ ...form, slug: e.target.value })} required /></Field>
              <Field label="Colour"><Input type="color" value={form.primary_color} onChange={(e) => setForm({ ...form, primary_color: e.target.value })} /></Field>
              <Button className="sm:col-span-3 sm:w-fit">Create brand</Button>
            </form>
          </CardContent>
        </Card>
      )}
    </>
  );
}
