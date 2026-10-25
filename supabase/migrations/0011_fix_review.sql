-- Validation des corrections depuis Vigie SEO : fusion ou refus de la pull request.
alter table public.fixes add column merged_at timestamptz;
alter table public.fixes add column closed_at timestamptz;
