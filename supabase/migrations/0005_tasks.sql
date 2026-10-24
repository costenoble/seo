-- Plan d'action : chaque recommandation (audit ou Search Console) devient une tâche suivie,
-- avec mesure de l'effet une fois marquée comme faite.
create table public.tasks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,                      -- clé du projet (ex. kompta.pro)
  fingerprint text not null,               -- identifiant stable de la recommandation (ex. audit:duplicate_title)
  source text not null check (source in ('audit', 'gsc')),
  priority text not null check (priority in ('high', 'medium', 'low')),
  title text not null,
  detail text not null default '',
  action text not null default '',
  pages jsonb not null default '[]',       -- URLs concernées
  queries jsonb not null default '[]',     -- requêtes suivies pour mesurer l'effet
  baseline jsonb not null default '{}',    -- chiffres au moment de la création
  status text not null default 'todo' check (status in ('todo', 'done', 'ignored')),
  resolved_by text,                        -- 'user' ou 'audit' (fermée automatiquement)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  done_at timestamptz,
  unique (user_id, site, fingerprint)
);
create index tasks_user_site on public.tasks (user_id, site, status);

alter table public.tasks enable row level security;
revoke all on public.tasks from anon, authenticated;
grant select on public.tasks to authenticated;
create policy "ses tâches" on public.tasks for select to authenticated using (user_id = (select auth.uid()));
