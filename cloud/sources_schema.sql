-- second-brain: source registry for the farm, editable from the PWA.
-- Read: public (anon).  Write: only through sb_source_write() with the device token.

create table if not exists public.sources (
  name          text primary key,
  kind          text not null default 'rss',
  enabled       boolean not null default true,
  every_minutes integer not null default 60,
  max_items     integer,
  category      text default 'news',
  fulltext      text default 'auto',
  config        jsonb   not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

alter table public.sources enable row level security;
drop policy if exists sources_public_read on public.sources;
create policy sources_public_read on public.sources for select using (true);

-- private key/value store: no policies, so anon/authenticated see nothing.
create table if not exists public.app_config (
  key   text primary key,
  value text not null
);
alter table public.app_config enable row level security;

create or replace function public.sb_source_write(p_token text, p_action text, p jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  ok  boolean;
  v   text := lower(btrim(coalesce(p->>'name', '')));
  act text := lower(btrim(coalesce(p_action, 'update')));
begin
  select exists(select 1 from public.app_config where key = 'write_token' and value = p_token)
    into ok;
  if not ok then
    return jsonb_build_object('ok', false, 'error', 'bad token');
  end if;
  if v = '' or v !~ '^[a-z0-9_.-]{1,40}$' then
    return jsonb_build_object('ok', false, 'error', 'name must be 1-40 chars of a-z 0-9 _ . -');
  end if;

  if act = 'delete' then
    delete from public.sources where name = v;
    return jsonb_build_object('ok', true, 'action', 'delete', 'name', v);
  end if;

  if act = 'update' then
    update public.sources set
      enabled       = coalesce((p->>'enabled')::boolean, enabled),
      every_minutes = coalesce(nullif(p->>'every_minutes', '')::int, every_minutes),
      max_items     = case when p ? 'max_items'
                            then nullif(p->>'max_items', '')::int else max_items end,
      category      = coalesce(nullif(p->>'category', ''), category),
      fulltext      = coalesce(nullif(p->>'fulltext', ''), fulltext),
      config        = config || coalesce(p->'config', '{}'::jsonb),
      updated_at    = now()
    where name = v;
    if not found then
      return jsonb_build_object('ok', false, 'error', 'unknown source ' || v);
    end if;
    return jsonb_build_object('ok', true, 'action', 'update', 'name', v);
  end if;

  -- add / upsert: the form always sends a complete source
  insert into public.sources (name, kind, enabled, every_minutes, max_items,
                              category, fulltext, config, updated_at)
  values (
    v,
    coalesce(nullif(p->>'kind', ''), 'rss'),
    coalesce((p->>'enabled')::boolean, true),
    coalesce(nullif(p->>'every_minutes', '')::int, 60),
    nullif(p->>'max_items', '')::int,
    coalesce(nullif(p->>'category', ''), 'news'),
    coalesce(nullif(p->>'fulltext', ''), 'auto'),
    coalesce(p->'config', '{}'::jsonb),
    now()
  )
  on conflict (name) do update set
    kind          = excluded.kind,
    enabled       = excluded.enabled,
    every_minutes = excluded.every_minutes,
    max_items     = excluded.max_items,
    category      = excluded.category,
    fulltext      = excluded.fulltext,
    config        = public.sources.config || excluded.config,
    updated_at    = now();
  return jsonb_build_object('ok', true, 'action', 'upsert', 'name', v);
end $$;

revoke all on function public.sb_source_write(text, text, jsonb) from public;
grant execute on function public.sb_source_write(text, text, jsonb) to anon, authenticated;

select 'sources table + sb_source_write() ready' as status;