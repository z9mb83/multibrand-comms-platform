"""Regenerate docs/database-schema.md from the SQLAlchemy models: python -m scripts.gen_schema_doc"""
from pathlib import Path

from app.models import Base

GROUPS = {
    "Tenancy & access": ["brands", "users", "brand_memberships", "audit_logs", "message_templates"],
    "Senders & routing": ["sender_accounts", "sender_routing_state", "sender_assignments"],
    "Contacts & consent": ["contacts", "brand_contacts", "contact_consents", "suppressions"],
    "Import": ["import_batches", "import_rows"],
    "Messaging": ["campaigns", "outbound_messages", "message_attempts", "conversations", "inbound_messages"],
    "Follow-ups & tasks": ["follow_up_rules", "follow_up_tasks", "daily_tasks"],
    "Master reporting": ["master_report_settings", "master_reports", "notification_deliveries"],
}


def main() -> None:
    t = Base.metadata.tables
    out = ["# Database schema", "",
           "Generated from `backend/app/models` by `python -m scripts.gen_schema_doc`. PostgreSQL 16.", "",
           "All brand-owned tables carry `brand_id`; every service query filters on it.", ""]
    out += ["```mermaid", "erDiagram"]
    for table in t.values():
        for fk in table.foreign_keys:
            out.append(f"    {fk.column.table.name} ||--o{{ {table.name} : \"{fk.parent.name}\"")
    out += ["```", ""]
    for group, names in GROUPS.items():
        out += [f"## {group}", ""]
        for name in names:
            table = t[name]
            out += [f"### `{name}`", "", "| Column | Type | Null | Notes |", "|---|---|---|---|"]
            for c in table.columns:
                notes = []
                if c.primary_key:
                    notes.append("PK")
                for fk in c.foreign_keys:
                    notes.append(f"FK → {fk.column.table.name}.{fk.column.name}")
                if c.unique:
                    notes.append("unique")
                if c.index:
                    notes.append("indexed")
                out.append(f"| {c.name} | {str(c.type).lower()} | {'yes' if c.nullable else 'no'} | {', '.join(notes)} |")
            uniq = [", ".join(col.name for col in con.columns) for con in table.constraints
                    if con.__class__.__name__ == "UniqueConstraint" and len(con.columns) > 1]
            if uniq:
                out += ["", "Unique: " + "; ".join(f"({u})" for u in uniq)]
            out.append("")
    Path(__file__).resolve().parents[2].joinpath("docs/database-schema.md").write_text("\n".join(out))
    print("written", sum(len(v) for v in GROUPS.values()), "tables")


if __name__ == "__main__":
    main()
