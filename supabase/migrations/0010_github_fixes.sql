-- Corrections automatiques via l'application GitHub « Vigie SEO » : installations, dépôt de chaque projet,
-- et historique des pull requests ouvertes.
create table public.github_installations (
  user_id uuid not null references auth.users (id) on delete cascade,
  installation_id bigint not null,
  account text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, installation_id)
);

create table public.github_repos (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,                    -- projet Vigie SEO (ex. kompta.pro)
  installation_id bigint not null,
  repo text not null,                    -- ex. moncompte/kompta
  created_at timestamptz not null default now(),
  primary key (user_id, site)
);

create table public.fixes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  task_id uuid references public.tasks (id) on delete cascade,
  site text not null,
  repo text not null,
  status text not null default 'running' check (status in ('running', 'done', 'failed', 'skipped')),
  branch text,
  pr_url text,
  pr_number int,
  summary text,
  error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index fixes_task on public.fixes (task_id, created_at desc);

alter table public.github_installations enable row level security;
alter table public.github_repos enable row level security;
alter table public.fixes enable row level security;
revoke all on public.github_installations, public.github_repos, public.fixes from anon, authenticated;
grant select on public.github_installations, public.github_repos, public.fixes to authenticated;
create policy "ses installations GitHub" on public.github_installations for select to authenticated using (user_id = (select auth.uid()));
create policy "ses dépôts" on public.github_repos for select to authenticated using (user_id = (select auth.uid()));
create policy "ses corrections" on public.fixes for select to authenticated using (user_id = (select auth.uid()));
