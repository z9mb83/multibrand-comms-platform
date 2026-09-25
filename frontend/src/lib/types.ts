export type Brand = { id: number; slug: string; name: string; master_email?: string | null; role?: string; primary_color?: string | null; status?: string; timezone?: string };
export type Me = { id: number; email: string; full_name: string; is_super_admin: boolean; brands: Brand[] };
export type Sender = {
  id: number; brand_id: number; channel: string; address: string; display_name: string | null; provider: string;
  status: string; connection_status: string; paused_reason: string | null; daily_limit: number; hourly_limit: number;
  provider_daily_limit: number | null; sent_today: number; sent_this_hour: number; rotation_order: number;
  health_status: string; last_health_check_at: string | null; last_error: string | null; is_notification_sender: boolean;
};
export type Campaign = { id: number; brand_id: number; name: string; channel: string; kind: string; status: string;
  subject: string | null; body: string | null; scheduled_at: string | null; approved_at: string | null; created_at: string };
export type Task = { id: number; task_code: string; brand_id: number; contact_id: number | null; company_name: string | null;
  assigned_user_id: number | null; description: string; priority: string; due_at: string | null; status: string;
  completed_at: string | null; completed_by: number | null; completed_source: string | null; notes: string | null;
  conversation_id: number | null; source: string; carry_count: number };
export type Conversation = { id: number; brand_id: number; brand_name: string; contact_id: number; channel: string;
  status: string; subject: string | null; sender_address: string | null; contact_name: string | null;
  contact_email: string | null; contact_phone: string | null; last_message: string | null; classification: string | null;
  last_inbound_at: string | null; unread_count: number; followups_paused: boolean };
