-- Cache partagé des données payantes (DataForSEO) : une même recherche n'est payée qu'une fois par période.
-- Données publiques (volumes, difficulté…) : non liées à un utilisateur, réservées au serveur.
create table public.data_cache (
  key text primary key,
  fetched_at timestamptz not null default now(),
  data jsonb not null
);
alter table public.data_cache enable row level security;
revoke all on public.data_cache from anon, authenticated;
