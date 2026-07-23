alter table public.tikpoc_target_pools
    add column import_state text not null default 'complete'
    check (import_state in ('importing', 'complete'));

alter table public.tikpoc_accounts
    add constraint tikpoc_accounts_account_device_key
    unique (account_id, device_id);

alter table public.tikpoc_device_health
    drop constraint tikpoc_device_health_account_id_fkey;

alter table public.tikpoc_device_health
    add constraint tikpoc_device_health_account_device_fkey
    foreign key (account_id, device_id)
    references public.tikpoc_accounts(account_id, device_id);

alter table public.tikpoc_leads
    add constraint tikpoc_leads_stage_check
    check (stage in (
        'new', 'engaged', 'qualified', 'invited',
        'contact_captured', 'human_required', 'closed'
    ));

alter table public.tikpoc_lead_events
    add constraint tikpoc_lead_events_stage_check
    check (stage in (
        'new', 'engaged', 'qualified', 'invited',
        'contact_captured', 'human_required', 'closed'
    ));

create function public.tikpoc_lead_stage_rank(stage_name text)
returns integer
language sql
immutable
strict
as $$
    select case stage_name
        when 'new' then 0
        when 'engaged' then 1
        when 'qualified' then 2
        when 'invited' then 3
        when 'contact_captured' then 4
        when 'human_required' then 5
        when 'closed' then 6
    end
$$;

create function public.tikpoc_lead_stage_monotonic()
returns trigger
language plpgsql
as $$
begin
    if public.tikpoc_lead_stage_rank(new.stage)
        < public.tikpoc_lead_stage_rank(old.stage) then
        raise exception 'tikpoc lead stage cannot regress';
    end if;
    return new;
end
$$;

create trigger tikpoc_lead_stage_monotonic
before update of stage on public.tikpoc_leads
for each row
execute function public.tikpoc_lead_stage_monotonic();

revoke all on function public.tikpoc_lead_stage_rank(text) from public;
revoke all on function public.tikpoc_lead_stage_monotonic() from public;
