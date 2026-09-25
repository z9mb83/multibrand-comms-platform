"use client";

import { CheckCircle2, Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { useApp } from "@/components/app-context";
import { ErrorNote, PageHeader, Stat } from "@/components/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, download } from "@/lib/api";
import { cn } from "@/lib/utils";

type Sheet = { name: string; columns: string[]; rows: number; detected_mapping: Record<string, string>; sample: Record<string, string>[] };
type Batch = { id: number; filename: string; status: string; sheets: Sheet[]; counts: Record<string, number | number[]>; duplicate_of_batch_id: number | null };
type Row = { id: number; sheet: string; row: number; status: string; errors: string[]; brand_id: number | null; normalized: Record<string, unknown> };

const STEPS = ["Upload", "Sheets & mapping", "Validate & preview", "Import"];

export default function ImportPage() {
  const { brands, brandId } = useApp();
  const [step, setStep] = useState(0);
  const [fields, setFields] = useState<string[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [sheets, setSheets] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [brandMode, setBrandMode] = useState<"single" | "column">("single");
  const [singleBrand, setSingleBrand] = useState<number | "">(brandId === "all" ? "" : brandId);
  const [channel, setChannel] = useState("email");
  const [routing, setRouting] = useState("AUTO");
  const [campaign, setCampaign] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [filter, setFilter] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api<{ fields: string[] }>("/imports/fields").then((f) => setFields(f.fields)).catch(() => {}); }, []);
  useEffect(() => { if (brandId !== "all") setSingleBrand(brandId); }, [brandId]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const onFile = (file: File) => run(async () => {
    const fd = new FormData();
    fd.append("file", file);
    if (brandId !== "all") fd.append("brand_id", String(brandId));
    const b = await api<Batch>("/imports/upload", { method: "POST", body: fd });
    setBatch(b);
    setSheets(b.sheets.map((s) => s.name));
    const merged: Record<string, string> = {};
    b.sheets.forEach((s) => Object.entries(s.detected_mapping).forEach(([k, v]) => { merged[k] ??= v; }));
    setMapping(merged);
    if (merged.brand && brandId === "all") setBrandMode("column");
    setStep(1);
  });

  const allColumns = Array.from(new Set(batch?.sheets.filter((s) => sheets.includes(s.name)).flatMap((s) => s.columns) ?? []));

  const preview = () => run(async () => {
    const options: Record<string, unknown> = { brand_mode: brandMode, default_channel: channel, default_routing_mode: routing };
    if (brandMode === "single") options.brand_id = singleBrand || undefined;
    if (campaign) options.campaign_name = campaign;
    if (scheduledAt) options.scheduled_at = scheduledAt;
    const clean = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
    const r = await api<{ batch: Batch }>(`/imports/${batch!.id}/preview`, { method: "POST", json: { sheets, mapping: clean, options } });
    setBatch(r.batch);
    setRows(await api<Row[]>(`/imports/${batch!.id}/rows?limit=500`));
    setStep(2);
  });

  const commit = (allowDuplicate = false) => run(async () => {
    const r = await api<{ counts: Record<string, unknown>; note: string }>(`/imports/${batch!.id}/commit`, { method: "POST", json: { allow_duplicate_file: allowDuplicate } });
    setResult({ ...r.counts, note: r.note });
    setStep(3);
  });

  const c = (batch?.counts ?? {}) as Record<string, number>;
  const shown = rows.filter((r) => !filter || r.status === filter);
  const brandName = (id: number | null) => brands.find((b) => b.id === id)?.name ?? "—";

  return (
    <>
      <PageHeader title="Excel Import" description="XLSX, XLS or CSV. Importing never sends anything: messages become drafts in a campaign that must be approved." />
      <ol className="mb-5 flex flex-wrap gap-2 text-xs">
        {STEPS.map((s, i) => (
          <li key={s} className={cn("rounded-full border px-3 py-1", i === step ? "border-primary bg-primary/10 text-primary" : i < step ? "text-foreground" : "text-muted-foreground")}>{i + 1}. {s}</li>
        ))}
      </ol>
      <ErrorNote error={err} />

      {step === 0 && (
        <Card>
          <CardContent className="p-6">
            <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-10 text-center hover:bg-muted/40">
              <Upload className="h-8 w-8 text-muted-foreground" />
              <span className="font-medium">{busy ? "Reading workbook…" : "Choose a workbook"}</span>
              <span className="text-xs text-muted-foreground">Multiple sheets and multiple brands per file are supported · 20 MB max</span>
              <input type="file" className="hidden" accept=".xlsx,.xls,.csv" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
            </label>
          </CardContent>
        </Card>
      )}

      {step === 1 && batch && (
        <div className="grid gap-5 lg:grid-cols-3">
          <Card className="lg:col-span-1">
            <CardHeader><CardTitle>{batch.filename}</CardTitle>
              {batch.duplicate_of_batch_id && <CardDescription className="text-amber-600">This exact file was already imported (batch #{batch.duplicate_of_batch_id}). Duplicate rows will be skipped.</CardDescription>}
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">Worksheets</div>
                {batch.sheets.map((s) => (
                  <label key={s.name} className="flex items-center gap-2 py-1 text-sm">
                    <input type="checkbox" checked={sheets.includes(s.name)} onChange={(e) => setSheets(e.target.checked ? [...sheets, s.name] : sheets.filter((x) => x !== s.name))} />
                    {s.name} <span className="text-xs text-muted-foreground">({s.rows} rows)</span>
                  </label>
                ))}
              </div>
              <Field label="Brand identification">
                <Select value={brandMode} onChange={(e) => setBrandMode(e.target.value as "single" | "column")}>
                  <option value="single">One brand for the whole file</option>
                  <option value="column">Use the brand column</option>
                </Select>
              </Field>
              {brandMode === "single" && (
                <Field label="Brand">
                  <Select value={singleBrand} onChange={(e) => setSingleBrand(e.target.value ? Number(e.target.value) : "")}>
                    <option value="">Select…</option>
                    {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                  </Select>
                </Field>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field label="Default channel"><Select value={channel} onChange={(e) => setChannel(e.target.value)}><option>email</option><option>whatsapp</option><option>sms</option></Select></Field>
                <Field label="Default routing"><Select value={routing} onChange={(e) => setRouting(e.target.value)}><option>AUTO</option><option>STICKY</option><option>FIXED</option></Select></Field>
              </div>
              <Field label="Campaign name (if no column)"><Input value={campaign} onChange={(e) => setCampaign(e.target.value)} /></Field>
              <Field label="Schedule (optional)"><Input type="datetime-local" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} /></Field>
            </CardContent>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>Column mapping</CardTitle><CardDescription>Detected automatically from header names; adjust as needed.</CardDescription></CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2">
              {fields.map((f) => (
                <div key={f} className="flex items-center gap-2">
                  <span className="w-36 shrink-0 font-mono text-xs">{f}</span>
                  <Select value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}>
                    <option value="">— not mapped —</option>
                    {allColumns.map((col) => <option key={col} value={col}>{col}</option>)}
                  </Select>
                </div>
              ))}
              <div className="sm:col-span-2 mt-2 flex gap-2">
                <Button variant="outline" onClick={() => { setStep(0); setBatch(null); }}>Back</Button>
                <Button disabled={busy || !sheets.length || (brandMode === "single" && !singleBrand)} onClick={preview}>{busy ? "Validating…" : "Validate & preview"}</Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {step === 2 && batch && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Rows" value={c.total ?? 0} />
            <Stat label="Valid" value={c.valid ?? 0} hint={`${c.with_message ?? 0} with a message`} />
            <Stat label="Invalid" value={c.invalid ?? 0} />
            <Stat label="Duplicate" value={c.duplicate ?? 0} />
            <Stat label="Skipped" value={c.skipped ?? 0} />
          </div>
          <div className="my-4 flex flex-wrap items-center gap-2">
            <Select className="w-44" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All rows</option><option value="valid">Valid</option><option value="invalid">Invalid</option><option value="duplicate">Duplicate</option><option value="skipped">Skipped</option>
            </Select>
            <Button variant="outline" onClick={() => download(`/imports/${batch.id}/errors.xlsx`, `import-${batch.id}-errors.xlsx`)}>Download error workbook</Button>
            <Button variant="outline" onClick={() => setStep(1)}>Adjust mapping</Button>
            <Button disabled={busy || !c.valid} onClick={() => commit(!!batch.duplicate_of_batch_id)}>Import {c.valid ?? 0} valid rows</Button>
          </div>
          <Card>
            <CardContent className="p-0">
              <Table>
                <THead><TR><TH>Sheet</TH><TH>Row</TH><TH>Status</TH><TH>Brand</TH><TH>Recipient</TH><TH>Channel</TH><TH>Routing</TH><TH>Issues</TH></TR></THead>
                <TBody>
                  {shown.slice(0, 300).map((r) => (
                    <TR key={r.id}>
                      <TD>{r.sheet}</TD><TD>{r.row}</TD><TD><Badge tone={r.status === "valid" ? "green" : r.status === "invalid" ? "red" : "amber"}>{r.status}</Badge></TD>
                      <TD>{brandName(r.brand_id)}</TD>
                      <TD className="text-xs">{String(r.normalized.email ?? r.normalized.phone ?? "—")}</TD>
                      <TD>{String(r.normalized.channel ?? "")}</TD><TD>{String(r.normalized.routing_mode ?? "")}</TD>
                      <TD className="max-w-md text-xs text-muted-foreground">{[...r.errors, ...((r.normalized.info as string[]) ?? [])].join("; ")}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}

      {step === 3 && result && (
        <Card>
          <CardContent className="flex flex-col items-start gap-3 p-6">
            <CheckCircle2 className="h-8 w-8 text-emerald-600" />
            <div className="text-lg font-semibold">Imported {String(result.imported)} contacts</div>
            <p className="text-sm">{String(result.queued_for_approval)} messages were created as drafts and queued for approval. {String(result.note)}</p>
            <div className="flex gap-2">
              <Button onClick={() => (window.location.href = "/campaigns")}>Review campaigns</Button>
              <Button variant="outline" onClick={() => { setStep(0); setBatch(null); setRows([]); setResult(null); }}>Import another file</Button>
            </div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
