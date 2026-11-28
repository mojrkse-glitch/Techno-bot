create table public.techno_cv_jobs (
    order_id bigint primary key,
    stage text not null,
    organized jsonb not null,
    canva_job text,
    design_id text,
    export_job text,
    submit_state text check (submit_state in ('sending', 'sent'))
);

alter table public.techno_cv_jobs enable row level security;
revoke all on public.techno_cv_jobs from anon, authenticated;
grant select, insert, update on public.techno_cv_jobs to service_role;
