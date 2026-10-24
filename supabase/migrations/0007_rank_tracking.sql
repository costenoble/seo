-- Suivi de positions sur des mots-clés choisis (relevé quotidien via DataForSEO, mode différé).
create table public.rank_keywords (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,                       -- domaine suivi (ex. kompta.pro)
  keyword text not null,
  location_code int not null default 2250,
  device text not null default 'desktop' check (device in ('desktop', 'mobile')),
  volume int,
  last_checked date,
  created_at timestamptz not null default now(),
  unique (user_id, site, keyword, location_code, device)
);
create index rank_keywords_due on public.rank_keywords (last_checked);

create table public.rank_checks (
  keyword_id uuid not null references public.rank_keywords (id) on delete cascade,
  date date not null,
  position int,                             -- null : absent du top suivi
  url text,
  ranks jsonb not null default '{}',        -- {domaine: [position, url]} pour tout le top relevé
  primary key (keyword_id, date)
);

create table public.rank_competitors (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,
  domain text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, site, domain)
);

-- Relevés en attente chez DataForSEO : un seul par (mot-clé, pays, appareil, jour), partagé entre utilisateurs.
create table public.serp_tasks (
  task_id text primary key,
  keyword text not null,
  location_code int not null,
  device text not null,
  date date not null,
  created_at timestamptz not null default now(),
  unique (keyword, location_code, device, date)
);

alter table public.rank_keywords enable row level security;
alter table public.rank_checks enable row level security;
alter table public.rank_competitors enable row level security;
alter table public.serp_tasks enable row level security;
revoke all on public.rank_keywords, public.rank_checks, public.rank_competitors, public.serp_tasks from anon, authenticated;
grant select on public.rank_keywords, public.rank_checks, public.rank_competitors to authenticated;
create policy "ses mots-clés suivis" on public.rank_keywords for select to authenticated using (user_id = (select auth.uid()));
create policy "ses relevés" on public.rank_checks for select to authenticated using (
  exists (select 1 from public.rank_keywords k where k.id = keyword_id and k.user_id = (select auth.uid())));
create policy "ses concurrents" on public.rank_competitors for select to authenticated using (user_id = (select auth.uid()));
-- serp_tasks : aucune politique (usage interne).
