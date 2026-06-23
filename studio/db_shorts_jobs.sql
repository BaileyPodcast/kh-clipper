-- KH Studio — Shorts Engine migration.
-- Drop this into the Studio repo as the next db/NNN_shorts_jobs.sql and apply it
-- (the convention is a committed, reviewed migration — not an ad-hoc push).
--
-- Verified against the live "Kintsugi Heroes Production Pipeline" project conventions:
--   * section RLS via can_read_section('studio') / can_write_section('studio')
--   * shared set_updated_at() BEFORE UPDATE trigger
-- The Modal worker uses the SERVICE-ROLE key and bypasses RLS to patch progress/outputs.

-- 1) Job table -------------------------------------------------------------
create table if not exists public.shorts_jobs (
  id          uuid primary key default gen_random_uuid(),
  url         text not null,
  series      text,                               -- slug matching assets/artwork/<series>.png
  clip_count  int  not null default 5,
  audiogram   boolean not null default true,
  reframe     text not null default 'speaker'  -- 'speaker' follows the active speaker; 'center' centre-crops
              check (reframe in ('speaker','center')),
  status      text not null default 'queued'
              check (status in ('queued','running','done','error')),
  stage       text,
  progress    int  not null default 0,
  message     text,
  error       text,
  outputs     jsonb,                              -- manifest: clips + metadata + storage paths
  episode_id  text,                               -- youtube id, once known
  created_by  uuid,                               -- my_member_id() (audit; RLS is section-based)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
comment on table public.shorts_jobs is
  'Shorts Engine jobs: a producer pastes a URL + picks a series; a Modal worker runs the kh-clipper pipeline, streams progress here, and uploads outputs to the private shorts bucket. Human approval still gates publish/schedule (KH-TIC-001).';

create index if not exists shorts_jobs_status_idx  on public.shorts_jobs (status);
create index if not exists shorts_jobs_created_idx on public.shorts_jobs (created_at desc);

alter table public.shorts_jobs enable row level security;
create policy shorts_jobs_sect_read  on public.shorts_jobs
  for select using (can_read_section('studio'));
create policy shorts_jobs_sect_write on public.shorts_jobs
  for all using (can_write_section('studio')) with check (can_write_section('studio'));

create trigger shorts_jobs_set_updated_at
  before update on public.shorts_jobs
  for each row execute function set_updated_at();

-- 2) Private storage bucket + section policies -----------------------------
insert into storage.buckets (id, name, public)
values ('shorts', 'shorts', false)
on conflict (id) do nothing;

create policy shorts_obj_read  on storage.objects
  for select using (bucket_id = 'shorts' and can_read_section('studio'));
create policy shorts_obj_write on storage.objects
  for all using (bucket_id = 'shorts' and can_write_section('studio'))
  with check (bucket_id = 'shorts' and can_write_section('studio'));

-- 3) Realtime (so the Studio page can stream progress) ---------------------
alter publication supabase_realtime add table public.shorts_jobs;
