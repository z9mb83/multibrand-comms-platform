"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { api } from "@/lib/api";

type State = { selected_id: number | null; working: boolean; env_mailer: string | null; options: { id: number; address: string }[] };

/** Super Admin: pick the Google-connected mailbox that sends sign-up and sign-in codes. */
export function CodeSenderCard() {
  const [s, setS] = useState<State | null>(null);
  const [pick, setPick] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    api<State>("/auth/code-sender").then((x) => { setS(x); setPick(x.selected_id ? String(x.selected_id) : ""); }).catch((e) => setErr(e.message));
  }, []);
  if (!s) return err ? <p className="text-sm text-destructive">{err}</p> : null;
  const save = async () => {
    setErr(null); setMsg(null);
    try {
      const x = await api<State>("/auth/code-sender", { method: "PUT", json: { sender_id: pick ? Number(pick) : null } });
      setS(x); setMsg("Saved");
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">Sign-in codes
          <Badge tone={s.working ? "green" : "amber"}>{s.working ? "Email codes working" : "Not set up"}</Badge></CardTitle>
        <CardDescription>
          The 6-digit codes for sign-up and sign-in are emailed from this mailbox. It must be connected with Google on Brands &amp; Mailboxes.
          {s.env_mailer && ` The server also has ${s.env_mailer.toUpperCase()} configured, which is used first.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-2">
        {s.options.length ? (
          <>
            <Select className="w-80" value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">— not set —</option>
              {s.options.map((o) => <option key={o.id} value={o.id}>{o.address}</option>)}
            </Select>
            <Button onClick={save}>Save</Button>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">No mailbox is connected with Google yet. Open Brands &amp; Mailboxes and click Connect with Google on the mailbox that should send the codes (for example linkwithmarketing@gmail.com).</p>
        )}
        {msg && <span className="text-sm text-emerald-600">{msg}</span>}
        {err && <span className="text-sm text-destructive">{err}</span>}
      </CardContent>
    </Card>
  );
}
