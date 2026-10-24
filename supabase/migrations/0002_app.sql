-- Application multi-utilisateurs : profils et abonnements, quotas, concurrents, Search Console, mots-clés.

-- Profils : créés automatiquement à l'inscription. Le plan n'est modifiable que côté serveur.
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  email text,
  plan text not null default 'gratuit',
  plan_renews_at timestamptz,
  created_at timestamptz not null default now()
);

create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (user_id, email) values (new.id, new.email) on conflict do nothing;
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Journal de consommation, pour les quotas mensuels (audits, recherches de mots-clés, imports…).
create table public.usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null,
  created_at timestamptz not null default now()
);
create index usage_events_month on public.usage_events (user_id, kind, created_at);

-- Audits : type (site de l'utilisateur ou concurrent) et verrou d'exécution (une seule instance à la fois).
alter table public.audits
  add column kind text not null default 'site' check (kind in ('site', 'competitor')),
  add column parent_site text,
  add column lease_until timestamptz,
  add column lease_owner text,
  add column pages_done int not null default 0,
  add column pages_total int not null default 0;
create index audits_competitors on public.audits (user_id, parent_site) where kind = 'competitor';

-- Jeton Google (Search Console) : chiffré par l'application, jamais lisible côté navigateur.
create table public.google_tokens (
  user_id uuid primary key references auth.users (id) on delete cascade,
  refresh_token_enc text not null,
  email text,
  updated_at timestamptz not null default now()
);

-- Propriétés Search Console suivies (import quotidien automatique).
create table public.gsc_properties (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,
  last_synced date,
  created_at timestamptz not null default now(),
  primary key (user_id, site)
);

create table public.gsc_daily (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,
  date date not null,
  query text not null,
  clicks real not null,
  impressions real not null,
  ctr real not null,
  position real not null,
  primary key (user_id, site, date, query)
);
create index gsc_daily_site_date on public.gsc_daily (user_id, site, date);

create table public.keyword_ideas (
  user_id uuid not null references auth.users (id) on delete cascade,
  seed text not null,
  idea text not null,
  kind text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, seed, idea)
);

-- Sécurité : lecture seule de ses propres lignes, aucune écriture directe depuis le navigateur.
alter table public.profiles enable row level security;
alter table public.usage_events enable row level security;
alter table public.google_tokens enable row level security;
alter table public.gsc_properties enable row level security;
alter table public.gsc_daily enable row level security;
alter table public.keyword_ideas enable row level security;

revoke all on public.profiles, public.usage_events, public.google_tokens, public.gsc_properties,
  public.gsc_daily, public.keyword_ideas from anon, authenticated;
grant select on public.profiles, public.gsc_properties, public.gsc_daily, public.keyword_ideas to authenticated;

create policy "son profil" on public.profiles for select to authenticated using (user_id = (select auth.uid()));
create policy "ses propriétés" on public.gsc_properties for select to authenticated using (user_id = (select auth.uid()));
create policy "ses positions" on public.gsc_daily for select to authenticated using (user_id = (select auth.uid()));
create policy "ses idées" on public.keyword_ideas for select to authenticated using (user_id = (select auth.uid()));
-- google_tokens et usage_events : aucune politique, réservés au serveur.
