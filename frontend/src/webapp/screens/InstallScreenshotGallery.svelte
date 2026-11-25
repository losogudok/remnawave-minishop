<script lang="ts">
  import { ImageViewer } from "$components/ui/index.js";
  import Button from "$components/ui/button.svelte";
  import { ChevronLeft, ChevronRight, ZoomIn } from "$components/ui/icons.js";
  import type { InstallGuideScreenshotView } from "$lib/webapp/installGuideRuntime.js";
  import type { Translate } from "$lib/webapp/types.js";

  let {
    screenshots,
    style,
    t,
  }: {
    screenshots: InstallGuideScreenshotView[];
    style: string;
    t: Translate;
  } = $props();

  let viewport: HTMLDivElement;
  let scrollIndex = $state(0);
  let viewer = $state<{ src: string; title: string } | null>(null);
  const activeIndex = $derived(Math.min(scrollIndex, screenshots.length - 1));
  const heading = $derived(t("wa_install_screenshots", {}, "Screenshots and steps"));
  const stepCount = $derived(
    t(
      "wa_install_screenshot_count",
      { index: activeIndex + 1, count: screenshots.length },
      `Step ${activeIndex + 1} of ${screenshots.length}`
    )
  );

  function slideSpacing(): number {
    const first = viewport.children[0];
    const second = viewport.children[1];
    return first instanceof HTMLElement && second instanceof HTMLElement
      ? second.offsetLeft - first.offsetLeft
      : 0;
  }

  function updateActiveSlide(): void {
    const spacing = slideSpacing();
    scrollIndex = spacing ? Math.max(0, Math.round(viewport.scrollLeft / spacing)) : 0;
  }

  function selectSlide(index: number): void {
    viewport.scrollTo({ left: index * slideSpacing() });
    updateActiveSlide();
  }

  function closeViewer(): void {
    viewer = null;
    viewport.querySelector<HTMLButtonElement>(".install-gallery-item.active")?.focus({
      preventScroll: true,
    });
  }
</script>

<section class="install-gallery motion-enter-card" {style} aria-label={heading}>
  <div class="gallery-header">
    <div class="gallery-heading">
      <h2>{heading}</h2>
      <span role="status">{stepCount}</span>
    </div>
    <div class="gallery-progress" aria-hidden="true">
      {#each screenshots as _, index (index)}
        <span class:filled={index <= activeIndex}></span>
      {/each}
    </div>
  </div>

  <div class="gallery-stage">
    <div class="gallery-viewport" bind:this={viewport} onscroll={updateActiveSlide}>
      {#each screenshots as shot, index (`${index}:${shot.src}`)}
        {@const active = index === activeIndex}
        {@const step = t("wa_install_screenshot_step", { index: index + 1 }, `Step ${index + 1}`)}
        {@const label = shot.caption ? `${step} · ${shot.caption}` : step}
        {@const caption =
          shot.caption ||
          t("wa_install_screenshot_alt", { index: index + 1 }, `Screenshot ${index + 1}`)}
        <button
          class="install-gallery-item"
          class:active
          type="button"
          tabindex={active ? undefined : -1}
          title={label}
          aria-label={active
            ? t("wa_install_screenshot_open", { index: index + 1 }, `Open screenshot ${index + 1}`)
            : label}
          onclick={() => {
            if (active) viewer = { src: shot.src, title: caption };
            else selectSlide(index);
          }}
        >
          <span class="slide-heading">
            <span class="slide-label">{label}</span>
            <ZoomIn size={15} aria-hidden="true" />
          </span>
          <img src={shot.src} alt={caption} loading="lazy" decoding="async" />
          <span class="slide-footer" aria-hidden="true">{index + 1} / {screenshots.length}</span>
        </button>
      {/each}
    </div>
    {#if screenshots.length > 1}
      <Button
        class="gallery-arrow previous"
        variant="secondary"
        size="icon"
        disabled={activeIndex === 0}
        aria-label={t("wa_install_screenshot_previous", {}, "Previous screenshot")}
        onclick={() => selectSlide(activeIndex - 1)}
      >
        <ChevronLeft size={18} />
      </Button>
      <Button
        class="gallery-arrow next"
        variant="secondary"
        size="icon"
        disabled={activeIndex === screenshots.length - 1}
        aria-label={t("wa_install_screenshot_next", {}, "Next screenshot")}
        onclick={() => selectSlide(activeIndex + 1)}
      >
        <ChevronRight size={18} />
      </Button>
    {/if}
  </div>

  {#if screenshots.length > 1}
    <div class="gallery-steps">
      {#each screenshots as shot, index (`${index}:${shot.src}`)}
        {@const label = shot.caption
          ? `${index + 1}. ${shot.caption}`
          : t("wa_install_screenshot_step", { index: index + 1 }, `Step ${index + 1}`)}
        <Button
          class="gallery-step"
          variant="secondary"
          size="sm"
          aria-current={index === activeIndex ? "step" : undefined}
          title={label}
          onclick={() => selectSlide(index)}
        >
          <span class="step-label">{label}</span>
        </Button>
      {/each}
    </div>
  {/if}
</section>

<ImageViewer
  open={viewer !== null}
  src={viewer?.src ?? ""}
  alt={viewer?.title ?? ""}
  title={viewer?.title ?? ""}
  closeLabel={t("wa_image_viewer_close", {}, "Close image")}
  zoomInLabel={t("wa_image_viewer_zoom_in", {}, "Zoom in")}
  zoomOutLabel={t("wa_image_viewer_zoom_out", {}, "Zoom out")}
  resetLabel={t("wa_image_viewer_reset", {}, "Reset zoom")}
  onclose={closeViewer}
/>

<style>
  .install-gallery {
    --gallery-gutter: clamp(10px, 4%, 18px);
    --slide-width: min(70%, 320px);
    container: install-gallery / inline-size;
    display: grid;
    gap: 16px;
    width: 100%;
    min-width: 0;
    margin-inline: auto;
    padding-block: 16px 10px;
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--panel) 60%, var(--bg));
  }

  .gallery-header {
    display: grid;
    gap: 9px;
    padding-inline: var(--gallery-gutter);
  }

  .gallery-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .gallery-heading h2 {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 0;
    color: var(--text);
    font-size: 15px;
    line-height: 1.2;
    letter-spacing: 0.035em;
    text-transform: uppercase;
  }

  .gallery-heading h2::before {
    flex: 0 0 8px;
    height: 18px;
    border-radius: 999px;
    background: var(--accent);
    content: "";
  }

  .gallery-heading [role="status"] {
    flex: none;
    padding: 3px 10px;
    border: 1px solid color-mix(in srgb, var(--accent) 32%, var(--border));
    border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 10%, var(--panel));
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
  }

  .gallery-progress {
    display: flex;
    gap: 7px;
  }

  .gallery-progress span {
    flex: 1;
    height: 4px;
    border-radius: 999px;
    background: var(--border-strong);
  }

  .gallery-progress span.filled {
    background: var(--accent);
  }

  .gallery-stage {
    position: relative;
    min-width: 0;
  }

  .gallery-viewport {
    position: relative;
    display: flex;
    gap: 12px;
    padding: 9px calc((100% - var(--slide-width)) / 2);
    overflow-x: auto;
    background: color-mix(in srgb, var(--bg) 58%, transparent);
    overscroll-behavior-x: contain;
    scroll-snap-type: x mandatory;
    scrollbar-width: none;
  }

  .install-gallery-item {
    flex: 0 0 100%;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    min-width: 0;
    aspect-ratio: 8 / 13;
    overflow: hidden;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: calc(var(--radius-card) + 8px);
    background: linear-gradient(180deg, var(--panel), var(--panel-2));
    color: var(--text);
    font: inherit;
    text-align: start;
    scroll-snap-align: center;
    cursor: pointer;
    opacity: 0.58;
  }

  .install-gallery-item.active {
    border-color: color-mix(in srgb, var(--accent) 48%, var(--border));
    box-shadow: 0 0 12px color-mix(in srgb, var(--accent) 14%, transparent);
    opacity: 1;
    cursor: zoom-in;
  }

  @container install-gallery (min-width: 600px) {
    .gallery-viewport {
      padding-inline: var(--gallery-gutter) calc(100% - var(--slide-width) - var(--gallery-gutter));
      scroll-padding-inline-start: var(--gallery-gutter);
    }

    .install-gallery-item {
      scroll-snap-align: start;
    }
  }

  .install-gallery-item:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .slide-heading {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 11px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--panel-3);
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
  }

  .slide-heading::before {
    flex: 0 0 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 7px currentColor;
    content: "";
  }

  .slide-heading :global(svg) {
    flex: none;
    margin-inline-start: auto;
    color: var(--muted);
  }

  .slide-label,
  .step-label {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .install-gallery-item img {
    display: block;
    width: 100%;
    height: 100%;
    min-height: 0;
    object-fit: contain;
  }

  .slide-footer {
    margin: 0 14px;
    padding: 9px 0 14px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font: 600 11px var(--font-mono);
    text-align: end;
  }

  .gallery-stage :global(.gallery-arrow) {
    position: absolute;
    top: 50%;
    translate: 0 -50%;
    border-radius: 50%;
    background: var(--bg);
  }

  .gallery-stage :global(.gallery-arrow:disabled) {
    opacity: 0.35;
    cursor: default;
  }

  .gallery-stage :global(.gallery-arrow.previous) {
    inset-inline-start: 8px;
  }

  .gallery-stage :global(.gallery-arrow.next) {
    inset-inline-end: 8px;
  }

  .gallery-stage :global(.gallery-arrow:dir(rtl) svg) {
    scale: -1 1;
  }

  .gallery-steps {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 90px), 1fr));
    gap: 8px;
    margin-block-start: 6px;
    padding-inline: var(--gallery-gutter);
  }

  .gallery-steps :global(.gallery-step) {
    min-width: 0;
    min-height: 38px;
    padding-inline: 4px;
    border-radius: calc(var(--radius-control) + 5px);
    box-shadow: none;
    color: var(--muted);
    font-size: clamp(11px, 3.1vw, 13px);
    font-weight: 500;
  }

  .gallery-steps :global(.gallery-step[aria-current="step"]) {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, var(--panel));
    color: var(--accent);
    font-weight: 700;
  }
</style>
