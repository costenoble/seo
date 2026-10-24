-- Search Console par page : quelle page répond à quelle requête (90 derniers jours), pour l'analyse.
-- Instantanés remplacés à chaque import (pas d'historique : gsc_daily garde l'historique des requêtes).
create table public.gsc_query_pages (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,
  query text not null,
  page text not null,
  clicks real not null,
  impressions real not null,
  position real not null,
  primary key (user_id, site, query, page)
);

create table public.gsc_pages (
  user_id uuid not null references auth.users (id) on delete cascade,
  site text not null,
  page text not null,
  clicks real not null,
  impressions real not null,
  position real not null,
  primary key (user_id, site, page)
);

alter table public.gsc_properties add column pages_synced_at timestamptz;

alter table public.gsc_query_pages enable row level security;
alter table public.gsc_pages enable row level security;
revoke all on public.gsc_query_pages, public.gsc_pages from anon, authenticated;
grant select on public.gsc_query_pages, public.gsc_pages to authenticated;
create policy "ses requêtes par page" on public.gsc_query_pages for select to authenticated using (user_id = (select auth.uid()));
create policy "ses pages" on public.gsc_pages for select to authenticated using (user_id = (select auth.uid()));
