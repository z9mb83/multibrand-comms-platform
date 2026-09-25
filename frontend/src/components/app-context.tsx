"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, getToken } from "@/lib/api";
import type { Brand, Me } from "@/lib/types";

type Ctx = {
  me: Me | null;
  brands: Brand[];
  brandId: number | "all";
  setBrandId: (id: number | "all") => void;
  currentBrand: Brand | null;
  requireBrand: () => number | null;
};

const AppCtx = createContext<Ctx | null>(null);
const BRAND_KEY = "comms_brand";

export function AppProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [brandId, setBrandIdState] = useState<number | "all">("all");

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<Me>("/auth/me")
      .then((m) => {
        setMe(m);
        let saved: string | null = null;
        try {
          saved = window.localStorage.getItem(BRAND_KEY);
        } catch {
          /* ignore */
        }
        const savedId = saved && saved !== "all" ? Number(saved) : null;
        if (savedId && m.brands.some((b) => b.id === savedId)) setBrandIdState(savedId);
        else if (!m.is_super_admin && m.brands[0]) setBrandIdState(m.brands[0].id);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  const setBrandId = useCallback((id: number | "all") => {
    setBrandIdState(id);
    try {
      window.localStorage.setItem(BRAND_KEY, String(id));
    } catch {
      /* ignore */
    }
  }, []);

  const brands = me?.brands ?? [];
  const currentBrand = brandId === "all" ? null : brands.find((b) => b.id === brandId) ?? null;
  const requireBrand = () => (brandId === "all" ? null : brandId);

  return <AppCtx.Provider value={{ me, brands, brandId, setBrandId, currentBrand, requireBrand }}>{children}</AppCtx.Provider>;
}

export function useApp() {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside provider");
  return c;
}

/** Fetch + refresh every `intervalMs` (frequent refresh for statuses and replies). */
export function usePoll<T>(path: string | null, intervalMs = 15000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }
    let alive = true;
    const load = () =>
      api<T>(path)
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(e.message));
    load();
    const t = intervalMs ? setInterval(load, intervalMs) : undefined;
    return () => {
      alive = false;
      if (t) clearInterval(t);
    };
  }, [path, intervalMs, tick]);
  return { data, error, reload };
}
