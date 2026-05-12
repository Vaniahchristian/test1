-- Container manifest: MARKS (唛头) column → document_items.marks
-- Run in Supabase SQL editor or psql before importing manifests with the updated pipeline.

alter table public.document_items
  add column if not exists marks text;

comment on column public.document_items.marks is
  'Shipping marks from container manifest (MARKS / 唛头). Distinct from item_code / delivery_no.';
