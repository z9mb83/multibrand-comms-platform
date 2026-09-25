"use client";

import { useEffect, useState } from "react";

import { useApp, usePoll } from "@/components/app-context";
import { SecurityCard } from "@/components/security-card";
import { ErrorNote, PageHeader } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { api } from "@/lib/api";

type S = {
  master_email: string | null; master_mobile: string | null; mobile_opt_in_at: string | null; notification_channel: string;
  morning_time: string; evening_time: string; timezone: string; working_days: number[]; brand_preferences: Record<string, { include?: boolean }>;
  emergency_alerts_enabled: boolean; report_recipients: string[]; attach_spreadsheet: boolean; notification_email_sender_id: number | null;
  notification_whatsapp_sender_id: number | null; notification_sms_sender_id: number | null; whatsapp_report_template: string | null; task_carry_forward: boolean;
};
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function SettingsPage() {
  const { me, brands } = useApp();
  const { data, error } = usePoll<S>(me?.is_super_admin ? "/master/settings" : null, 0);
  const [s, setS] = useState<S | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (data) setS(data); }, [data]);
  if (me && !me.is_super_admin) return (<><PageHeader title="Settings" /><SecurityCard /></>);
  if (!s) return (<><PageHeader title="Settings" /><SecurityCard /></>);

  const set = <K extends keyof S>(k: K, v: S[K]) => setS({ ...s, [k]: v });
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try { setS(await api<S>("/master/settings", { method: "PUT", json: s })); setMsg("Saved"); setErr(null); }
    catch (e2) { setErr((e2 as Error).message); setMsg(null); }
  }

  return (
    <>
      <PageHeader title="Master Control Center" description="Reporting and notification settings for the Super Admin. Independent of marketing sender rotation." />
      <ErrorNote error={error || err} />
      <SecurityCard />
      <form onSubmit={save} className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Master contact</CardTitle></CardHeader>
          <CardContent className="grid gap-3">
            <Field label="Master email"><Input type="email" value={s.master_email ?? ""} onChange={(e) => set("master_email", e.target.value || null)} /></Field>
            <Field label="Master mobile (E.164)"><Input value={s.master_mobile ?? ""} onChange={(e) => set("master_mobile", e.target.value || null)} placeholder="+91…" /></Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!s.mobile_opt_in_at} onChange={(e) => set("mobile_opt_in_at", e.target.checked ? new Date().toISOString() : null)} />
              Master mobile has opted in to WhatsApp/SMS notifications
            </label>
            <Field label="Notification channel">
              <Select value={s.notification_channel} onChange={(e) => set("notification_channel", e.target.value)}>
                <option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="sms">SMS</option>
                <option value="email+whatsapp">Email + WhatsApp</option><option value="email+sms">Email + SMS</option>
              </Select>
            </Field>
            <Field label="Extra report recipients (comma separated)">
              <Input value={s.report_recipients.join(", ")} onChange={(e) => set("report_recipients", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
            </Field>
            <Field label="Approved WhatsApp report template name"><Input value={s.whatsapp_report_template ?? ""} onChange={(e) => set("whatsapp_report_template", e.target.value || null)} /></Field>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Schedule</CardTitle><CardDescription>Defaults: 09:30 and 18:30 Asia/Kolkata</CardDescription></CardHeader>
          <CardContent className="grid gap-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Morning report"><Input type="time" value={s.morning_time} onChange={(e) => set("morning_time", e.target.value)} /></Field>
              <Field label="Evening report"><Input type="time" value={s.evening_time} onChange={(e) => set("evening_time", e.target.value)} /></Field>
            </div>
            <Field label="Timezone"><Input value={s.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">Working days</div>
              <div className="flex flex-wrap gap-2">
                {DAYS.map((d, i) => (
                  <label key={d} className="flex items-center gap-1 text-sm">
                    <input type="checkbox" checked={s.working_days.includes(i)} onChange={(e) => set("working_days", e.target.checked ? [...s.working_days, i].sort() : s.working_days.filter((x) => x !== i))} />{d}
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.attach_spreadsheet} onChange={(e) => set("attach_spreadsheet", e.target.checked)} /> Attach spreadsheet to email reports</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.emergency_alerts_enabled} onChange={(e) => set("emergency_alerts_enabled", e.target.checked)} /> Emergency alerts</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.task_carry_forward} onChange={(e) => set("task_carry_forward", e.target.checked)} /> Carry unfinished tasks to the next working day</label>
          </CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Brands in reports</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-4">
            {brands.map((b) => {
              const inc = s.brand_preferences[String(b.id)]?.include !== false;
              return (
                <label key={b.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={inc} onChange={(e) => set("brand_preferences", { ...s.brand_preferences, [String(b.id)]: { include: e.target.checked } })} />{b.name}
                </label>
              );
            })}
          </CardContent>
        </Card>
        <div className="flex items-center gap-3 lg:col-span-2">
          <Button>Save settings</Button>
          {msg && <span className="text-sm text-emerald-600">{msg}</span>}
        </div>
      </form>
    </>
  );
}
