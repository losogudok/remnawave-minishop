<script lang="ts">
  import { ArrowLeft, ArrowRight, X } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Plate from "$components/ui/plate.svelte";
  import { Popover } from "$components/ui/primitives.js";
  import type { Translate } from "$lib/webapp/types.js";

  export type PartnerTourStep = {
    /** `data-tour` value of the element this step explains. */
    target: string;
    titleKey: string;
    textKey: string;
  };

  type Rect = { top: number; left: number; width: number; height: number };

  let {
    steps = [],
    step = $bindable(0),
    t = (key: string) => key,
  }: {
    steps?: PartnerTourStep[];
    /** 1-based active step; `0` closes the tour. */
    step?: number;
    t?: Translate;
  } = $props();

  const total = $derived(steps.length);
  const open = $derived(step > 0 && step <= total);
  const current = $derived(open ? steps[step - 1] : null);

  const SPOTLIGHT_PADDING = 8;
  /** Keep the bubble and its anchor away from the very edge of the screen. */
  const VIEWPORT_PADDING = 12;
  const SIDE_OFFSET = 14;
  /** How still the page must be before the tour comes back. */
  const SETTLE_MS = 130;
  const FALLBACK_BUBBLE_HEIGHT = 200;

  let spotlight = $state<Rect>({ top: 0, left: 0, width: 0, height: 0 });
  /** Plain mirror of `spotlight`. Reading the state inside the step effect that
   *  also writes it is the `effect_update_depth_exceeded` trap. */
  let lastRect: Rect = { top: 0, left: 0, width: 0, height: 0 };
  /** Drives the fade of both layers; nothing ever animates its position. */
  let shown = $state(false);
  let side = $state<"top" | "bottom">("bottom");
  let arrowX = $state("50%");
  let bubble = $state<HTMLElement | null>(null);
  let anchorRect: Rect = { top: 0, left: 0, width: 0, height: 0 };
  /** Distance floating-ui actually leaves between anchor and bubble, learned
   *  from the rendered result — the notch and the offset middleware both add to
   *  `sideOffset`, so assuming the raw value under-reserves space. */
  let bubbleGap = SIDE_OFFSET;
  let settleTimer = 0;
  let revealRaf = 0;

  function findTarget(selector: string): HTMLElement | null {
    if (typeof document === "undefined") return null;
    return document.querySelector<HTMLElement>(`[data-tour="${selector}"]`);
  }

  function targetRect(): Rect | null {
    const element = current ? findTarget(current.target) : null;
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      top: rect.top - SPOTLIGHT_PADDING,
      left: rect.left - SPOTLIGHT_PADDING,
      width: rect.width + SPOTLIGHT_PADDING * 2,
      height: rect.height + SPOTLIGHT_PADDING * 2,
    };
  }

  function clamp(value: number, min: number, max: number): number {
    return Math.min(Math.max(value, min), Math.max(min, max));
  }

  /**
   * The cut-out never leaves the screen. An element at the very end of the page
   * cannot always be scrolled fully into view, and a ring running off the fold
   * reads as a bug; showing the visible slice of it reads as deliberate.
   */
  function clampToViewport(rect: Rect): Rect {
    const top = Math.max(VIEWPORT_PADDING, rect.top);
    const left = Math.max(VIEWPORT_PADDING, rect.left);
    const bottom = Math.min(window.innerHeight - VIEWPORT_PADDING, rect.top + rect.height);
    const right = Math.min(window.innerWidth - VIEWPORT_PADDING, rect.left + rect.width);
    return {
      top,
      left,
      width: Math.max(0, right - left),
      height: Math.max(0, bottom - top),
    };
  }

  /** Zero-size box at the same centre: the dim stays, the hole closes. */
  function collapsed(rect: Rect): Rect {
    return {
      top: rect.top + rect.height / 2,
      left: rect.left + rect.width / 2,
      width: 0,
      height: 0,
    };
  }

  /**
   * Works out where the bubble can fit and hands floating-ui an anchor that
   * guarantees it. Two things break a plain "put it below the element":
   *
   * - a card taller than the screen has its real bottom edge off the fold, so
   *   "below" is off-screen;
   * - floating-ui's `shift` only slides along the cross axis, so it cannot
   *   rescue a vertical overflow on its own.
   *
   * So the anchor is clipped to the visible slice of the element, and when
   * neither side has room its bottom edge is pulled up until the bubble fits.
   * The bubble then overlaps the lower part of a tall card, which is what every
   * coach-mark library does there; the cut-out still shows which card is meant.
   */
  function placeBubble(rect: Rect): void {
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;
    const visibleTop = Math.max(VIEWPORT_PADDING, Math.min(rect.top, viewportHeight));
    const visibleBottom = Math.min(
      viewportHeight - VIEWPORT_PADDING,
      Math.max(rect.top + rect.height, VIEWPORT_PADDING)
    );
    const left = Math.max(0, Math.min(rect.left, viewportWidth));
    const right = Math.min(viewportWidth, Math.max(rect.left + rect.width, 0));

    const box = bubble?.getBoundingClientRect();
    if (box && box.height > 0) {
      const gap =
        side === "bottom"
          ? box.top - (anchorRect.top + anchorRect.height)
          : anchorRect.top - (box.top + box.height);
      if (gap > 0 && gap < 80) bubbleGap = gap;
    }
    const reserve = (box?.height || FALLBACK_BUBBLE_HEIGHT) + bubbleGap + VIEWPORT_PADDING;

    let top = visibleTop;
    let bottom = visibleBottom;
    let nextSide: "top" | "bottom" = "bottom";
    if (viewportHeight - visibleBottom < reserve) {
      if (visibleTop >= reserve) {
        nextSide = "top";
      } else {
        bottom = Math.max(VIEWPORT_PADDING + 1, viewportHeight - reserve);
        top = Math.min(top, bottom - 1);
      }
    }
    if (nextSide !== side) side = nextSide;
    anchorRect = {
      top,
      left,
      width: Math.max(1, right - left),
      height: Math.max(1, bottom - top),
    };
  }

  /** Point the notch at the element, not at the middle of a shifted bubble. */
  function aimArrow(rect: Rect): void {
    const box = bubble?.getBoundingClientRect();
    if (!box || box.width <= 0) {
      arrowX = "50%";
      return;
    }
    const centre = clamp(rect.left + rect.width / 2, box.left + 18, box.right - 18);
    arrowX = `${Math.round(centre - box.left)}px`;
  }

  const virtualAnchor = {
    getBoundingClientRect(): DOMRect {
      return new DOMRect(anchorRect.left, anchorRect.top, anchorRect.width, anchorRect.height);
    },
  };

  /**
   * Closes the cut-out without touching the dim. The scrim rides on the same
   * element as the hole, so fading the element out would undim the page between
   * steps — collapsing the hole instead keeps the page dimmed throughout.
   */
  function hide(): void {
    shown = false;
    setSpotlight(collapsed(lastRect));
  }

  function setSpotlight(rect: Rect): void {
    lastRect = rect;
    spotlight = rect;
  }

  /**
   * The tour never chases the page. Any scroll closes it, and once the page is
   * still again it re-measures and opens at the new place: a cut-out that is
   * always exactly on its element beats one that is smoothly late.
   *
   * Three frames, because each needs the previous one's layout: park the closed
   * hole on the new target, let floating-ui move the bubble, then open the hole
   * and aim the notch.
   */
  function reveal(): void {
    cancelAnimationFrame(revealRaf);
    const rect = targetRect();
    if (!rect) {
      hide();
      return;
    }
    const first = clampToViewport(rect);
    setSpotlight(collapsed(first));
    placeBubble(first);
    revealRaf = requestAnimationFrame(() => {
      const settled = clampToViewport(targetRect() ?? rect);
      placeBubble(settled);
      revealRaf = requestAnimationFrame(() => {
        setSpotlight(settled);
        aimArrow(settled);
        shown = true;
      });
    });
  }

  function scheduleReveal(): void {
    clearTimeout(settleTimer);
    settleTimer = window.setTimeout(reveal, SETTLE_MS);
  }

  function handleViewportChange(): void {
    hide();
    scheduleReveal();
  }

  $effect(() => {
    if (!open || !current) {
      hide();
      return;
    }
    const element = findTarget(current.target);
    if (!element) return;
    hide();
    // A card taller than half the screen is scrolled to the top so the bubble
    // has room under it; a short one is centred. Either way the page may run
    // out of scroll before the element fits — `clampToViewport` handles that.
    const tall = element.getBoundingClientRect().height > window.innerHeight * 0.5;
    element.scrollIntoView({ block: tall ? "start" : "center", behavior: "smooth" });
    // The scroll may be a no-op when the element is already in place, so the
    // reveal is armed here as well as by the scroll listener.
    scheduleReveal();
  });

  $effect(() => {
    if (!open || typeof window === "undefined") return;
    // Capture phase, so scrolling inside a nested container counts too.
    window.addEventListener("scroll", handleViewportChange, { capture: true, passive: true });
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("orientationchange", handleViewportChange);
    return () => {
      window.removeEventListener("scroll", handleViewportChange, { capture: true });
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("orientationchange", handleViewportChange);
      clearTimeout(settleTimer);
      cancelAnimationFrame(revealRaf);
    };
  });

  function close(): void {
    step = 0;
  }

  function back(): void {
    if (step > 1) step -= 1;
  }

  function next(): void {
    step = step >= total ? 0 : step + 1;
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      next();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      back();
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open && current}
  <!-- One element carries the whole scrim: a huge outer shadow dims the page
       while the element being explained stays untouched inside the cut-out. -->
  <div
    class="partner-tour-spotlight"
    class:is-ready={shown}
    style={`top:${spotlight.top}px; left:${spotlight.left}px; width:${spotlight.width}px; height:${spotlight.height}px;`}
    aria-hidden="true"
  ></div>

  <Popover.Root open={true}>
    <Popover.Portal>
      <Popover.Content
        bind:ref={bubble}
        class={shown ? "partner-tour-popover is-shown" : "partner-tour-popover"}
        customAnchor={virtualAnchor}
        {side}
        align="center"
        sideOffset={SIDE_OFFSET}
        collisionPadding={VIEWPORT_PADDING}
        updatePositionStrategy="always"
        interactOutsideBehavior="ignore"
        escapeKeydownBehavior="ignore"
        trapFocus={false}
        onOpenAutoFocus={(event: Event) => event.preventDefault()}
        onCloseAutoFocus={(event: Event) => event.preventDefault()}
      >
        <Plate
          class="partner-tour-plate"
          arrow={side === "bottom" ? "top" : "bottom"}
          {arrowX}
          visible={shown}
        >
          <div class="partner-tour-head">
            <span class="partner-tour-step">
              {t("wa_partner_tutorial_progress", { step, total })}
            </span>
            <button type="button" onclick={close} aria-label={t("wa_partner_tutorial_skip")}>
              <X size={16} />
            </button>
          </div>
          <h2>{t(current.titleKey)}</h2>
          <p>{t(current.textKey)}</p>
          <div class="partner-tour-dots" aria-hidden="true">
            {#each steps as _item, index (index)}
              <span class:active={index + 1 === step}></span>
            {/each}
          </div>
          <div class="partner-tour-actions">
            {#if step > 1}
              <Button variant="outline" size="sm" onclick={back}>
                <ArrowLeft size={15} />{t("wa_back")}
              </Button>
            {:else}
              <Button variant="outline" size="sm" onclick={close}>
                {t("wa_partner_tutorial_skip")}
              </Button>
            {/if}
            <Button size="sm" onclick={next}>
              {step === total ? t("wa_partner_tutorial_done") : t("wa_next")}
              <ArrowRight size={15} />
            </Button>
          </div>
        </Plate>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
{/if}
