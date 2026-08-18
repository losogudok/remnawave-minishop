<script lang="ts">
  import { AnimatedPrice } from "$components/patterns/webapp/index.js";
  import type { PlanView } from "$lib/webapp/types.js";

  let {
    plan = null,
    promoPlans = null,
    unitPricePlan = null,
    unitPriceSuffix = "",
    method = "",
    replaceAnimations = false,
  }: {
    plan?: PlanView | null;
    promoPlans?: { base: PlanView; discounted: PlanView } | null;
    unitPricePlan?: PlanView | null;
    unitPriceSuffix?: string;
    method?: string;
    replaceAnimations?: boolean;
  } = $props();
</script>

{#if promoPlans}
  <span class="promo-price-pair">
    <s><AnimatedPrice plan={promoPlans.base} {method} {replaceAnimations} /></s>
    <b><AnimatedPrice plan={promoPlans.discounted} {method} {replaceAnimations} /></b>
  </span>
{:else}
  <span><AnimatedPrice {plan} {method} {replaceAnimations} /></span>
{/if}
{#if unitPricePlan}
  <small class="period-unit-price">
    <AnimatedPrice plan={unitPricePlan} {method} {replaceAnimations} />
    <span>{unitPriceSuffix}</span>
  </small>
{/if}
