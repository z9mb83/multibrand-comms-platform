"use client";

import { useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";
import { fmtDate } from "@/lib/utils";

const STATUSES = ["Pending", "In Progress", "Completed", "Blocked", "Overdue", "Cancelled"];

export default function TasksPage() {
  const { brandId, brands } = useApp();
  const [mine, setMine] = useState(false);
  const [status, setStatus] = useState("");
  const path = brandId === "all" ? `/tasks?mine=${mine}${status ? `&status=${encodeURIComponent(status)}` : ""}`
    : `/brands/${brandId}/tasks${status ? `?status=${encodeURIComponent(status)}` : ""}`;
  const { data, error, reload } = usePoll<Task[]>(path, 20000);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ description: "", company_name: "", priority: "medium", due_at: "" });
  const brandName = (id: number) => brands.find((b) => b.id === id)?.name ?? id;

  async function setTaskStatus(t: Task, s: string) {
    try { await api(`/tasks/${t.id}`, { method: "PATCH", json: { status: s } }); reload(); } catch (e) { setErr((e as Error).message); }
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (brandId === "all") return;
    try {
      await api(`/brands/${brandId}/tasks`, { method: "POST", json: { ...form, due_at: form.due_at ? new Date(form.due_at).toISOString() : null, company_name: form.company_name || null } });
      setForm({ description: "", company_name: "", priority: "medium", due_at: "" }); reload();
    } catch (e2) { setErr((e2 as Error).message); }
  }

  return (
    <>
      <PageHeader title="Daily Tasks" description="Work only counts as completed when recorded by the system or confirmed here by an authorised user."
        actions={<>
          {brandId === "all" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>}
          <Select className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}</Select>
        </>} />
      <ErrorNote error={error || err} />
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>ID</TH><TH>Brand</TH><TH>Task</TH><TH>Priority</TH><TH>Due</TH><TH>Status</TH><TH>Update</TH></TR></THead>
            <TBody>
              {data?.map((t) => (
                <TR key={t.id}>
                  <TD className="font-mono text-xs">{t.task_code}</TD><TD>{brandName(t.brand_id)}</TD>
                  <TD><div>{t.description}</div><div className="text-xs text-muted-foreground">{t.company_name}{t.carry_count ? ` · carried ${t.carry_count}×` : ""}{t.source !== "manual" ? ` · ${t.source}` : ""}</div></TD>
                  <TD><Badge tone={t.priority === "high" || t.priority === "urgent" ? "red" : "gray"}>{t.priority}</Badge></TD>
                  <TD className="text-xs">{fmtDate(t.due_at)}</TD>
                  <TD><Badge>{t.status}</Badge>{t.completed_at && <div className="text-xs text-muted-foreground">{fmtDate(t.completed_at)}</div>}</TD>
                  <TD><Select className="h-8 w-36 text-xs" value={t.status} onChange={(e) => setTaskStatus(t, e.target.value)}>{STATUSES.map((s) => <option key={s}>{s}</option>)}</Select></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {brandId !== "all" && (
        <Card className="mt-5 max-w-2xl">
          <CardHeader><CardTitle>Add activity</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
              <Field label="Description" className="sm:col-span-2"><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} required /></Field>
              <Field label="Company"><Input value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} /></Field>
              <Field label="Priority"><Select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option>low</option><option>medium</option><option>high</option><option>urgent</option></Select></Field>
              <Field label="Due"><Input type="datetime-local" value={form.due_at} onChange={(e) => setForm({ ...form, due_at: e.target.value })} /></Field>
              <Button className="self-end sm:w-fit">Add task</Button>
            </form>
          </CardContent>
        </Card>
      )}
    </>
  );
}
