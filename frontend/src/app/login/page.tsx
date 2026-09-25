"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const [mode, setMode] = useState<"loading" | "login" | "setup">("loading");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [brands, setBrands] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ needs_setup: boolean }>("/auth/setup-status")
      .then((r) => setMode(r.needs_setup ? "setup" : "login"))
      .catch((e) => { setMode("login"); setError(`API unreachable: ${e.message}`); });
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (mode === "setup" && password !== confirm) return setError("Passwords do not match");
    setBusy(true);
    try {
      const r = mode === "setup"
        ? await api<{ access_token: string }>("/auth/setup", { method: "POST", json: { email, full_name: name, password, create_brands: brands } })
        : await api<{ access_token: string }>("/auth/login", { method: "POST", json: { email, password } });
      setToken(r.access_token);
      window.location.href = "/";
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">{mode === "setup" ? "Set up Comms Hub" : "Comms Hub"}</CardTitle>
          <CardDescription>
            {mode === "setup"
              ? "First run: create the Super Admin account. This screen disappears once an account exists."
              : "Sign in to manage Email, WhatsApp and SMS across your brands."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {mode === "loading" ? <p className="text-sm text-muted-foreground">Loading…</p> : (
            <form onSubmit={submit} className="flex flex-col gap-3">
              {mode === "setup" && <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>}
              <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
              <Field label="Password">
                <Input type="password" autoComplete={mode === "setup" ? "new-password" : "current-password"} minLength={mode === "setup" ? 10 : undefined}
                  value={password} onChange={(e) => setPassword(e.target.value)} required />
              </Field>
              {mode === "setup" && (
                <>
                  <Field label="Confirm password"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></Field>
                  <label className="flex items-start gap-2 text-sm">
                    <input type="checkbox" className="mt-1" checked={brands} onChange={(e) => setBrands(e.target.checked)} />
                    <span>Create the 5 brand workspaces with demo (mock) senders. Mock senders never deliver real messages.</span>
                  </label>
                </>
              )}
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button disabled={busy}>{busy ? "Please wait…" : mode === "setup" ? "Create admin & continue" : "Sign in"}</Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
