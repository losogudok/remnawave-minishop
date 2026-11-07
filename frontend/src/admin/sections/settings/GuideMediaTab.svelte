<script lang="ts">
  import { onMount } from "svelte";
  import {
    AdminBadge,
    AdminButton,
    AdminEmptyState,
    AdminField,
    AdminSelect,
  } from "$components/patterns/admin/index.js";
  import Dialog from "$components/ui/dialog.svelte";
  import {
    ArrowDown,
    ArrowUp,
    Monitor,
    RefreshCw,
    Save,
    Trash2,
    Upload,
  } from "$components/ui/icons.js";
  import { ImageViewer, Input } from "$components/ui/index.js";
  import ImageAttachment from "$components/ui/image-attachment.svelte";
  import { getAdminApi } from "$lib/admin/context";
  import type { components } from "$lib/api/openapi.generated.js";
  import { messageRequestBody } from "$lib/messageImage.js";
  import { builtApiPath, unwrap } from "$lib/webapp/publicApi.js";
  import type { AdminSectionComponentProps } from "../extensionTypes";
  import {
    catalogProblems,
    catalogState,
    orphanState,
    type CatalogProblem,
  } from "./guideMediaState";

  type CatalogConfig = components["schemas"]["GuideMediaCatalogConfigOut"];
  type CatalogApp = components["schemas"]["GuideMediaCatalogAppOut"];
  type Gallery = components["schemas"]["GuideMediaGalleryOut"];
  type GalleryImage = components["schemas"]["GuideMediaImageOut"];
  type GalleryKey = {
    config_id: string;
    platform: string;
    client_id: string;
  };
  type CaptionDraft = { ru: string; en: string };
  type TranslateFn = AdminSectionComponentProps["at"];

  const MAX_SCREENSHOTS = 8;
  const api = getAdminApi();

  let { at }: Partial<AdminSectionComponentProps> & { at: TranslateFn } = $props();

  let catalog = $state<CatalogConfig[]>([]);
  let galleries = $state<Gallery[]>([]);
  let orphaned = $state<GalleryKey[]>([]);
  let catalogAvailable = $state(false);
  let catalogError = $state<string | null>(null);
  let orphanedKnown = $state(false);
  let loading = $state(true);
  let loadFailed = $state(false);
  let configId = $state("");
  let platformId = $state("");
  let clientId = $state("");
  let orphanSelection = $state<GalleryKey | null>(null);
  let uploadFile = $state<File | null>(null);
  let uploadCaptions = $state<CaptionDraft>({ ru: "", en: "" });
  let uploadBusy = $state(false);
  let uploadError = $state("");
  let actionError = $state("");
  let captionDrafts = $state<Record<string, CaptionDraft>>({});
  let saveBusyId = $state("");
  let orderBusy = $state(false);
  let deleteTarget = $state<GalleryImage | null>(null);
  let deleteBusy = $state(false);
  let deleteError = $state("");
  let viewerImage = $state<GalleryImage | null>(null);

  const selectedConfig = $derived(catalog.find((item) => item.config_id === configId) ?? null);
  const platforms = $derived(selectedConfig?.platforms ?? []);
  const selectedPlatform = $derived(platforms.find((item) => item.id === platformId) ?? null);
  const apps = $derived(selectedPlatform?.apps ?? []);
  const selectedApp = $derived(apps.find((item) => item.id === clientId) ?? null);
  const activeKey = $derived.by(() => {
    if (orphanSelection) return orphanSelection;
    if (!configId || !platformId || !clientId) return null;
    return { config_id: configId, platform: platformId, client_id: clientId };
  });
  const activeGallery = $derived(
    activeKey ? (galleries.find((item) => sameKey(item, activeKey)) ?? null) : null
  );
  const activeImages = $derived.by(() =>
    [...(activeGallery?.images ?? [])].sort((left, right) => left.order - right.order)
  );
  const activeOrphanState = $derived.by(() => {
    if (activeKey && catalogKeys.has(galleryKeyText(activeKey))) return "known";
    return orphanState(Boolean(activeGallery?.orphaned) || Boolean(orphanSelection), orphanedKnown);
  });
  const activeOrphaned = $derived(activeOrphanState === "orphaned");
  const activeOrphanUnknown = $derived(activeOrphanState === "unknown");
  const activeAmbiguous = $derived(orphanSelection === null && Boolean(selectedApp?.ambiguous));
  const limitReached = $derived(activeImages.length >= MAX_SCREENSHOTS);
  const catalogStatus = $derived(catalogState(catalog, catalogAvailable));
  const problems = $derived(catalogProblems(catalog));
  const catalogKeys = $derived(
    new Set(
      catalog.flatMap((config) =>
        (config.platforms ?? []).flatMap((platform) =>
          (platform.apps ?? []).map(
            (app) => `${config.config_id}\u0000${platform.id}\u0000${app.id}`
          )
        )
      )
    )
  );
  const storedFallback = $derived(
    catalogStatus === "unavailable"
      ? galleries
      : !orphanedKnown
        ? galleries.filter((gallery) => !catalogKeys.has(galleryKeyText(gallery)))
        : []
  );
  const savedGalleryConfigError = $derived(
    Boolean(selectedConfig?.missing || selectedConfig?.error)
  );

  onMount(() => {
    void load();
  });

  function sameKey(left: GalleryKey, right: GalleryKey): boolean {
    return galleryKeyText(left) === galleryKeyText(right);
  }

  function galleryKeyText(key: GalleryKey): string {
    return `${key.config_id}\u0000${key.platform}\u0000${key.client_id}`;
  }

  function isOrphanKey(key: GalleryKey): boolean {
    return orphaned.some((item) => sameKey(item, key));
  }

  function sourceLabel(source: string): string {
    if (source === "file") return at("guide_media_source_file", {}, "Local file");
    if (source === "admin_json") return at("guide_media_source_admin_json", {}, "Admin JSON");
    if (source === "panel") return at("guide_media_source_panel", {}, "Panel");
    return at("guide_media_source_unknown", {}, "Source");
  }

  function configLabel(config: CatalogConfig): string {
    return `${sourceLabel(config.source)} · ${config.config_id}`;
  }

  function problemText(problem: CatalogProblem): string {
    return problem.error ?? at("guide_media_config_missing");
  }

  function syncSelection(): void {
    if (orphanSelection && !isOrphanKey(orphanSelection)) orphanSelection = null;
    if (orphanSelection) return;
    const current = catalog.find((item) => item.config_id === configId);
    if (current) {
      const currentPlatforms = current.platforms ?? [];
      const platform =
        currentPlatforms.find((item) => item.id === platformId) ?? currentPlatforms[0];
      if (platform) {
        configId = current.config_id;
        platformId = platform.id;
        const currentApps = platform.apps ?? [];
        clientId = currentApps.some((item) => item.id === clientId)
          ? clientId
          : (currentApps[0]?.id ?? "");
        return;
      }
    }
    const fallback = catalog.find((item) => (item.platforms ?? []).length > 0);
    if (!fallback) {
      configId = "";
      platformId = "";
      clientId = "";
      return;
    }
    const fallbackPlatform = (fallback.platforms ?? [])[0];
    configId = fallback.config_id;
    platformId = fallbackPlatform.id;
    clientId = (fallbackPlatform.apps ?? [])[0]?.id ?? "";
  }

  async function load(): Promise<void> {
    loading = true;
    loadFailed = false;
    actionError = "";
    try {
      const result = unwrap(
        await api(
          builtApiPath<"/api/admin/guide-media/galleries">("/api/admin/guide-media/galleries")
        )
      );
      catalog = result.catalog ?? [];
      galleries = result.galleries ?? [];
      orphaned = result.orphaned ?? [];
      catalogAvailable = result.catalog_available ?? false;
      catalogError = result.catalog_error ?? null;
      orphanedKnown = result.orphaned_known ?? false;
      syncSelection();
    } catch {
      loadFailed = true;
    } finally {
      loading = false;
    }
  }

  function selectConfig(value: string): void {
    configId = value;
    const config = catalog.find((item) => item.config_id === value);
    const platform = (config?.platforms ?? [])[0];
    platformId = platform?.id ?? "";
    clientId = (platform?.apps ?? [])[0]?.id ?? "";
    orphanSelection = null;
    uploadError = "";
  }

  function selectPlatform(value: string): void {
    platformId = value;
    const platform = platforms.find((item) => item.id === value);
    clientId = (platform?.apps ?? [])[0]?.id ?? "";
    orphanSelection = null;
    uploadError = "";
  }

  function selectClient(value: string): void {
    clientId = value;
    orphanSelection = null;
    uploadError = "";
  }

  function openOrphan(key: GalleryKey): void {
    orphanSelection = { ...key };
    uploadError = "";
  }

  function closeOrphan(): void {
    orphanSelection = null;
    syncSelection();
  }

  function captionValue(image: GalleryImage, locale: "ru" | "en"): string {
    return captionDrafts[image.image_id]?.[locale] ?? image.captions?.[locale] ?? "";
  }

  function setCaption(image: GalleryImage, locale: "ru" | "en", value: string): void {
    const current = captionDrafts[image.image_id] ?? {
      ru: image.captions?.ru ?? "",
      en: image.captions?.en ?? "",
    };
    captionDrafts = {
      ...captionDrafts,
      [image.image_id]: { ...current, [locale]: value },
    };
  }

  function replaceGallery(gallery: Gallery): void {
    if (galleries.some((item) => sameKey(item, gallery))) {
      galleries = galleries.map((item) => (sameKey(item, gallery) ? gallery : item));
    } else {
      galleries = [...galleries, gallery];
    }
  }

  function replaceImage(image: GalleryImage): void {
    galleries = galleries.map((gallery) =>
      sameKey(gallery, image)
        ? {
            ...gallery,
            images: (gallery.images ?? []).map((item) =>
              item.image_id === image.image_id ? image : item
            ),
          }
        : gallery
    );
  }

  function imageSizeLabel(image: GalleryImage): string {
    const size = Math.max(1, Math.ceil((image.size_bytes || 0) / 1024));
    return `${image.width ?? 0} × ${image.height ?? 0} · ${size} KB`;
  }

  async function saveCaptions(image: GalleryImage): Promise<void> {
    if (saveBusyId) return;
    saveBusyId = image.image_id;
    actionError = "";
    try {
      const result = unwrap(
        await api(
          builtApiPath<"/api/admin/guide-media/images/{image_id}">(
            `/api/admin/guide-media/images/${encodeURIComponent(image.image_id)}`
          ),
          {
            method: "PATCH",
            body: JSON.stringify({
              captions: {
                ru: captionValue(image, "ru").trim(),
                en: captionValue(image, "en").trim(),
              },
              order: image.order,
            }),
          }
        )
      );
      if (!("image" in result)) throw new Error("Invalid guide media response");
      replaceImage(result.image);
    } catch {
      actionError = at("guide_media_save_failed", {}, "Could not save captions.");
    } finally {
      saveBusyId = "";
    }
  }

  async function moveImage(image: GalleryImage, direction: -1 | 1): Promise<void> {
    if (!activeKey || orderBusy) return;
    const index = activeImages.findIndex((item) => item.image_id === image.image_id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= activeImages.length) return;
    const ids = activeImages.map((item) => item.image_id);
    [ids[index], ids[target]] = [ids[target], ids[index]];
    orderBusy = true;
    actionError = "";
    try {
      const result = unwrap(
        await api(
          builtApiPath<"/api/admin/guide-media/images/reorder">(
            "/api/admin/guide-media/images/reorder"
          ),
          {
            method: "POST",
            body: JSON.stringify({
              config_id: activeKey.config_id,
              platform: activeKey.platform,
              client_id: activeKey.client_id,
              image_ids: ids,
            }),
          }
        )
      );
      replaceGallery(result.gallery);
    } catch {
      actionError = at("guide_media_reorder_failed", {}, "Could not reorder screenshots.");
    } finally {
      orderBusy = false;
    }
  }

  async function uploadImage(): Promise<void> {
    if (!activeKey || uploadBusy) return;
    if (!uploadFile) {
      uploadError = at("guide_media_image_missing", {}, "Choose an image first.");
      return;
    }
    uploadBusy = true;
    uploadError = "";
    try {
      unwrap(
        await api(builtApiPath<"/api/admin/guide-media/images">("/api/admin/guide-media/images"), {
          method: "POST",
          body: messageRequestBody(
            {
              config_id: activeKey.config_id,
              platform: activeKey.platform,
              client_id: activeKey.client_id,
              captions: {
                ru: uploadCaptions.ru.trim(),
                en: uploadCaptions.en.trim(),
              },
            },
            uploadFile
          ),
        })
      );
      uploadFile = null;
      uploadCaptions = { ru: "", en: "" };
      await load();
    } catch {
      uploadError = at("guide_media_upload_failed", {}, "Could not upload the screenshot.");
    } finally {
      uploadBusy = false;
    }
  }

  async function deleteImage(): Promise<void> {
    if (!deleteTarget || deleteBusy) return;
    deleteBusy = true;
    deleteError = "";
    try {
      unwrap(
        await api(
          builtApiPath<"/api/admin/guide-media/images/{image_id}">(
            `/api/admin/guide-media/images/${encodeURIComponent(deleteTarget.image_id)}`
          ),
          { method: "DELETE" }
        )
      );
      deleteTarget = null;
      await load();
    } catch {
      deleteError = at("guide_media_delete_failed", {}, "Could not delete the screenshot.");
    } finally {
      deleteBusy = false;
    }
  }
</script>

<div class="guide-media">
  <div class="guide-media-toolbar">
    <p class="guide-media-hint">{at("guide_media_hint")}</p>
    <AdminButton variant="ghost" disabled={loading} onclick={load}>
      <RefreshCw size={15} />{at("refresh")}
    </AdminButton>
  </div>

  {#if loading}
    <AdminEmptyState>{at("loading")}</AdminEmptyState>
  {:else if loadFailed}
    <div class="guide-media-error" role="alert">
      <p>{at("guide_media_load_failed")}</p>
      <AdminButton onclick={load}>{at("refresh")}</AdminButton>
    </div>
  {:else}
    {#if catalogStatus === "unavailable"}
      <div class="guide-media-warning" role="status">
        <strong>{at("guide_media_catalog_unavailable")}</strong>
        {#if catalogError}<span>{catalogError}</span>{/if}
        <p class="guide-media-muted">{at("guide_media_catalog_unavailable_hint")}</p>
      </div>
    {:else if catalogStatus === "empty"}
      <AdminEmptyState>
        <p>{at("guide_media_no_configs")}</p>
        <p class="guide-media-muted">{at("guide_media_catalog_unavailable_hint")}</p>
      </AdminEmptyState>
    {/if}

    {#if problems.length > 0}
      <section class="guide-media-orphans" aria-label={at("guide_media_catalog_problems")}>
        <div class="guide-media-section-head">
          <h3>{at("guide_media_catalog_problems")}</h3>
          <AdminBadge variant="warning">{problems.length}</AdminBadge>
        </div>
        <div class="guide-media-orphan-list">
          {#each problems as problem (problem.source + problem.config_id)}
            <p class="guide-media-warning">
              <strong>{sourceLabel(problem.source)} · {problem.config_id}</strong>
              <span>{problemText(problem)}</span>
            </p>
          {/each}
        </div>
      </section>
    {/if}

    {#if catalogStatus === "ready"}
      <div class="guide-media-selectors">
        <AdminField label={at("guide_media_config")}>
          <AdminSelect
            value={configId}
            ariaLabel={at("guide_media_config")}
            items={catalog.map((item) => ({
              value: item.config_id,
              label: configLabel(item),
              disabled: (item.platforms ?? []).length === 0,
            }))}
            onValueChange={selectConfig}
          />
        </AdminField>
        <AdminField label={at("guide_media_platform")}>
          <AdminSelect
            value={platformId}
            ariaLabel={at("guide_media_platform")}
            disabled={platforms.length === 0}
            items={platforms.map((item) => ({ value: item.id, label: item.label }))}
            onValueChange={selectPlatform}
          />
        </AdminField>
        <AdminField label={at("guide_media_client")}>
          <AdminSelect
            value={clientId}
            ariaLabel={at("guide_media_client")}
            disabled={apps.length === 0}
            items={apps.map((item: CatalogApp) => ({
              value: item.id,
              label: item.ambiguous ? `${item.name} · ${at("guide_media_ambiguous")}` : item.name,
              disabled: item.ambiguous,
            }))}
            onValueChange={selectClient}
          />
        </AdminField>
      </div>
    {/if}

    {#if orphaned.length > 0 && orphanedKnown}
      <section class="guide-media-orphans" aria-label={at("guide_media_orphaned_title")}>
        <div class="guide-media-section-head">
          <h3>{at("guide_media_orphaned_title")}</h3>
          <AdminBadge variant="warning">{orphaned.length}</AdminBadge>
        </div>
        <p class="guide-media-muted">{at("guide_media_orphaned_hint")}</p>
        <div class="guide-media-orphan-list">
          {#each orphaned as key (key.config_id + key.platform + key.client_id)}
            <AdminButton variant="ghost" size="sm" onclick={() => openOrphan(key)}>
              <Monitor size={14} />
              {key.config_id} · {key.platform} · {key.client_id}
            </AdminButton>
          {/each}
        </div>
      </section>
    {:else if !orphanedKnown && storedFallback.length > 0}
      <section class="guide-media-orphans" aria-label={at("guide_media_stored_galleries")}>
        <div class="guide-media-section-head">
          <h3>{at("guide_media_stored_galleries")}</h3>
          <AdminBadge variant="muted">{storedFallback.length}</AdminBadge>
        </div>
        <p class="guide-media-muted">{at("guide_media_orphan_unknown")}</p>
        <div class="guide-media-orphan-list">
          {#each storedFallback as gallery (gallery.config_id + gallery.platform + gallery.client_id)}
            <AdminButton variant="ghost" size="sm" onclick={() => openOrphan(gallery)}>
              <Monitor size={14} />
              {gallery.config_id} · {gallery.platform} · {gallery.client_id}
            </AdminButton>
          {/each}
        </div>
      </section>
    {/if}

    {#if activeKey}
      <section class="guide-media-gallery">
        <div class="guide-media-section-head">
          <div>
            <h2>{at("guide_media_gallery_title")}</h2>
            {#if activeOrphaned}
              <AdminBadge variant="warning">{at("guide_media_orphaned_badge")}</AdminBadge>
            {:else if activeOrphanUnknown}
              <AdminBadge variant="muted">{at("guide_media_orphan_unknown_badge")}</AdminBadge>
            {:else if activeAmbiguous}
              <AdminBadge variant="warning">{at("guide_media_ambiguous")}</AdminBadge>
            {/if}
          </div>
          <div class="guide-media-counter">
            <span
              >{at("guide_media_limit", {
                count: activeImages.length,
                limit: MAX_SCREENSHOTS,
              })}</span
            >
            {#if orphanSelection !== null}
              <AdminButton variant="ghost" size="sm" onclick={closeOrphan}>
                {at("guide_media_back_to_catalog")}
              </AdminButton>
            {/if}
          </div>
        </div>

        {#if activeOrphaned}
          <p class="guide-media-warning" role="status">{at("guide_media_orphaned_hint")}</p>
        {:else if activeOrphanUnknown}
          <p class="guide-media-warning" role="status">{at("guide_media_orphan_unknown")}</p>
        {:else if activeAmbiguous}
          <p class="guide-media-warning" role="status">{at("guide_media_ambiguous_hint")}</p>
        {/if}

        {#if activeImages.length === 0}
          <AdminEmptyState>
            <p>{at("guide_media_empty_gallery")}</p>
            <p class="guide-media-muted">
              {at("guide_media_empty_gallery_hint", { limit: MAX_SCREENSHOTS })}
            </p>
          </AdminEmptyState>
        {:else}
          <div class="guide-media-grid">
            {#each activeImages as image, index (image.image_id)}
              <article class="guide-media-card">
                <button
                  type="button"
                  class="guide-media-thumb"
                  aria-label={at("guide_media_open_image", { index: index + 1 })}
                  onclick={() => (viewerImage = image)}
                >
                  <img
                    src={image.src}
                    alt={at("guide_media_image_alt", { index: index + 1 })}
                    loading="lazy"
                    decoding="async"
                  />
                </button>
                <div class="guide-media-card-body">
                  <span class="guide-media-muted">{imageSizeLabel(image)}</span>
                  <AdminField label={at("guide_media_caption_ru")}>
                    <Input
                      type="text"
                      maxlength={300}
                      value={captionValue(image, "ru")}
                      aria-label={at("guide_media_caption_ru")}
                      oninput={(event) => setCaption(image, "ru", event.currentTarget.value)}
                    />
                  </AdminField>
                  <AdminField label={at("guide_media_caption_en")}>
                    <Input
                      type="text"
                      maxlength={300}
                      value={captionValue(image, "en")}
                      aria-label={at("guide_media_caption_en")}
                      oninput={(event) => setCaption(image, "en", event.currentTarget.value)}
                    />
                  </AdminField>
                  <div class="guide-media-card-actions">
                    <AdminButton
                      variant="ghost"
                      size="sm"
                      disabled={index === 0 || orderBusy}
                      aria-label={at("guide_media_move_up")}
                      title={at("guide_media_move_up")}
                      onclick={() => moveImage(image, -1)}
                    >
                      <ArrowUp size={15} />
                    </AdminButton>
                    <AdminButton
                      variant="ghost"
                      size="sm"
                      disabled={index === activeImages.length - 1 || orderBusy}
                      aria-label={at("guide_media_move_down")}
                      title={at("guide_media_move_down")}
                      onclick={() => moveImage(image, 1)}
                    >
                      <ArrowDown size={15} />
                    </AdminButton>
                  </div>
                  <div class="guide-media-card-actions">
                    <AdminButton
                      variant="ghost"
                      size="sm"
                      disabled={saveBusyId === image.image_id}
                      onclick={() => saveCaptions(image)}
                    >
                      <Save size={15} />
                      {#if saveBusyId === image.image_id}
                        {at("saving")}
                      {:else}
                        {at("save")}
                      {/if}
                    </AdminButton>
                    <AdminButton
                      variant="dangerSoft"
                      size="sm"
                      onclick={() => {
                        deleteError = "";
                        deleteTarget = image;
                      }}
                    >
                      <Trash2 size={15} />
                      {at("delete")}
                    </AdminButton>
                  </div>
                </div>
              </article>
            {/each}
          </div>
        {/if}
        {#if actionError}<p class="guide-media-error-text" role="alert">{actionError}</p>{/if}
      </section>

      {#if orphanSelection === null && !activeAmbiguous && !savedGalleryConfigError && catalogStatus !== "unavailable"}
        <section class="guide-media-upload">
          <div class="guide-media-section-head">
            <h2>{at("guide_media_upload_title")}</h2>
            <span class="guide-media-muted">
              {at("guide_media_limit", { count: activeImages.length, limit: MAX_SCREENSHOTS })}
            </span>
          </div>
          {#if limitReached}
            <p class="guide-media-warning" role="status">
              {at("guide_media_limit_reached", { limit: MAX_SCREENSHOTS })}
            </p>
          {:else}
            <ImageAttachment
              bind:file={uploadFile}
              disabled={uploadBusy}
              labels={{
                drop: at("guide_media_drop_image"),
                choose: at("guide_media_choose_image"),
                upload: at("guide_media_upload"),
                remove: at("guide_media_remove_image"),
                hint: at("guide_media_image_hint"),
                invalidType: at("guide_media_invalid_type"),
                tooLarge: at("guide_media_too_large"),
                previewAlt: at("guide_media_preview_alt"),
              }}
            />
            <div class="guide-media-captions">
              <AdminField label={at("guide_media_caption_ru")}>
                <Input
                  type="text"
                  maxlength={300}
                  value={uploadCaptions.ru}
                  aria-label={at("guide_media_caption_ru")}
                  oninput={(event) => (uploadCaptions.ru = event.currentTarget.value)}
                />
              </AdminField>
              <AdminField label={at("guide_media_caption_en")}>
                <Input
                  type="text"
                  maxlength={300}
                  value={uploadCaptions.en}
                  aria-label={at("guide_media_caption_en")}
                  oninput={(event) => (uploadCaptions.en = event.currentTarget.value)}
                />
              </AdminField>
            </div>
            {#if uploadError}<p class="guide-media-error-text" role="alert">{uploadError}</p>{/if}
            <AdminButton
              variant="primary"
              disabled={uploadBusy || !uploadFile}
              onclick={uploadImage}
            >
              <Upload size={16} />
              {#if uploadBusy}
                {at("guide_media_uploading")}
              {:else}
                {at("guide_media_upload")}
              {/if}
            </AdminButton>
          {/if}
        </section>
      {/if}
    {:else}
      <AdminEmptyState>{at("guide_media_select_hint")}</AdminEmptyState>
    {/if}
  {/if}

  <Dialog
    open={deleteTarget !== null}
    title={at("guide_media_delete_title")}
    closeLabel={at("close")}
    onclose={() => {
      if (!deleteBusy) deleteTarget = null;
    }}
    class="admin-dialog admin-dialog-compact"
    portal
  >
    <div class="guide-media-delete-dialog">
      <p>{at("guide_media_delete_confirm")}</p>
      {#if deleteError}<p class="guide-media-error-text" role="alert">{deleteError}</p>{/if}
      <div class="admin-dialog-actions">
        <AdminButton disabled={deleteBusy} onclick={() => (deleteTarget = null)}>
          {at("cancel")}
        </AdminButton>
        <AdminButton variant="danger" disabled={deleteBusy} onclick={deleteImage}>
          {#if deleteBusy}
            {at("guide_media_deleting")}
          {:else}
            {at("delete")}
          {/if}
        </AdminButton>
      </div>
    </div>
  </Dialog>

  <ImageViewer
    open={viewerImage !== null}
    src={viewerImage?.src ?? ""}
    alt={at("guide_media_image_alt", { index: 1 })}
    title={at("guide_media_gallery_title")}
    closeLabel={at("close")}
    onclose={() => (viewerImage = null)}
  />
</div>

<style>
  .guide-media {
    display: grid;
    gap: 18px;
    min-width: 0;
  }

  .guide-media-toolbar,
  .guide-media-section-head,
  .guide-media-counter {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
  }

  .guide-media-toolbar {
    flex-wrap: wrap;
  }

  .guide-media-section-head {
    flex-wrap: wrap;
  }

  .guide-media-section-head > div:first-child {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    min-width: 0;
  }

  .guide-media-section-head h2,
  .guide-media-section-head h3 {
    margin: 0;
    font-size: 16px;
  }

  .guide-media-hint,
  .guide-media-muted {
    margin: 0;
    color: var(--muted, var(--admin-muted, #6b7280));
    font-size: 13px;
  }

  .guide-media-selectors,
  .guide-media-captions {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
    min-width: 0;
  }

  .guide-media-warning {
    display: grid;
    gap: 4px;
    margin: 0;
    border: 1px solid color-mix(in srgb, var(--warning, #f59e0b) 35%, transparent);
    border-radius: 12px;
    background: color-mix(in srgb, var(--warning, #f59e0b) 10%, transparent);
    padding: 10px 12px;
    color: var(--text, inherit);
    font-size: 13px;
    overflow-wrap: anywhere;
  }

  .guide-media-warning span {
    color: var(--muted, var(--admin-muted, #6b7280));
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .guide-media-error,
  .guide-media-orphans,
  .guide-media-gallery,
  .guide-media-upload {
    display: grid;
    gap: 12px;
    min-width: 0;
    border: 1px solid var(--border, var(--admin-border, rgba(148, 163, 184, 0.34)));
    border-radius: 16px;
    background: var(--surface, var(--admin-surface, transparent));
    padding: 14px;
  }

  .guide-media-error {
    border-color: color-mix(in srgb, var(--danger, #ff5c5c) 35%, transparent);
  }

  .guide-media-error p,
  .guide-media-error-text {
    margin: 0;
    color: var(--danger, var(--admin-danger, #ff5c5c));
    font-size: 13px;
  }

  .guide-media-orphan-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    min-width: 0;
  }

  .guide-media-orphan-list :global(.admin-btn) {
    max-width: 100%;
    overflow-wrap: anywhere;
    white-space: normal;
  }

  .guide-media-counter {
    justify-content: flex-end;
    flex-wrap: wrap;
    color: var(--muted, var(--admin-muted, #6b7280));
    font-size: 12px;
  }

  .guide-media-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 12px;
    min-width: 0;
  }

  .guide-media-card {
    display: grid;
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--border, var(--admin-border, rgba(148, 163, 184, 0.34)));
    border-radius: 14px;
    background: var(--surface-soft, var(--admin-surface-muted, rgba(148, 163, 184, 0.06)));
  }

  .guide-media-thumb {
    display: block;
    width: 100%;
    aspect-ratio: 16 / 10;
    border: 0;
    background: rgba(15, 23, 42, 0.08);
    padding: 0;
    cursor: zoom-in;
  }

  .guide-media-thumb img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .guide-media-card-body {
    display: grid;
    gap: 9px;
    min-width: 0;
    padding: 10px;
  }

  .guide-media-card-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
    min-width: 0;
  }

  .guide-media-delete-dialog {
    display: grid;
    gap: 14px;
  }

  @media (max-width: 520px) {
    .guide-media-grid {
      grid-template-columns: minmax(0, 1fr);
    }

    .guide-media-toolbar,
    .guide-media-section-head,
    .guide-media-counter {
      align-items: flex-start;
    }

    .guide-media-counter {
      justify-content: flex-start;
    }
  }
</style>
