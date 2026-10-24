-- E-mails : résumé hebdomadaire et alertes (score en baisse, problèmes graves, mouvements de positions).
create table public.notification_settings (
  user_id uuid primary key references auth.users (id) on delete cascade,
  weekly_digest boolean not null default true,
  alerts boolean not null default true,
  last_digest_at timestamptz,
  last_alert_email_at timestamptz,
  updated_at timestamptz not null default now()
);

create table public.alerts (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  dedupe_key text not null,
  kind text not null,                   -- score_drop, new_critical, rank_drop, rank_gain
  tone text not null default 'bad' check (tone in ('bad', 'good')),
  site text not null,
  title text not null,
  detail text not null default '',
  link text not null default '',
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  unique (user_id, dedupe_key)
);
create index alerts_unsent on public.alerts (user_id) where sent_at is null;

alter table public.audits add column notified_at timestamptz;

alter table public.notification_settings enable row level security;
alter table public.alerts enable row level security;
revoke all on public.notification_settings, public.alerts from anon, authenticated;
grant select on public.notification_settings, public.alerts to authenticated;
create policy "ses réglages" on public.notification_settings for select to authenticated using (user_id = (select auth.uid()));
create policy "ses alertes" on public.alerts for select to authenticated using (user_id = (select auth.uid()));

-- les audits déjà terminés ne déclenchent pas d'alerte rétroactive
update public.audits set notified_at = coalesce(finished_at, now()) where status in ('done', 'failed');

-- prénom affiché dans les e-mails (repris de la session Google à chaque connexion)
alter table public.profiles add column name text;
