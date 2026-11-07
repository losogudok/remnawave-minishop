-- Remnawave <= 2.8 uses uuid; 3.x removes it and exposes id instead.
-- Read the actual rows: sequence values vary across existing Panel databases.
SELECT json_agg(json_build_object(
    'username', username,
    'panel_user_uuid', coalesce(to_jsonb(u)->>'uuid', to_jsonb(u)->>'id'),
    'panel_short_uuid', short_uuid
) ORDER BY username)
FROM users AS u
WHERE username IN ('runes_admin', 'runes_active', 'runes_expired');
