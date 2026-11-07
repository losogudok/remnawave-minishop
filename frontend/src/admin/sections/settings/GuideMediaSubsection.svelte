<script lang="ts">
  import SettingsDisclosureTrigger from "./SettingsDisclosureTrigger.svelte";
  import GuideMediaTab from "./GuideMediaTab.svelte";
  import { settingsSubsectionAnchorKey } from "$lib/admin/settingsSections";

  let {
    at,
    contentId,
    open,
    onToggle,
  }: {
    at: (key: string, params?: Record<string, unknown>, fallback?: string) => string;
    contentId: string;
    open: boolean;
    onToggle: () => void;
  } = $props();
</script>

<div class="admin-subsection-accordion">
  <section class="admin-settings-subsection">
    <SettingsDisclosureTrigger
      anchorKey={settingsSubsectionAnchorKey("subscription_guides", "screenshots")}
      {contentId}
      countLabel=""
      level="subsection"
      {onToggle}
      {open}
      title={at("guide_media_tab", {}, "Screenshots")}
    />
    {#if open}
      <div id={contentId} class="admin-accordion-content" data-state="open">
        <div class="admin-settings-subsection-body">
          <GuideMediaTab {at} />
        </div>
      </div>
    {/if}
  </section>
</div>
