# ONB-03 — Admin screenshot management by OS and VPN client

Layer: plugin. Dependencies: ONB-02. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Provide an admin view listing configured VPN clients under each OS and allowing
gallery upload, preview, ordering, replacement, and removal. Use a supported admin section/tab
and the Core media contract instead of replacing the instructions screen.

## Acceptance criteria

- Admins can find the relevant source/configuration, OS, and client; loading,
  unavailable, empty, and orphaned-attachment states are distinct.
- Support zero or more ordered images per client with localized captions; the
  same configured gallery appears in authenticated and public subscription instructions.
- Include all configured guide variants and make their source identity visible;
  editing one client's gallery does not change another configuration's gallery.
- Uploaded content has bounded bytes/dimensions, validated decoded image format,
  safe storage names, and appropriate serving headers. Active content is rejected.
- Upload/change operations require admin authorization; public image access exposes
  no account data, subscription URLs, tokens, or storage paths.
- Plugin-owned storage/migrations have documented persistence, removal, and
  backup/restore expectations. Do not modify Panel configuration just to store images.
- Reuse appropriate existing attachment/viewer components and inspect their consumers.
- Verify rendered images and lightbox behavior, if used, on desktop/mobile; a missing
  image never blocks download/import actions. Test stale associations and access rules.

## Source starting points

- `docs/development/plugin-ui-composition.md`
- `frontend/src/lib/admin/components/InstallTargetFields.svelte`
- `frontend/src/lib/components/ui/image-attachment.svelte`
- `frontend/src/lib/components/ui/image-viewer.svelte`
