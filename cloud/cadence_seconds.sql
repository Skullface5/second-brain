-- Cadence as a number + unit instead of a fixed dropdown.
-- every_seconds is the real value; every_minutes is kept in sync for anything
-- that still reads it (and for rows created before this).

alter table public.sources add column if not exists every_seconds integer;

update public.sources
   set every_seconds = every_minutes * 60
 where every_seconds is null;

create or replace function public.sb_source_write(p_token text, p_action text, p jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  ok   boolean;
  v    text := lower(btrim(coalesce(p->>'name', '')));
  act  text := lower(btrim(coalesce(p_action, 'update')));
  secs integer;
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

  -- "every": "5m" / "2h" / "1d" parsed by the client into every_seconds
  secs := nullif(p->>'every_seconds', '')::int;
  if secs is not null and secs < 60 then
    secs := 60;                                  -- one minute floor
  end if;

  if act = 'update' then
    update public.sources set
      enabled       = coalesce((p->>'enabled')::boolean, enabled),
      every_seconds = coalesce(secs, every_seconds),
      every_minutes = case when secs is not null
                           then greatest(1, (secs + 30) / 60) else every_minutes end,
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
    return jsonb_build_object('ok', true, 'action', 'update', 'name', v, 'every_seconds', secs);
  end if;

  insert into public.sources (name, kind, enabled, every_seconds, every_minutes, max_items,
                              category, fulltext, config, updated_at)
  values (
    v,
    coalesce(nullif(p->>'kind', ''), 'rss'),
    coalesce((p->>'enabled')::boolean, true),
    coalesce(secs, 3600),
    greatest(1, (coalesce(secs, 3600) + 30) / 60),
    nullif(p->>'max_items', '')::int,
    coalesce(nullif(p->>'category', ''), 'news'),
    coalesce(nullif(p->>'fulltext', ''), 'auto'),
    coalesce(p->'config', '{}'::jsonb),
    now()
  )
  on conflict (name) do update set
    kind          = excluded.kind,
    enabled       = excluded.enabled,
    every_seconds = excluded.every_seconds,
    every_minutes = excluded.every_minutes,
    max_items     = excluded.max_items,
    category      = excluded.category,
    fulltext      = excluded.fulltext,
    config        = public.sources.config || excluded.config,
    updated_at    = now();
  return jsonb_build_object('ok', true, 'action', 'upsert', 'name', v,
                            'every_seconds', coalesce(secs, 3600));
end $$;

revoke all on function public.sb_source_write(text, text, jsonb) from public;
grant execute on function public.sb_source_write(text, text, jsonb) to anon, authenticated;

select name, every_seconds, every_minutes from public.sources order by name;