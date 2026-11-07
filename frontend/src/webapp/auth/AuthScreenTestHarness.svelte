<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { Tooltip } from "$components/ui/primitives.js";
  import AuthScreen from "./AuthScreen.svelte";

  let { ...props }: ComponentProps<typeof AuthScreen> = $props();
</script>

<Tooltip.Provider>
  <AuthScreen {...props} />
</Tooltip.Provider>
