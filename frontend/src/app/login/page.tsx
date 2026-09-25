"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { api, setToken } from "@/lib/api";

type Mode = "loading" | "login" | "setup" | "code";
type LoginOut = { access_token: string | null; mfa_required: boolean; challenge_id: string | null; email_hint: string | null };

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" className="h-4 w-4" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("loading");
  const [google, setGoogle] = useState(false);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [brands, setBrands] = useState(true);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Return from Google: token in the URL fragment, or a code step / error in the query string
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const q = new URLSearchParams(window.location.search);
    if (hash.get("token")) {
      setToken(hash.get("token"));
      window.location.replace("/");
      return;
    }
    if (q.get("error")) setError(q.get("error"));
    if (q.get("mfa")) {
      setChallenge(q.get("mfa"));
      setHint(q.get("hint"));
      setMode("code");
    }
    if (q.toString()) window.history.replaceState(null, "", "/login");
    api<{ google: boolean }>("/auth/providers").then((p) => setGoogle(p.google)).catch(() => {});
    if (q.get("mfa")) return;
    api<{ needs_setup: boolean }>("/auth/setup-status")
      .then((r) => setMode(r.needs_setup ? "setup" : "login"))
      .catch((e) => { setMode("login"); setError(`API unreachable: ${e.message}`); });
  }, []);

  function done(token: string) {
    setToken(token);
    window.location.href = "/";
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (mode === "setup" && password !== confirm) return setError("Passwords do not match");
    setBusy(true);
    try {
      if (mode === "setup") {
        const r = await api<{ access_token: string }>("/auth/setup", { method: "POST", json: { email, full_name: name, password, create_brands: brands } });
        return done(r.access_token);
      }
      if (mode === "code") {
        const r = await api<{ access_token: string }>("/auth/otp/verify", { method: "POST", json: { challenge_id: challenge, code } });
        return done(r.access_token);
      }
      const r = await api<LoginOut>("/auth/login", { method: "POST", json: { email, password } });
      if (r.mfa_required) {
        setChallenge(r.challenge_id);
        setHint(r.email_hint);
        setCode("");
        setMode("code");
      } else if (r.access_token) done(r.access_token);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      const r = await api<LoginOut>("/auth/otp/resend", { method: "POST", json: { challenge_id: challenge } });
      setChallenge(r.challenge_id);
      setInfo("A new code is on its way.");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const title = mode === "setup" ? "Set up Comms Hub" : mode === "code" ? "Check your email" : "Comms Hub";
  const desc = mode === "setup"
    ? "First run: create the Super Admin account. This screen disappears once an account exists."
    : mode === "code"
      ? `We sent a 6-digit code to ${hint ?? "your email"}. It expires in 10 minutes.`
      : "Sign in to manage Email, WhatsApp and SMS across your brands.";

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">{title}</CardTitle>
          <CardDescription>{desc}</CardDescription>
        </CardHeader>
        <CardContent>
          {mode === "loading" ? <p className="text-sm text-muted-foreground">Loading…</p> : (
            <>
              {mode === "login" && google && (
                <>
                  <a href="/api/auth/google/start"
                    className="flex h-9 w-full items-center justify-center gap-2 rounded-md border border-input bg-background text-sm font-medium hover:bg-accent">
                    <GoogleIcon /> Sign in with Google
                  </a>
                  <div className="my-4 flex items-center gap-3 text-xs text-muted-foreground">
                    <div className="h-px flex-1 bg-border" /> or <div className="h-px flex-1 bg-border" />
                  </div>
                </>
              )}
              <form onSubmit={submit} className="flex flex-col gap-3">
                {mode === "code" ? (
                  <Field label="6-digit code">
                    <Input inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} autoFocus
                      className="text-center text-lg tracking-[0.5em]" value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required />
                  </Field>
                ) : (
                  <>
                    {mode === "setup" && <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>}
                    <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
                    <Field label="Password">
                      <Input type="password" autoComplete={mode === "setup" ? "new-password" : "current-password"} minLength={mode === "setup" ? 10 : undefined}
                        value={password} onChange={(e) => setPassword(e.target.value)} required />
                    </Field>
                  </>
                )}
                {mode === "setup" && (
                  <>
                    <Field label="Confirm password"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></Field>
                    <label className="flex items-start gap-2 text-sm">
                      <input type="checkbox" className="mt-1" checked={brands} onChange={(e) => setBrands(e.target.checked)} />
                      <span>Create the 5 brand workspaces with demo (mock) senders. Mock senders never deliver real messages.</span>
                    </label>
                  </>
                )}
                {info && <p className="text-sm text-emerald-600">{info}</p>}
                {error && <p className="text-sm text-destructive">{error}</p>}
                <Button disabled={busy || (mode === "code" && code.length !== 6)}>
                  {busy ? "Please wait…" : mode === "setup" ? "Create admin & continue" : mode === "code" ? "Verify & sign in" : "Sign in"}
                </Button>
                {mode === "code" && (
                  <div className="flex justify-between text-xs">
                    <button type="button" className="text-primary hover:underline" onClick={resend}>Send a new code</button>
                    <button type="button" className="text-muted-foreground hover:underline" onClick={() => { setMode("login"); setError(null); }}>Back to sign in</button>
                  </div>
                )}
              </form>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
