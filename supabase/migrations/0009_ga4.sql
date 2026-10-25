-- Google Analytics 4 : autorisations accordées à la connexion Google, propriétés GA4 associées aux projets.
alter table public.google_tokens add column scopes text not null default '';
update public.google_tokens set scopes = 'https://www.googleapis.com/auth/webmasters.readonly' where scopes = '';

create table public.ga4_properties (
  user_id uuid not null references auth.users (id) on delete cascade,
  property_id text not null,            -- ex. properties/123456789
  display_name text not null default '',
  site text,                            -- projet associé (ex. kompta.pro), facultatif
  created_at timestamptz not null default now(),
  primary key (user_id, property_id)
);
alter table public.ga4_properties enable row level security;
revoke all on public.ga4_properties from anon, authenticated;
grant select on public.ga4_properties to authenticated;
create policy "ses propriétés GA4" on public.ga4_properties for select to authenticated using (user_id = (select auth.uid()));
