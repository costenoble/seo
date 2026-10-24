-- Audits : état du crawl reprenable, rapports, et isolation par utilisateur (RLS).
-- Le moteur d'audit se connecte avec le rôle postgres / service_role, qui contourne la RLS.
-- Les utilisateurs ne peuvent que LIRE leurs audits ; toute création passe par l'API (quotas, anti-SSRF).

create table public.audits (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  url text not null,
  site text not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
  phase text not null default 'init' check (phase in ('init', 'crawl', 'sitemap', 'finalize')),
  options jsonb not null default '{}',
  robots_text text,
  sitemap_urls jsonb not null default '[]',
  error text,
  summary jsonb,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index audits_user_site on public.audits (user_id, site, created_at desc);
create index audits_active on public.audits (created_at) where status in ('queued', 'running');

create table public.audit_pages (
  audit_id uuid not null references public.audits (id) on delete cascade,
  url text not null,
  depth int,
  state text not null default 'pending' check (state in ('pending', 'claimed', 'done')),
  claimed_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  result jsonb,
  primary key (audit_id, url)
);
create index audit_pages_todo on public.audit_pages (audit_id, created_at) where state <> 'done';

create table public.audit_reports (
  audit_id uuid primary key references public.audits (id) on delete cascade,
  issues jsonb not null,
  pages jsonb not null
);

alter table public.audits enable row level security;
alter table public.audit_pages enable row level security;
alter table public.audit_reports enable row level security;

create policy "lecture de ses audits" on public.audits
  for select to authenticated using (user_id = (select auth.uid()));
create policy "lecture de ses rapports" on public.audit_reports
  for select to authenticated using (
    exists (select 1 from public.audits a where a.id = audit_id and a.user_id = (select auth.uid())));
-- audit_pages : aucune politique, donc inaccessible aux utilisateurs (usage interne du moteur).

-- Défense en profondeur : Supabase accorde par défaut tous les droits à anon et authenticated sur le schéma
-- public ; on les retire et on n'accorde que la lecture des audits et rapports de l'utilisateur connecté.
revoke all on public.audits, public.audit_pages, public.audit_reports from anon, authenticated;
grant select on public.audits, public.audit_reports to authenticated;
