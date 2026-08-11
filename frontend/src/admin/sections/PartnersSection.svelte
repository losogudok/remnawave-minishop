<script lang="ts">
  import { onMount } from "svelte";
  import {
    ArrowRight,
    CheckCircle2,
    Coins,
    CreditCard,
    FileText,
    History,
    Plus,
    RefreshCw,
    Settings,
    TrendingUp,
    TriangleAlert,
    UserPlus,
    UsersRound,
    WalletCards,
  } from "$components/ui/icons.js";
  import { Tabs } from "$components/ui/primitives.js";
  import {
    AdminBadge,
    AdminButton,
    AdminEmptyState,
    AdminSelect,
  } from "$components/patterns/admin/index.js";
  import { formatPartnerMoney, partnerStatusVariant } from "$lib/admin/partnerProgramUi.js";
  import { stripRoutePrefix, withRoutePrefix } from "$lib/webapp/routes.js";
  import { getSettingsStore } from "$lib/admin/context.js";
  import {
    applications as previewApplications,
    partnerAudit as previewPartnerAudit,
    partnerClients as previewPartnerClients,
    partnerCommissions as previewPartnerCommissions,
    partnerLedger as previewPartnerLedger,
    partnerLinks as previewPartnerLinks,
    partnerPayoutsDaily as previewPartnerPayoutsDaily,
    partnerRevenueDaily as previewPartnerRevenueDaily,
    partners as previewPartners,
    withdrawals as previewWithdrawals,
    type ApplicationRow,
    type PartnerAuditRow,
    type PartnerChartPoint,
    type PartnerClientRow,
    type PartnerCommissionRow,
    type PartnerLedgerRow,
    type PartnerRow,
    type WithdrawalRow,
  } from "$lib/admin/previewMock/partnerProgram.js";
  import type { AdminApi } from "../adminStores.js";
  import {
    loadPartnerDashboard,
    loadPartnerDetail,
    loadPartnerLists,
    type AdminPartnerDashboard,
    type PartnerLinkRow,
  } from "$lib/admin/partnerProgramApi.js";
  import PartnerProgramCharts from "./partners/PartnerProgramCharts.svelte";
  import PartnerProgramSkeleton from "./partners/PartnerProgramSkeleton.svelte";
  import PartnerProgramTables from "./partners/PartnerProgramTables.svelte";
  import PartnerActionDialog from "./partners/PartnerActionDialog.svelte";
  import PartnerOperationDetails from "./partners/PartnerOperationDetails.svelte";
  import PartnerReferralImportBanner from "./partners/PartnerReferralImportBanner.svelte";
  import "./partnersSection.css";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type View =
    | "dashboard"
    | "partners"
    | "applications"
    | "withdrawals"
    | "partner_detail"
    | "application_detail"
    | "withdrawal_detail";
  type DialogKind = "" | "create" | "rate" | "balance" | "import" | "status" | "link";
  type IconComponent = typeof UsersRound;

  let {
    api,
    at,
    routePrefix = "",
    onOpenSettingsPath = () => {},
    onOpenUserCard = () => {},
    onOpenPaymentCard = () => {},
  }: {
    api: AdminApi;
    at: TranslateFn;
    routePrefix?: string;
    onOpenSettingsPath?: (path?: unknown) => void;
    onOpenUserCard?: (userId: number) => void;
    onOpenPaymentCard?: (paymentId: number) => void;
  } = $props();

  const adminScenario = initialScenario();
  const previewMode = Boolean(adminScenario);
  const emptyChartsPreview = adminScenario === "empty_charts";
  const settingsStore = getSettingsStore();
  const referralProgramEnabled = $derived.by(() => {
    const field = settingsStore.settingsSections
      .flatMap((section) => section.fields || [])
      .find((item) => item.key === "REFERRAL_PROGRAM_ENABLED");
    return field ? Boolean(field.value) : true;
  });
  let currency = $state("RUB");
  let partners = $state<PartnerRow[]>(
    previewMode ? previewPartners.map((item) => ({ ...item })) : []
  );
  let applications = $state<ApplicationRow[]>(
    previewMode ? previewApplications.map((item) => ({ ...item })) : []
  );
  let withdrawals = $state<WithdrawalRow[]>(
    previewMode ? previewWithdrawals.map((item) => ({ ...item })) : []
  );
  let partnerLinks = $state<PartnerLinkRow[]>(
    previewMode
      ? previewPartnerLinks.map((item) => ({
          ...item,
          id: item.id as PartnerLinkRow["id"],
        }))
      : []
  );
  let partnerClients = $state<PartnerClientRow[]>(previewMode ? [...previewPartnerClients] : []);
  let partnerCommissions = $state<PartnerCommissionRow[]>(
    previewMode ? [...previewPartnerCommissions] : []
  );
  let partnerLedger = $state<PartnerLedgerRow[]>(previewMode ? [...previewPartnerLedger] : []);
  let partnerAudit = $state<PartnerAuditRow[]>(previewMode ? [...previewPartnerAudit] : []);
  let partnerRevenueDaily = $state<PartnerChartPoint[]>(
    previewMode
      ? previewPartnerRevenueDaily.map((item) =>
          emptyChartsPreview ? { ...item, amount: 0 } : { ...item }
        )
      : []
  );
  let partnerPayoutsDaily = $state<PartnerChartPoint[]>(
    previewMode
      ? previewPartnerPayoutsDaily.map((item) =>
          emptyChartsPreview ? { ...item, amount: 0 } : { ...item }
        )
      : []
  );
  let dashboard = $state<AdminPartnerDashboard | null>(null);
  let loading = $state(!previewMode);
  let loadError = $state(false);

  const dashboardMetrics: Array<{
    key: string;
    value: string;
    icon: IconComponent;
    tone: string;
  }> = $derived([
    {
      key: "active",
      value:
        adminScenario === "empty"
          ? "0 / 0"
          : dashboard
            ? `${dashboard.active} / ${dashboard.paused}`
            : "18 / 2",
      icon: UsersRound,
      tone: "success",
    },
    {
      key: "clients",
      value: adminScenario === "empty" ? "0" : dashboard ? String(dashboard.clients) : "247",
      icon: UserPlus,
      tone: "info",
    },
    {
      key: "gross",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.gross ?? 482700)),
      icon: TrendingUp,
      tone: "success",
    },
    {
      key: "commissions",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.commissions ?? 144810)),
      icon: Coins,
      tone: "warning",
    },
    {
      key: "paid",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.paid ?? 87300)),
      icon: WalletCards,
      tone: "muted",
    },
    {
      key: "available",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.available ?? 42840)),
      icon: CreditCard,
      tone: "info",
    },
    {
      key: "requested",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.requested ?? 11700)),
      icon: History,
      tone: "warning",
    },
    {
      key: "hold",
      value: money(adminScenario === "empty" ? 0 : (dashboard?.hold ?? 9340)),
      icon: FileText,
      tone: "muted",
    },
  ]);
  const emptyPartner: PartnerRow = {
    id: "",
    userId: 0,
    name: "",
    handle: "",
    status: "closed",
    rate: 0,
    clients: 0,
    payments: 0,
    gross: 0,
    earned: 0,
    available: 0,
    activated: "",
  };
  const emptyApplication: ApplicationRow = {
    id: "",
    userId: 0,
    user: "",
    handle: "",
    submitted: "",
    status: "canceled",
    messageKey: "",
  };
  const emptyWithdrawal: WithdrawalRow = {
    id: "",
    partnerId: "",
    partner: "",
    handle: "",
    method: "bank_card",
    masked: "",
    amount: 0,
    status: "failed",
    requested: "",
    processedAt: "",
    noteKey: "",
  };

  const initialRoute = routeStateFromLocation();
  let view = $state<View>(initialRoute.view);
  let selectedPartner = $state<PartnerRow>(
    (previewMode
      ? previewPartners.find(
          (partner) => partner.id.toLowerCase() === initialRoute.id.toLowerCase()
        ) || previewPartners[0]
      : null) || emptyPartner
  );
  let selectedApplication = $state<ApplicationRow>(
    (previewMode
      ? previewApplications.find(
          (application) => application.id.toLowerCase() === initialRoute.id.toLowerCase()
        ) || previewApplications[0]
      : null) || emptyApplication
  );
  let selectedWithdrawal = $state<WithdrawalRow>(
    (previewMode
      ? previewWithdrawals.find(
          (withdrawal) => withdrawal.id.toLowerCase() === initialRoute.id.toLowerCase()
        ) || previewWithdrawals[0]
      : null) || emptyWithdrawal
  );
  // "Change status" said nothing about what it does. The label names the
  // transition the click performs, and flips with the partner's state.
  const partnerStatusActionLabel = $derived(
    selectedPartner.status === "paused"
      ? at("partners_action_resume", {}, "Resume partnership")
      : at("partners_action_pause", {}, "Pause partnership")
  );
  const pendingApplications = $derived(
    applications.filter((application) => application.status === "pending")
  );
  const pendingApplicationCount = $derived(pendingApplications.length);
  const openWithdrawalCount = $derived(
    withdrawals.filter(
      (withdrawal) => withdrawal.status === "requested" || withdrawal.status === "processing"
    ).length
  );
  let dialog = $state<DialogKind>("");
  let dialogReason = $state("");
  let dialogAmount = $state("");
  const balanceModes = ["add", "subtract", "set"] as const;
  let balanceMode = $state<(typeof balanceModes)[number]>("add");
  const balancePreviewValue = $derived.by(() => {
    const amount = Number(dialogAmount || 0);
    if (balanceMode === "set") return amount;
    if (balanceMode === "subtract") return selectedPartner.available - amount;
    return selectedPartner.available + amount;
  });
  let decisionOutcome = $state<"approved" | "rejected">("approved");
  let approvalRate = $state("30");
  let approvalWelcome = $state("");
  let rejectMessage = $state("");
  let actionStatus = $state("");
  let actionBusy = $state(false);
  let createUserId = $state("");
  let createRate = $state("30");
  let dialogRate = $state("30");
  let importOnCreate = $state(false);
  let importPreview = $state({ found: 0, new_clients: 0, existing: 0, conflicts: 0 });
  let revealedRequisites = $state("");

  function initialScenario(): string {
    if (typeof window === "undefined") return "populated";
    return String(
      new URLSearchParams(window.location.search).get("partner_admin_scenario") || ""
    ).toLowerCase();
  }

  function routeStateFromLocation(): { id: string; view: View } {
    if (typeof window === "undefined") return { id: "", view: "dashboard" };
    const path = stripRoutePrefix(window.location.pathname, routePrefix);
    const detailMatch = path.match(
      /^\/admin\/partners\/(partner|applications|withdrawals)\/([^/]+)$/i
    );
    if (detailMatch) {
      const detailView: Record<string, View> = {
        applications: "application_detail",
        partner: "partner_detail",
        withdrawals: "withdrawal_detail",
      };
      return {
        id: decodeURIComponent(detailMatch[2]),
        view: detailView[detailMatch[1].toLowerCase()] || "dashboard",
      };
    }
    const listMatch = path.match(/^\/admin\/partners\/(partners|applications|withdrawals)$/i);
    if (listMatch) {
      return { id: "", view: listMatch[1].toLowerCase() as View };
    }
    const queryView = new URLSearchParams(window.location.search).get("partner_admin_view");
    if (queryView === "partners" || queryView === "applications" || queryView === "withdrawals")
      return { id: "", view: queryView };
    if (
      queryView === "partner_detail" ||
      queryView === "application_detail" ||
      queryView === "withdrawal_detail"
    )
      return { id: "", view: queryView };
    return { id: "", view: "dashboard" };
  }

  function syncRouteView(): void {
    const route = routeStateFromLocation();
    const routeId = route.id.toLowerCase();
    if (route.view === "partner_detail") {
      selectedPartner =
        partners.find((partner) => partner.id.toLowerCase() === routeId) || selectedPartner;
    } else if (route.view === "application_detail") {
      selectedApplication =
        applications.find((application) => application.id.toLowerCase() === routeId) ||
        selectedApplication;
    } else if (route.view === "withdrawal_detail") {
      selectedWithdrawal =
        withdrawals.find((withdrawal) => withdrawal.id.toLowerCase() === routeId) ||
        selectedWithdrawal;
    }
    view = route.view;
    if (!previewMode && route.view === "partner_detail" && route.id) {
      void loadSelectedPartnerDetail(route.id);
    }
  }

  function money(value: number): string {
    return formatPartnerMoney(value, currency);
  }

  type PartnerRequestOptions = {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  };

  async function request(
    path: string,
    options: PartnerRequestOptions = {}
  ): Promise<Record<string, unknown>> {
    const call = api as unknown as (
      path: string,
      options?: PartnerRequestOptions
    ) => Promise<Record<string, unknown>>;
    return call(path, options);
  }

  async function post(
    path: string,
    payload?: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    return request(path, {
      method: "POST",
      headers: payload ? { "Content-Type": "application/json" } : undefined,
      body: payload ? JSON.stringify(payload) : undefined,
    });
  }

  function idempotencyKey(prefix: string): string {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    return `${prefix}-${random}`;
  }

  async function loadSelectedPartnerDetail(partnerId = selectedPartner.id): Promise<void> {
    if (previewMode || !partnerId) return;
    const path = `/admin/partners/${encodeURIComponent(partnerId)}?currency=${encodeURIComponent(currency)}`;
    const detail = await loadPartnerDetail(api, partnerId, currency, path);
    selectedPartner = detail.partner;
    partnerLinks = detail.links;
    partnerClients = detail.clients;
    partnerCommissions = detail.commissions;
    partnerLedger = detail.ledger;
    partnerAudit = detail.audit;
    const otherWithdrawals = withdrawals.filter((item) => item.partnerId !== partnerId);
    withdrawals = [...detail.withdrawals, ...otherWithdrawals];
  }

  async function refreshAll(): Promise<void> {
    if (previewMode) return;
    loading = true;
    loadError = false;
    try {
      const [nextDashboard, lists] = await Promise.all([
        loadPartnerDashboard(api, currency),
        loadPartnerLists(api, currency),
      ]);
      dashboard = nextDashboard;
      partnerRevenueDaily = nextDashboard.revenue;
      partnerPayoutsDaily = nextDashboard.payouts;
      partners = lists.partners;
      applications = lists.applications;
      withdrawals = lists.withdrawals;
      selectedPartner =
        partners.find((item) => item.id === selectedPartner.id) || partners[0] || emptyPartner;
      selectedApplication =
        applications.find((item) => item.id === selectedApplication.id) ||
        applications[0] ||
        emptyApplication;
      selectedWithdrawal =
        withdrawals.find((item) => item.id === selectedWithdrawal.id) ||
        withdrawals[0] ||
        emptyWithdrawal;
      if (view === "partner_detail" && selectedPartner.id) {
        await loadSelectedPartnerDetail(selectedPartner.id);
      }
    } catch {
      loadError = true;
    } finally {
      loading = false;
    }
  }

  async function changeCurrency(value: string): Promise<void> {
    currency = value;
    if (!previewMode) await refreshAll();
  }

  function statusLabel(status: string): string {
    return at(`partners_status_${status}`, {}, status);
  }

  function navigate(next: View, id = ""): void {
    if (next !== "withdrawal_detail") revealedRequisites = "";
    view = next;
    if (typeof window === "undefined" || window.location.protocol === "file:") return;
    let suffix = "";
    if (next === "partner_detail")
      suffix = `/partner/${encodeURIComponent(id || selectedPartner.id)}`;
    else if (next === "application_detail")
      suffix = `/applications/${encodeURIComponent(id || selectedApplication.id)}`;
    else if (next === "withdrawal_detail")
      suffix = `/withdrawals/${encodeURIComponent(id || selectedWithdrawal.id)}`;
    else if (next === "partners" || next === "applications" || next === "withdrawals")
      suffix = `/${next}`;
    const target = withRoutePrefix(`/admin/partners${suffix}`, routePrefix);
    const query = new URLSearchParams(window.location.search);
    query.delete("partner_admin_view");
    const search = query.size ? `?${query.toString()}` : "";
    window.history.pushState(null, "", `${target}${search}${window.location.hash}`);
  }

  function openPartnerById(partnerId: string): void {
    const target = partners.find((partner) => partner.id === partnerId);
    if (target) openPartner(target);
  }

  function openPartner(partner: (typeof partners)[number]): void {
    selectedPartner = partner;
    navigate("partner_detail", partner.id);
    if (!previewMode) void loadSelectedPartnerDetail(partner.id);
  }

  function openApplication(application: (typeof applications)[number]): void {
    selectedApplication = application;
    navigate("application_detail", application.id);
  }

  function openWithdrawal(withdrawal: (typeof withdrawals)[number]): void {
    revealedRequisites = "";
    selectedWithdrawal = withdrawal;
    navigate("withdrawal_detail", withdrawal.id);
  }

  async function completeDialog(): Promise<void> {
    if (previewMode) {
      actionStatus = at("partners_action_saved", {}, "Changes saved in the prototype");
      dialog = "";
      dialogReason = "";
      dialogAmount = "";
      return;
    }
    if (actionBusy) return;
    actionBusy = true;
    try {
      if (dialog === "create") {
        const created = await post("/admin/partners", {
          user_id: Number(createUserId),
          commission_bps: Math.round(Number(createRate) * 100),
          welcome_message: null,
        });
        const createdProfile = created.partner as Record<string, unknown> | undefined;
        if (importOnCreate && createdProfile?.partner_id) {
          await post(`/admin/partners/${createdProfile.partner_id}/referral-import`, {
            confirm_without_retroactive_commission: true,
          });
        }
      } else if (dialog === "rate") {
        await post(`/admin/partners/${selectedPartner.id}/commission-rate`, {
          commission_bps: Math.round(Number(dialogRate) * 100),
          reason: dialogReason.trim(),
        });
      } else if (dialog === "balance") {
        const scale = selectedPartner.currencyScale ?? 2;
        await post(`/admin/partners/${selectedPartner.id}/balance-adjustments`, {
          currency,
          currency_scale: scale,
          mode: balanceMode,
          amount_minor: Math.round(Number(dialogAmount) * 10 ** scale),
          reason: dialogReason.trim(),
          idempotency_key: idempotencyKey("admin-balance"),
          allow_negative: false,
          internal_reference: null,
        });
      } else if (dialog === "import") {
        await post(`/admin/partners/${selectedPartner.id}/referral-import`, {
          confirm_without_retroactive_commission: true,
        });
      } else if (dialog === "status") {
        const transition = selectedPartner.status === "paused" ? "resume" : "pause";
        await post(`/admin/partners/${selectedPartner.id}/${transition}`, {
          reason: dialogReason.trim() || null,
        });
      } else if (dialog === "link") {
        await post(`/admin/partners/${selectedPartner.id}/link/rotate`);
      }
      dialog = "";
      dialogReason = "";
      dialogAmount = "";
      await refreshAll();
      actionStatus = at("partners_action_saved", {}, "Changes saved");
    } catch (error) {
      actionStatus =
        error instanceof Error ? error.message : at("partners_action_failed", {}, "Action failed");
    } finally {
      actionBusy = false;
    }
  }

  async function decideApplication(status: "approved" | "rejected"): Promise<void> {
    if (previewMode) {
      selectedApplication.status = status;
    } else {
      actionBusy = true;
      try {
        await post(
          `/admin/partner-applications/${selectedApplication.id}/${status === "approved" ? "approve" : "reject"}`,
          {
            decision_message: status === "rejected" ? rejectMessage.trim() || null : null,
            commission_bps: status === "approved" ? Math.round(Number(approvalRate) * 100) : null,
            welcome_message: status === "approved" ? approvalWelcome.trim() || null : null,
          }
        );
        await refreshAll();
      } catch (error) {
        actionStatus =
          error instanceof Error
            ? error.message
            : at("partners_action_failed", {}, "Action failed");
        return;
      } finally {
        actionBusy = false;
      }
    }
    actionStatus =
      status === "approved"
        ? at("partners_application_approved", {}, "Application approved")
        : at("partners_application_rejected", {}, "Application rejected");
  }

  async function transitionWithdrawal(
    status: "processing" | "paid" | "reject" | "fail"
  ): Promise<void> {
    if (previewMode) {
      selectedWithdrawal.status =
        status === "reject" ? "rejected" : status === "fail" ? "failed" : status;
      return;
    }
    actionBusy = true;
    try {
      await post(`/admin/partner-withdrawals/${selectedWithdrawal.id}/${status}`, {
        status_version: selectedWithdrawal.statusVersion || 1,
        message: dialogReason.trim() || null,
        external_reference: selectedWithdrawal.externalReference || null,
        settlement_amount: selectedWithdrawal.settlementAmount || null,
      });
      await refreshAll();
      selectedWithdrawal =
        withdrawals.find((item) => item.id === selectedWithdrawal.id) || selectedWithdrawal;
      actionStatus = at("partners_action_saved", {}, "Changes saved");
    } catch (error) {
      actionStatus =
        error instanceof Error ? error.message : at("partners_action_failed", {}, "Action failed");
    } finally {
      actionBusy = false;
    }
  }

  async function revealWithdrawalRequisites(): Promise<void> {
    if (previewMode) {
      revealedRequisites = selectedWithdrawal.masked;
      return;
    }
    try {
      const response = await post(`/admin/partner-withdrawals/${selectedWithdrawal.id}/reveal`);
      revealedRequisites = JSON.stringify(response.requisites || {}, null, 2);
    } catch (error) {
      actionStatus =
        error instanceof Error ? error.message : at("partners_action_failed", {}, "Action failed");
    }
  }

  async function loadImportPreview(): Promise<void> {
    if (previewMode || dialog !== "import" || !selectedPartner.id) return;
    try {
      const response = await request(`/admin/partners/${selectedPartner.id}/referral-import`);
      const value = (response.preview || {}) as Record<string, unknown>;
      importPreview = {
        found: Number(value.found || 0),
        new_clients: Number(value.importable || 0),
        existing: Number(value.already_this_partner || 0),
        conflicts: Number(value.other_partner || 0) + Number(value.self_conflict || 0),
      };
    } catch {
      importPreview = { found: 0, new_clients: 0, existing: 0, conflicts: 0 };
    }
  }

  $effect(() => {
    dialog;
    selectedPartner.id;
    if (dialog === "import") void loadImportPreview();
    if (dialog === "rate") dialogRate = String(selectedPartner.rate);
  });

  onMount(() => {
    if (!previewMode) void refreshAll();
  });

  async function copyLink(url: string): Promise<void> {
    try {
      await navigator.clipboard?.writeText(url);
      actionStatus = at("partners_link_copied", {}, "Partner link copied");
    } catch {
      actionStatus = at("partners_link_copy_failed", {}, "Could not copy the partner link");
    }
  }
</script>

<svelte:window onpopstate={syncRouteView} />

<div class="partners-admin-page">
  <header class="partners-toolbar">
    <Tabs.Root
      class="admin-tabs-root partners-toolbar-tabs"
      value={view}
      onValueChange={(value) => navigate(value as View)}
    >
      <Tabs.List
        class="admin-tabs-list"
        aria-label={at("partners_navigation", {}, "Partner program navigation")}
      >
        {#each ["dashboard", "partners", "applications", "withdrawals"] as item}
          <Tabs.Trigger class="admin-tabs-trigger" value={item}>
            {at(`partners_tab_${item}`, {}, item)}
            {#if item === "applications" && pendingApplicationCount}
              <AdminBadge variant="danger">{pendingApplicationCount}</AdminBadge>
            {/if}
            {#if item === "withdrawals" && openWithdrawalCount}
              <AdminBadge variant="warning">{openWithdrawalCount}</AdminBadge>
            {/if}
          </Tabs.Trigger>
        {/each}
      </Tabs.List>
    </Tabs.Root>
    <div class="partners-toolbar-actions">
      <AdminSelect
        value={currency}
        items={["RUB", "USD", "EUR"].map((value) => ({ value, label: value }))}
        ariaLabel={at("partners_currency", {}, "Currency")}
        onValueChange={(value) => void changeCurrency(value)}
      />
      <AdminButton onclick={() => onOpenSettingsPath(["partner"])}>
        <Settings size={15} />{at("partners_configure", {}, "Configure program")}
      </AdminButton>
    </div>
  </header>

  {#if actionStatus}
    <div class="partners-success-banner" role="status">
      <CheckCircle2 size={16} />{actionStatus}
    </div>
  {/if}

  {#if adminScenario === "loading" || loading}
    <PartnerProgramSkeleton {at} {view} />
  {:else if adminScenario === "error" || loadError}
    <AdminEmptyState class="partners-dashboard-state partners-dashboard-error">
      <TriangleAlert size={26} />
      <h2>{at("partners_load_error_title", {}, "Could not load partner dashboard")}</h2>
      <p>{at("partners_load_error_hint", {}, "Check the connection and try again.")}</p>
      <AdminButton onclick={() => void refreshAll()}>
        <RefreshCw size={15} />{at("partners_retry", {}, "Try again")}
      </AdminButton>
    </AdminEmptyState>
  {:else if view === "dashboard"}
    {#if !referralProgramEnabled}
      <PartnerReferralImportBanner {at} {api} {previewMode} onImported={refreshAll} />
    {/if}

    <section
      class="partners-kpi-grid"
      aria-label={at("partners_kpi_title", {}, "Partner program summary")}
    >
      {#each dashboardMetrics as metric}
        {@const MetricIcon = metric.icon}
        <article class="partners-kpi-card partners-tone-{metric.tone}">
          <span><MetricIcon size={18} /></span>
          <div>
            <small>{at(`partners_kpi_${metric.key}`, {}, metric.key)}</small><strong
              >{metric.value}</strong
            >
          </div>
        </article>
      {/each}
    </section>

    {#if adminScenario === "empty" || (!previewMode && !partners.length && !applications.length)}
      <AdminEmptyState class="partners-dashboard-state">
        <UsersRound size={27} />
        <h2>{at("partners_empty_title", {}, "No partner activity yet")}</h2>
        <p>
          {at(
            "partners_empty_hint",
            {},
            "Approve an application or add a partner to start collecting statistics."
          )}
        </p>
        <AdminButton variant="primary" onclick={() => (dialog = "create")}>
          <Plus size={15} />{at("partners_add", {}, "Add partner")}
        </AdminButton>
      </AdminEmptyState>
    {:else}
      <PartnerProgramCharts {at} {currency} {money} {partnerRevenueDaily} {partnerPayoutsDaily} />

      <section class="partners-preview-grid">
        <article class="admin-card partners-preview-card">
          <header>
            <div>
              <WalletCards size={17} /><strong
                >{at("partners_withdrawal_queue", {}, "Withdrawal queue")}</strong
              >
            </div>
            <button type="button" onclick={() => navigate("withdrawals")}
              >{at("partners_view_all", {}, "View all")}<ArrowRight size={14} /></button
            >
          </header>
          {#each withdrawals.slice(0, 3) as withdrawal (withdrawal.id)}<button
              type="button"
              class="partners-preview-row"
              onclick={() => openWithdrawal(withdrawal)}
              ><span
                ><strong>{withdrawal.partner}</strong><small
                  >{at(`partners_method_${withdrawal.method}`, {}, withdrawal.method)} · {withdrawal.masked}</small
                ></span
              ><span
                ><strong>{money(withdrawal.amount)}</strong><AdminBadge
                  variant={partnerStatusVariant(withdrawal.status)}
                  >{statusLabel(withdrawal.status)}</AdminBadge
                ></span
              ></button
            >{/each}
        </article>
        <article class="admin-card partners-preview-card">
          <header>
            <div>
              <TrendingUp size={17} /><strong
                >{at("partners_top_partners", {}, "Top partners")}</strong
              >
            </div>
            <button type="button" onclick={() => navigate("partners")}
              >{at("partners_view_all", {}, "View all")}<ArrowRight size={14} /></button
            >
          </header>
          {#each partners.slice(0, 3) as partner (partner.id)}<button
              type="button"
              class="partners-preview-row"
              onclick={() => openPartner(partner)}
              ><span
                ><strong>{partner.handle}</strong><small
                  >{partner.clients}
                  {at("partners_clients_short", {}, "clients")} · {partner.rate}%</small
                ></span
              ><strong>{money(partner.gross)}</strong></button
            >{/each}
        </article>
        <article class="admin-card partners-preview-card partners-preview-wide">
          <header>
            <div>
              <FileText size={17} /><strong
                >{at("partners_pending_applications", {}, "Pending applications")}</strong
              >{#if pendingApplicationCount}<AdminBadge variant="danger"
                  >{pendingApplicationCount}</AdminBadge
                >{/if}
            </div>
            <button type="button" onclick={() => navigate("applications")}
              >{at("partners_view_all", {}, "View all")}<ArrowRight size={14} /></button
            >
          </header>
          {#each pendingApplications.slice(0, 3) as application (application.id)}<button
              type="button"
              class="partners-preview-row"
              onclick={() => openApplication(application)}
              ><span
                ><strong>{application.handle} — {application.user}</strong><small
                  >{application.submitted}</small
                ></span
              ><AdminBadge variant={partnerStatusVariant(application.status)}
                >{statusLabel(application.status)}</AdminBadge
              ></button
            >{/each}
        </article>
      </section>
    {/if}
  {:else if view === "partners" || view === "applications" || view === "withdrawals"}
    <PartnerProgramTables
      {at}
      {partners}
      {applications}
      {withdrawals}
      {view}
      {money}
      onAddPartner={() => (dialog = "create")}
      onOpenPartner={openPartner}
      onOpenApplication={openApplication}
      onOpenWithdrawal={openWithdrawal}
      {onOpenUserCard}
    />
  {:else}
    <PartnerOperationDetails
      {at}
      {view}
      {selectedPartner}
      {selectedApplication}
      {selectedWithdrawal}
      {partnerLinks}
      {partnerClients}
      {partnerCommissions}
      {partnerLedger}
      {partnerAudit}
      {withdrawals}
      {money}
      {statusLabel}
      {partnerStatusActionLabel}
      {onOpenUserCard}
      {onOpenPaymentCard}
      onNavigate={navigate}
      onOpenWithdrawal={openWithdrawal}
      {openPartnerById}
      onCopyLink={copyLink}
      {decideApplication}
      {transitionWithdrawal}
      {revealWithdrawalRequisites}
      {revealedRequisites}
      bind:dialog
      bind:decisionOutcome
      bind:approvalRate
      bind:approvalWelcome
      bind:rejectMessage
    />
  {/if}
</div>

<PartnerActionDialog
  {at}
  {selectedPartner}
  {previewMode}
  {importPreview}
  {balanceModes}
  {balancePreviewValue}
  {money}
  {completeDialog}
  {actionBusy}
  bind:dialog
  bind:createUserId
  bind:createRate
  bind:importOnCreate
  bind:dialogRate
  bind:dialogReason
  bind:dialogAmount
  bind:balanceMode
/>
