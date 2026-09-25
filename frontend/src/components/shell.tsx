"use client";

import {
  BarChart3, Building2, CheckSquare, ClipboardList, FileSpreadsheet, Inbox, LayoutDashboard, LogOut, Mail, Menu,
  MessageCircle, Megaphone, PenSquare, Repeat, Settings, Smartphone, Users, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useApp } from "@/components/app-context";
import { Select } from "@/components/ui/input";
import { setToken } from "@/lib/api";
import { cn } from "@/lib/utils";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/brands", label: "Brands", icon: Building2 },
  { href: "/contacts", label: "Contacts", icon: Users },
  { href: "/import", label: "Excel Import", icon: FileSpreadsheet },
  { href: "/compose", label: "Compose", icon: PenSquare },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/accounts/email", label: "Email Accounts", icon: Mail },
  { href: "/accounts/whatsapp", label: "WhatsApp Accounts", icon: MessageCircle },
  { href: "/accounts/sms", label: "SMS Accounts", icon: Smartphone },
  { href: "/inbox", label: "Unified Inbox", icon: Inbox },
  { href: "/follow-ups", label: "Follow-ups", icon: Repeat },
  { href: "/tasks", label: "Daily Tasks", icon: CheckSquare },
  { href: "/reports", label: "Master Reports", icon: ClipboardList },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { me, brands, brandId, setBrandId } = useApp();
  const [open, setOpen] = useState(false);

  const nav = (
    <nav className="flex flex-col gap-0.5 p-2">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} onClick={() => setOpen(false)}
            className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
              active ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 overflow-y-auto border-r bg-card lg:block">
        <div className="flex h-14 items-center border-b px-4 font-semibold">Comms Hub</div>
        {nav}
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute left-0 top-0 h-full w-64 overflow-y-auto bg-card shadow-xl">
            <div className="flex h-14 items-center justify-between border-b px-4 font-semibold">
              Comms Hub
              <button aria-label="Close menu" onClick={() => setOpen(false)}><X className="h-5 w-5" /></button>
            </div>
            {nav}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur">
          <button className="lg:hidden" aria-label="Open menu" onClick={() => setOpen(true)}><Menu className="h-5 w-5" /></button>
          <Select aria-label="Brand" className="w-48 sm:w-64" value={String(brandId)}
            onChange={(e) => setBrandId(e.target.value === "all" ? "all" : Number(e.target.value))}>
            {me?.is_super_admin && <option value="all">All Brands</option>}
            {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </Select>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-muted-foreground sm:inline">{me?.email}{me?.is_super_admin ? " · Super Admin" : ""}</span>
            <button aria-label="Sign out" title="Sign out" onClick={() => { setToken(null); window.location.href = "/login"; }}>
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function PickBrand() {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
      Select a brand in the switcher above to work in its workspace.
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>;
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}
