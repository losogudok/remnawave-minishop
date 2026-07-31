<script lang="ts">
  import RichTextEditor from "$lib/richtext/RichTextEditor.svelte";
  import type { MessageShortcodeInfo } from "$lib/richtext/editorSchema";

  import { adminRichTextLabels } from "../richTextLabels.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

  let {
    value,
    onInput,
    shortcodes,
    onRequestShortcodes,
    at,
    placeholder = "",
  }: {
    value: string;
    onInput: (value: string) => void;
    shortcodes: MessageShortcodeInfo[];
    onRequestShortcodes: () => void;
    at: TranslateFn;
    placeholder?: string;
  } = $props();

  const labels = $derived(adminRichTextLabels(at));
</script>

<RichTextEditor
  {value}
  {onInput}
  {labels}
  {placeholder}
  {shortcodes}
  {onRequestShortcodes}
  showSource
/>
