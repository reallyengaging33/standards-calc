-- Your Delulu Number: anonymous results table
-- Paste this whole file into Supabase > SQL Editor > New query, then click Run.

create table if not exists public.results (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  app_version    text,
  gender         text,
  seeking        text,
  age_min        int,
  age_max        int,
  us_state       text,              -- only when they searched a state or metro
  city           text,              -- only when they searched a metro
  census_n       double precision,  -- census odds: 1 in N
  real_n         double precision,  -- with standards: 1 in N
  census_pts     real,
  standards_pts  real,
  delusion_score int,
  answers        jsonb not null,    -- the full filter state from the page
  constraint answers_not_huge check (pg_column_size(answers) < 8000)
);

-- Lock it down: the website may ONLY add rows. Nobody can read, change or delete
-- rows through the public key. You read them in the Supabase dashboard.
alter table public.results enable row level security;

drop policy if exists "Website can add anonymous results" on public.results;
create policy "Website can add anonymous results"
  on public.results
  for insert
  to anon
  with check (true);

grant insert on public.results to anon;
