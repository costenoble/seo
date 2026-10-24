-- Offres attribuées d'avance par e-mail (testeurs, comptes offerts) : appliquées à la création du compte.
create table public.plan_grants (
  email text primary key,
  plan text not null check (plan in ('gratuit', 'pro', 'agence')),
  created_at timestamptz not null default now()
);
alter table public.plan_grants enable row level security;
revoke all on public.plan_grants from anon, authenticated;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (user_id, email, plan)
  values (new.id, new.email,
          coalesce((select g.plan from public.plan_grants g where lower(g.email) = lower(new.email)), 'gratuit'))
  on conflict do nothing;
  return new;
end $$;
