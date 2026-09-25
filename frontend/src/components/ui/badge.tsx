import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  green: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  red: "bg-red-500/15 text-red-700 dark:text-red-300",
  amber: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  blue: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  gray: "bg-muted text-muted-foreground",
};

const STATUS_TONE: Record<string, keyof typeof tones> = {
  active: "green", connected: "green", healthy: "green", sent: "green", delivered: "green", read: "green",
  Completed: "green", completed: "green", running: "green", approved: "blue", granted: "green",
  paused: "amber", pending: "amber", Pending: "amber", queued: "blue", draft: "gray", pending_approval: "amber",
  held_for_review: "amber", awaiting_approval: "amber", "In Progress": "blue", needs_reply: "amber",
  failed: "red", error: "red", failing: "red", Overdue: "red", Blocked: "red", emergency_stopped: "red",
  suppressed: "red", withdrawn: "red", disconnected: "red", unknown: "gray", cancelled: "gray", Cancelled: "gray",
  inactive: "gray", paused_on_reply: "amber", retrying: "amber", generated: "blue", partially_delivered: "amber",
};

export function Badge({ children, tone, className }: { children: React.ReactNode; tone?: keyof typeof tones; className?: string }) {
  const t = tone ?? STATUS_TONE[String(children)] ?? "gray";
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", tones[t], className)}>{typeof children === "string" ? children.replace(/_/g, " ") : children}</span>;
}
