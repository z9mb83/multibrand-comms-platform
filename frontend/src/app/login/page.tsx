"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { api, setToken } from "@/lib/api";

type Step = "loading" | "start" | "code" | "password" | "mfa" | "setup-password";
type Providers = { google: boolean; email_codes_available: boolean; needs_owner: boolean };
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
  const [step, setStep] = useState<Step>("loading");
  const [prov, setProv] = useState<Providers>({ google: false, email_codes_available: false, needs_owner: false });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const q = new URLSearchParams(window.location.search);
    if (hash.get("token")) {
      setToken(hash.get("token"));
      window.location.replace("/");
      return;
    }
    if (q.get("error")) setError(q.get("error"));
    const mfa = q.get("mfa");
    if (q.toString()) window.history.replaceState(null, "", "/login");
    api<Providers>("/auth/providers")
      .then((p) => {
        setProv(p);
        if (mfa) { setChallenge(mfa); setHint(q.get("hint")); setStep("mfa"); }
        else if (p.needs_owner && !p.google && !p.email_codes_available) setStep("setup-password");
        else if (!p.google && !p.email_codes_available) setStep("password");
        else setStep("start");
      })
      .catch((e) => { setStep("start"); setError(`Can't reach the server: ${e.message}`); });
  }, []);

  const done = (token: string) => { setToken(token); window.location.href = "/"; };
  const run = async (fn: () => Promise<void>) => {
    setError(null); setInfo(null); setBusy(true);
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const sendCode = () => run(async () => {
    const r = await api<LoginOut>("/auth/email/start", { method: "POST", json: { email } });
    setChallenge(r.challenge_id); setHint(r.email_hint); setCode(""); setStep("code");
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step === "start") return sendCode();
    if (step === "code") return run(async () => {
      const r = await api<{ access_token: string }>("/auth/email/verify", { method: "POST", json: { challenge_id: challenge, code, full_name: name || null } });
      done(r.access_token);
    });
    if (step === "mfa") return run(async () => {
      const r = await api<{ access_token: string }>("/auth/otp/verify", { method: "POST", json: { challenge_id: challenge, code } });
      done(r.access_token);
    });
    if (step === "password") return run(async () => {
      const r = await api<LoginOut>("/auth/login", { method: "POST", json: { email, password } });
      if (r.mfa_required) { setChallenge(r.challenge_id); setHint(r.email_hint); setCode(""); setStep("mfa"); }
      else if (r.access_token) done(r.access_token);
    });
    if (step === "setup-password") return run(async () => {
      if (password !== confirm) throw new Error("Passwords do not match");
      const r = await api<{ access_token: string }>("/auth/setup", { method: "POST", json: { email, full_name: name, password, create_brands: false } });
      done(r.access_token);
    });
  }

  const resend = () => run(async () => {
    if (step === "code") return sendCode();
    const r = await api<LoginOut>("/auth/otp/resend", { method: "POST", json: { challenge_id: challenge } });
    setChallenge(r.challenge_id); setInfo("A new code is on its way.");
  });

  const owner = prov.needs_owner;
  const title = step === "code" || step === "mfa" ? "Check your email" : owner ? "Create the owner account" : "Sign in to Comms Hub";
  const desc = step === "code" || step === "mfa"
    ? `We sent a 6-digit code to ${hint ?? "your email"}. It expires in 10 minutes.`
    : owner
      ? "You're the first person here, so this account will own the brand group (Super Admin)."
      : "Manage Email, WhatsApp and SMS across your brands.";
  const codeInput = (
    <Field label="6-digit code">
      <Input inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} autoFocus
        className="text-center text-lg tracking-[0.5em]" value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required />
    </Field>
  );

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">{title}</CardTitle>
          <CardDescription>{desc}</CardDescription>
        </CardHeader>
        <CardContent>
          {step === "loading" ? <p className="text-sm text-muted-foreground">Loading…</p> : (
            <form onSubmit={submit} className="flex flex-col gap-3">
              {step === "start" && (
                <>
                  {prov.google && (
                    <a href="/api/auth/google/start"
                      className="flex h-10 w-full items-center justify-center gap-2 rounded-md border border-input bg-background text-sm font-medium hover:bg-accent">
                      <GoogleIcon /> {owner ? "Sign up with Google" : "Continue with Google"}
                    </a>
                  )}
                  {prov.google && prov.email_codes_available && (
                    <div className="flex items-center gap-3 text-xs text-muted-foreground"><div className="h-px flex-1 bg-border" /> or <div className="h-px flex-1 bg-border" /></div>
                  )}
                  {prov.email_codes_available ? (
                    <>
                      <Field label="Email"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
                      <Button disabled={busy}>{busy ? "Sending…" : "Email me a code"}</Button>
                    </>
                  ) : (
                    !prov.google && <p className="text-sm text-muted-foreground">Email codes and Google aren&apos;t set up on the server yet.</p>
                  )}
                  {!owner && (
                    <button type="button" className="text-center text-xs text-muted-foreground hover:underline" onClick={() => { setStep("password"); setError(null); }}>
                      Sign in with a password instead
                    </button>
                  )}
                </>
              )}

              {step === "code" && (
                <>
                  {owner && <Field label="Your name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>}
                  {codeInput}
                </>
              )}
              {step === "mfa" && codeInput}

              {(step === "password" || step === "setup-password") && (
                <>
                  {step === "setup-password" && <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>}
                  <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
                  <Field label="Password">
                    <Input type="password" autoComplete={step === "password" ? "current-password" : "new-password"} minLength={step === "password" ? undefined : 10}
                      value={password} onChange={(e) => setPassword(e.target.value)} required />
                  </Field>
                  {step === "setup-password" && (
                    <Field label="Confirm password"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></Field>
                  )}
                </>
              )}

              {info && <p className="text-sm text-emerald-600">{info}</p>}
              {error && <p className="text-sm text-destructive">{error}</p>}

              {step !== "start" && (
                <Button disabled={busy || ((step === "code" || step === "mfa") && code.length !== 6)}>
                  {busy ? "Please wait…" : step === "password" ? "Sign in" : step === "setup-password" ? "Create owner account" : owner ? "Verify & create account" : "Verify & sign in"}
                </Button>
              )}
              {(step === "code" || step === "mfa") && (
                <div className="flex justify-between text-xs">
                  <button type="button" className="text-primary hover:underline" onClick={resend}>Send a new code</button>
                  <button type="button" className="text-muted-foreground hover:underline" onClick={() => { setStep("start"); setError(null); }}>Back</button>
                </div>
              )}
              {step === "password" && (
                <button type="button" className="text-center text-xs text-muted-foreground hover:underline" onClick={() => { setStep("start"); setError(null); }}>
                  Back to other sign-in options
                </button>
              )}
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
