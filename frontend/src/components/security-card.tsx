"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type Me = { email: string; mfa_email_enabled: boolean; google_linked: boolean };

export function SecurityCard() {
  const [me, setMe] = useState<Me | null>(null);
  const [providers, setProviders] = useState<{ google: boolean; email_codes_available: boolean } | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [action, setAction] = useState<"enable" | "disable">("enable");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Me>("/auth/me").then(setMe).catch(() => {});
    api<{ google: boolean; email_codes_available: boolean }>("/auth/providers").then(setProviders).catch(() => {});
  }, []);

  async function start(a: "enable" | "disable") {
    setErr(null); setMsg(null);
    try {
      const r = await api<{ challenge_id: string; email_hint: string }>("/auth/mfa/email/start", { method: "POST", json: { action: a } });
      setAction(a); setChallenge(r.challenge_id); setCode("");
      setMsg(`We sent a code to ${r.email_hint}.`);
    } catch (e) { setErr((e as Error).message); }
  }
  async function confirm() {
    setErr(null);
    try {
      const r = await api<Me>("/auth/mfa/email/confirm", { method: "POST", json: { challenge_id: challenge, code } });
      setMe(r); setChallenge(null);
      setMsg(r.mfa_email_enabled ? "Email codes are on. You'll be asked for a code each time you sign in." : "Email codes are off.");
    } catch (e) { setErr((e as Error).message); }
  }
  async function unlink() {
    try { setMe(await api<Me>("/auth/google/unlink", { method: "POST" })); setMsg("Google account unlinked."); }
    catch (e) { setErr((e as Error).message); }
  }

  if (!me) return null;
  return (
    <Card className="mb-5">
      <CardHeader>
        <CardTitle>My security</CardTitle>
        <CardDescription>Settings for your own account ({me.email}).</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium">Email sign-in codes</div>
            <Badge tone={me.mfa_email_enabled ? "green" : "gray"}>{me.mfa_email_enabled ? "On" : "Off"}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">After your password or Google sign-in, enter a 6-digit code sent to your email.</p>
          {challenge ? (
            <div className="mt-3 flex gap-2">
              <Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code" value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              <Button disabled={code.length !== 6} onClick={confirm}>{action === "enable" ? "Turn on" : "Turn off"}</Button>
            </div>
          ) : me.mfa_email_enabled ? (
            <Button className="mt-3" variant="outline" size="sm" onClick={() => start("disable")}>Turn off…</Button>
          ) : (
            <Button className="mt-3" size="sm" disabled={providers?.email_codes_available === false} onClick={() => start("enable")}>Turn on…</Button>
          )}
          {providers?.email_codes_available === false && !me.mfa_email_enabled && (
            <p className="mt-2 text-xs text-amber-600">Email delivery isn&apos;t configured on the server yet, so codes can&apos;t be turned on.</p>
          )}
        </div>
        <div className="rounded-md border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium">Sign in with Google</div>
            <Badge tone={me.google_linked ? "green" : "gray"}>{me.google_linked ? "Linked" : "Not linked"}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {providers?.google
              ? "Use “Sign in with Google” on the sign-in page with the Google account that matches your email. It links automatically the first time."
              : "Not configured on the server yet."}
          </p>
          {me.google_linked && <Button className="mt-3" variant="outline" size="sm" onClick={unlink}>Unlink Google</Button>}
        </div>
        {msg && <p className="text-sm text-emerald-600 md:col-span-2">{msg}</p>}
        {err && <p className="text-sm text-destructive md:col-span-2">{err}</p>}
      </CardContent>
    </Card>
  );
}
