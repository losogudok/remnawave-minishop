<script lang="ts">
  import { FileText, Plus, Search, UsersRound, WalletCards } from "$components/ui/icons.js";
  import { Input } from "$components/ui/index.js";
  import {
    AdminBadge,
    AdminButton,
    AdminEmptyState,
    AdminPagination,
    AdminEntityLink,
    AdminSelect,
    AdminSortableHeader,
    AdminTable,
  } from "$components/patterns/admin/index.js";
  import { sortAdminRows, type AdminSortColumn } from "$lib/admin/tableSort.js";
  import { USERS_PAGE_SIZE } from "$lib/admin/stores/usersStoreState.js";
  import type {
    ApplicationRow,
    PartnerRow,
    WithdrawalRow,
  } from "$lib/admin/previewMock/partnerProgram.js";
  import { partnerStatusVariant } from "$lib/admin/partnerProgramUi.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type View = "partners" | "applications" | "withdrawals";
  type Partner = PartnerRow;
  type Application = ApplicationRow;
  type Withdrawal = WithdrawalRow;

  let {
    at,
    partners,
    applications,
    withdrawals,
    view,
    money,
    onAddPartner,
    onOpenPartner,
    onOpenApplication,
    onOpenWithdrawal,
    onOpenUserCard,
  }: {
    at: TranslateFn;
    partners: PartnerRow[];
    applications: ApplicationRow[];
    withdrawals: WithdrawalRow[];
    view: View;
    money: (value: number) => string;
    onAddPartner: () => void;
    onOpenPartner: (partner: Partner) => void;
    onOpenApplication: (application: Application) => void;
    onOpenWithdrawal: (withdrawal: Withdrawal) => void;
    onOpenUserCard: (userId: number) => void;
  } = $props();

  function openPartnerById(partnerId: string): void {
    const target = partners.find((partner) => partner.id === partnerId);
    if (target) onOpenPartner(target);
  }

  const openPartnerLabel = $derived(at("partners_open_partner_card", {}, "Open partner card"));
  const openUserLabel = $derived(at("partners_open_user_card", {}, "Open user card"));
  const openApplicationLabel = $derived(at("partners_open_application", {}, "Open application"));
  const openWithdrawalLabel = $derived(at("partners_open_withdrawal", {}, "Open withdrawal"));

  const pageSize = USERS_PAGE_SIZE;
  let partnerSearch = $state("");
  let partnerStatus = $state("all");
  let partnerSort = $state("gross_desc");
  let partnerPage = $state(0);
  let applicationSearch = $state("");
  let applicationStatus = $state("all");
  let applicationSort = $state("submitted_desc");
  let applicationPage = $state(0);
  let withdrawalSearch = $state("");
  let withdrawalStatus = $state("all");
  let withdrawalSort = $state("requested_desc");
  let withdrawalPage = $state(0);

  const partnerSortColumns: AdminSortColumn<Partner>[] = [
    {
      asc: "user_asc",
      desc: "user_desc",
      defaultDirection: "asc",
      value: (row) => [row.name, row.handle],
    },
    { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
    { asc: "rate_asc", desc: "rate_desc", defaultDirection: "desc", value: (row) => row.rate },
    {
      asc: "clients_asc",
      desc: "clients_desc",
      defaultDirection: "desc",
      value: (row) => row.clients,
    },
    { asc: "gross_asc", desc: "gross_desc", defaultDirection: "desc", value: (row) => row.gross },
    {
      asc: "earned_asc",
      desc: "earned_desc",
      defaultDirection: "desc",
      value: (row) => row.earned,
    },
    {
      asc: "available_asc",
      desc: "available_desc",
      defaultDirection: "desc",
      value: (row) => row.available,
    },
  ];
  const applicationSortColumns: AdminSortColumn<Application>[] = [
    {
      asc: "application_asc",
      desc: "application_desc",
      defaultDirection: "desc",
      value: (row) => row.id,
    },
    {
      asc: "applicant_asc",
      desc: "applicant_desc",
      defaultDirection: "asc",
      value: (row) => [row.user, row.handle],
    },
    {
      asc: "message_asc",
      desc: "message_desc",
      defaultDirection: "asc",
      value: (row) => row.messageKey,
    },
    {
      asc: "submitted_asc",
      desc: "submitted_desc",
      defaultDirection: "desc",
      value: (row) => row.submitted,
    },
    {
      asc: "application_status_asc",
      desc: "application_status_desc",
      defaultDirection: "asc",
      value: (row) => row.status,
    },
  ];
  const withdrawalSortColumns: AdminSortColumn<Withdrawal>[] = [
    {
      asc: "withdrawal_asc",
      desc: "withdrawal_desc",
      defaultDirection: "desc",
      value: (row) => row.id,
    },
    {
      asc: "withdrawal_partner_asc",
      desc: "withdrawal_partner_desc",
      defaultDirection: "asc",
      value: (row) => [row.partner, row.handle],
    },
    { asc: "method_asc", desc: "method_desc", defaultDirection: "asc", value: (row) => row.method },
    {
      asc: "amount_asc",
      desc: "amount_desc",
      defaultDirection: "desc",
      value: (row) => row.amount,
    },
    {
      asc: "withdrawal_status_asc",
      desc: "withdrawal_status_desc",
      defaultDirection: "asc",
      value: (row) => row.status,
    },
    {
      asc: "requested_asc",
      desc: "requested_desc",
      defaultDirection: "desc",
      value: (row) => row.requested,
    },
  ];

  const filteredPartners = $derived(
    partners.filter((partner) => {
      const query = partnerSearch.trim().toLocaleLowerCase();
      const matchesQuery =
        !query ||
        `${partner.name} ${partner.handle} ${partner.id}`.toLocaleLowerCase().includes(query);
      return matchesQuery && (partnerStatus === "all" || partner.status === partnerStatus);
    })
  );
  const sortedPartners = $derived(sortAdminRows(filteredPartners, partnerSort, partnerSortColumns));
  const partnerPageCount = $derived(Math.max(1, Math.ceil(sortedPartners.length / pageSize)));
  const pagedPartners = $derived(
    sortedPartners.slice(partnerPage * pageSize, (partnerPage + 1) * pageSize)
  );

  const filteredApplications = $derived(
    applications.filter((application) => {
      const query = applicationSearch.trim().toLocaleLowerCase();
      const matchesQuery =
        !query ||
        `${application.user} ${application.handle} ${application.id}`
          .toLocaleLowerCase()
          .includes(query);
      return (
        matchesQuery && (applicationStatus === "all" || application.status === applicationStatus)
      );
    })
  );
  const sortedApplications = $derived(
    sortAdminRows(filteredApplications, applicationSort, applicationSortColumns)
  );
  const applicationPageCount = $derived(
    Math.max(1, Math.ceil(sortedApplications.length / pageSize))
  );
  const pagedApplications = $derived(
    sortedApplications.slice(applicationPage * pageSize, (applicationPage + 1) * pageSize)
  );

  const filteredWithdrawals = $derived(
    withdrawals.filter((withdrawal) => {
      const query = withdrawalSearch.trim().toLocaleLowerCase();
      const matchesQuery =
        !query ||
        `${withdrawal.partner} ${withdrawal.handle} ${withdrawal.id} ${withdrawal.masked}`
          .toLocaleLowerCase()
          .includes(query);
      return matchesQuery && (withdrawalStatus === "all" || withdrawal.status === withdrawalStatus);
    })
  );
  const sortedWithdrawals = $derived(
    sortAdminRows(filteredWithdrawals, withdrawalSort, withdrawalSortColumns)
  );
  const withdrawalPageCount = $derived(Math.max(1, Math.ceil(sortedWithdrawals.length / pageSize)));
  const pagedWithdrawals = $derived(
    sortedWithdrawals.slice(withdrawalPage * pageSize, (withdrawalPage + 1) * pageSize)
  );

  const paginationLabels = $derived({
    page: at("page_short", {}, "Page"),
    of: at("pagination_of", {}, "of"),
    total: at("total", {}, "Total"),
    jump: at("pagination_jump_aria", {}, "Go to page"),
    go: at("pagination_go", {}, "Go"),
    back: at("back", {}, "Back"),
    next: at("next", {}, "Next"),
  });

  function statusLabel(status: string): string {
    return at(`partners_status_${status}`, {}, status);
  }

  function resetPartnerFilter(next: string): void {
    partnerStatus = next;
    partnerPage = 0;
  }

  function resetApplicationFilter(next: string): void {
    applicationStatus = next;
    applicationPage = 0;
  }

  function resetWithdrawalFilter(next: string): void {
    withdrawalStatus = next;
    withdrawalPage = 0;
  }

  function resetPartnerFilters(): void {
    partnerSearch = "";
    resetPartnerFilter("all");
  }

  function resetApplicationFilters(): void {
    applicationSearch = "";
    resetApplicationFilter("all");
  }

  function resetWithdrawalFilters(): void {
    withdrawalSearch = "";
    resetWithdrawalFilter("all");
  }
</script>

{#if view === "partners"}
  <section class="partners-list-view">
    <div class="partners-list-head">
      <div>
        <h2>{at("partners_list_title", {}, "Partners")}</h2>
        <p>
          {at(
            "partners_list_hint",
            {},
            "Search, filter, and inspect partner balances by currency."
          )}
        </p>
      </div>
      <AdminButton variant="primary" onclick={onAddPartner}
        ><Plus size={15} />{at("partners_add", {}, "Add partner")}</AdminButton
      >
    </div>
    <div class="partners-filters">
      <label class="partners-search">
        <span class="sr-only">{at("partners_search", {}, "Search by user or partner ID")}</span>
        <Search size={16} />
        <Input
          class="input"
          type="search"
          value={partnerSearch}
          oninput={(event) => {
            partnerSearch = event.currentTarget.value;
            partnerPage = 0;
          }}
          placeholder={at("partners_search", {}, "Search by user or partner ID")}
        />
      </label>
      <AdminSelect
        value={partnerStatus}
        items={[
          { value: "all", label: at("partners_filter_all", {}, "All statuses") },
          { value: "active", label: statusLabel("active") },
          { value: "paused", label: statusLabel("paused") },
        ]}
        ariaLabel={at("partners_col_status", {}, "Status")}
        onValueChange={resetPartnerFilter}
      />
    </div>
    <AdminTable>
      <thead
        ><tr>
          <AdminSortableHeader
            label={at("partners_col_user", {}, "User")}
            column={partnerSortColumns[0]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_status", {}, "Status")}
            column={partnerSortColumns[1]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_rate", {}, "Rate")}
            column={partnerSortColumns[2]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_clients", {}, "Clients")}
            column={partnerSortColumns[3]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_gross", {}, "Gross")}
            column={partnerSortColumns[4]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_earned", {}, "Net commission")}
            column={partnerSortColumns[5]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_available", {}, "Available")}
            column={partnerSortColumns[6]}
            currentSort={partnerSort}
            {at}
            onSort={(sort) => {
              partnerSort = sort;
              partnerPage = 0;
            }}
          />
          <th>{at("actions", {}, "Actions")}</th>
        </tr></thead
      >
      <tbody>
        {#each pagedPartners as partner (partner.id)}
          <tr>
            <td class="admin-cell-primary" data-label={at("partners_col_user", {}, "User")}>
              <AdminEntityLink
                kind="partner"
                label={partner.name}
                secondary={partner.handle}
                idText={partner.id}
                title={openPartnerLabel}
                onclick={() => onOpenPartner(partner)}
              />
            </td>
            <td data-label={at("partners_col_status", {}, "Status")}>
              <AdminBadge variant={partnerStatusVariant(partner.status)}
                >{statusLabel(partner.status)}</AdminBadge
              >
            </td>
            <td data-label={at("partners_col_rate", {}, "Rate")}>{partner.rate}%</td>
            <td data-label={at("partners_col_clients", {}, "Clients")}>{partner.clients}</td>
            <td data-label={at("partners_col_gross", {}, "Gross")}>{money(partner.gross)}</td>
            <td data-label={at("partners_col_earned", {}, "Net commission")}
              >{money(partner.earned)}</td
            >
            <td data-label={at("partners_col_available", {}, "Available")}
              ><span class:partners-negative={partner.available < 0}
                >{money(partner.available)}</span
              ></td
            >
            <td class="admin-cell-actions" data-label={at("actions", {}, "Actions")}>
              <AdminButton size="sm" onclick={() => onOpenPartner(partner)}
                >{at("partners_open", {}, "Open")}</AdminButton
              >
            </td>
          </tr>
        {/each}
      </tbody>
    </AdminTable>
    {#if !sortedPartners.length}
      <AdminEmptyState class="partners-table-empty">
        <UsersRound size={24} />
        <strong>{at("partners_list_empty_title", {}, "No partners match the filters")}</strong>
        <p>{at("partners_list_empty_hint", {}, "Clear the search or pick another status.")}</p>
        <AdminButton onclick={resetPartnerFilters}
          >{at("partners_reset_filters", {}, "Reset filters")}</AdminButton
        >
      </AdminEmptyState>
    {/if}
    {#if sortedPartners.length}
      <AdminPagination
        page={partnerPage}
        pageCount={partnerPageCount}
        total={filteredPartners.length}
        pageLabel={paginationLabels.page}
        ofLabel={paginationLabels.of}
        totalLabel={paginationLabels.total}
        jumpLabel={paginationLabels.page}
        jumpAriaLabel={paginationLabels.jump}
        goLabel={paginationLabels.go}
        prevLabel={paginationLabels.back}
        nextLabel={paginationLabels.next}
        onPageChange={(page) => (partnerPage = page)}
      />
    {/if}
  </section>
{:else if view === "applications"}
  <section class="partners-list-view">
    <div class="partners-list-head">
      <div>
        <h2>{at("partners_applications_title", {}, "Applications")}</h2>
        <p>{at("partners_applications_hint", {}, "Pending requests are shown first.")}</p>
      </div>
    </div>
    <div class="partners-filters">
      <label class="partners-search">
        <span class="sr-only">{at("partners_search_applications", {}, "Search applications")}</span>
        <Search size={16} />
        <Input
          class="input"
          type="search"
          value={applicationSearch}
          oninput={(event) => {
            applicationSearch = event.currentTarget.value;
            applicationPage = 0;
          }}
          placeholder={at("partners_search_applications", {}, "Search applications")}
        />
      </label>
      <AdminSelect
        value={applicationStatus}
        items={[
          { value: "all", label: at("partners_filter_all", {}, "All statuses") },
          {
            value: "pending",
            label: `${statusLabel("pending")} (${applications.filter((item) => item.status === "pending").length})`,
          },
          {
            value: "approved",
            label: `${statusLabel("approved")} (${applications.filter((item) => item.status === "approved").length})`,
          },
          {
            value: "rejected",
            label: `${statusLabel("rejected")} (${applications.filter((item) => item.status === "rejected").length})`,
          },
        ]}
        ariaLabel={at("partners_col_status", {}, "Status")}
        onValueChange={resetApplicationFilter}
      />
    </div>
    <AdminTable>
      <thead
        ><tr>
          <AdminSortableHeader
            label={at("partners_application", {}, "Application")}
            column={applicationSortColumns[0]}
            currentSort={applicationSort}
            {at}
            onSort={(sort) => {
              applicationSort = sort;
              applicationPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_applicant", {}, "Applicant")}
            column={applicationSortColumns[1]}
            currentSort={applicationSort}
            {at}
            onSort={(sort) => {
              applicationSort = sort;
              applicationPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_application_message", {}, "Application message")}
            column={applicationSortColumns[2]}
            currentSort={applicationSort}
            {at}
            onSort={(sort) => {
              applicationSort = sort;
              applicationPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_submitted", {}, "Submitted")}
            column={applicationSortColumns[3]}
            currentSort={applicationSort}
            {at}
            onSort={(sort) => {
              applicationSort = sort;
              applicationPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_status", {}, "Status")}
            column={applicationSortColumns[4]}
            currentSort={applicationSort}
            {at}
            onSort={(sort) => {
              applicationSort = sort;
              applicationPage = 0;
            }}
          />
          <th>{at("actions", {}, "Actions")}</th>
        </tr></thead
      >
      <tbody>
        {#each pagedApplications as application (application.id)}
          <tr>
            <td
              class="partners-ref-cell"
              data-label={at("partners_application", {}, "Application")}
            >
              <AdminEntityLink
                kind="application"
                label={application.id}
                title={openApplicationLabel}
                onclick={() => onOpenApplication(application)}
              />
            </td>
            <td class="admin-cell-primary" data-label={at("partners_applicant", {}, "Applicant")}>
              <AdminEntityLink
                kind="user"
                label={application.user}
                secondary={application.handle}
                title={openUserLabel}
                onclick={() => onOpenUserCard(application.userId)}
              />
            </td>
            <td
              class="admin-cell-wrap"
              data-label={at("partners_application_message", {}, "Application message")}
              >{at(application.messageKey, {}, application.messageKey)}</td
            >
            <td data-label={at("partners_submitted", {}, "Submitted")}>{application.submitted}</td>
            <td data-label={at("partners_col_status", {}, "Status")}>
              <AdminBadge variant={partnerStatusVariant(application.status)}
                >{statusLabel(application.status)}</AdminBadge
              >
            </td>
            <td class="admin-cell-actions" data-label={at("actions", {}, "Actions")}>
              <AdminButton size="sm" onclick={() => onOpenApplication(application)}
                >{at("partners_open", {}, "Open")}</AdminButton
              >
            </td>
          </tr>
        {/each}
      </tbody>
    </AdminTable>
    {#if !sortedApplications.length}
      <AdminEmptyState class="partners-table-empty">
        <FileText size={24} />
        <strong>
          {at("partners_applications_empty_title", {}, "No applications match the filters")}
        </strong>
        <p>
          {at("partners_applications_empty_hint", {}, "Clear the search or pick another status.")}
        </p>
        <AdminButton onclick={resetApplicationFilters}
          >{at("partners_reset_filters", {}, "Reset filters")}</AdminButton
        >
      </AdminEmptyState>
    {/if}
    {#if sortedApplications.length}
      <AdminPagination
        page={applicationPage}
        pageCount={applicationPageCount}
        total={filteredApplications.length}
        pageLabel={paginationLabels.page}
        ofLabel={paginationLabels.of}
        totalLabel={paginationLabels.total}
        jumpLabel={paginationLabels.page}
        jumpAriaLabel={paginationLabels.jump}
        goLabel={paginationLabels.go}
        prevLabel={paginationLabels.back}
        nextLabel={paginationLabels.next}
        onPageChange={(page) => (applicationPage = page)}
      />
    {/if}
  </section>
{:else}
  <section class="partners-list-view">
    <div class="partners-list-head">
      <div>
        <h2>{at("partners_withdrawals_title", {}, "Withdrawals")}</h2>
        <p>
          {at("partners_withdrawals_hint", {}, "Manual payout queue with status preconditions.")}
        </p>
      </div>
    </div>
    <div class="partners-filters">
      <label class="partners-search">
        <span class="sr-only">{at("partners_search_withdrawals", {}, "Search withdrawals")}</span>
        <Search size={16} />
        <Input
          class="input"
          type="search"
          value={withdrawalSearch}
          oninput={(event) => {
            withdrawalSearch = event.currentTarget.value;
            withdrawalPage = 0;
          }}
          placeholder={at("partners_search_withdrawals", {}, "Search withdrawals")}
        />
      </label>
      <AdminSelect
        value={withdrawalStatus}
        items={[
          { value: "all", label: at("partners_filter_all", {}, "All statuses") },
          ...["requested", "processing", "paid", "rejected"].map((status) => ({
            value: status,
            label: `${statusLabel(status)} (${withdrawals.filter((item) => item.status === status).length})`,
          })),
        ]}
        ariaLabel={at("partners_col_status", {}, "Status")}
        onValueChange={resetWithdrawalFilter}
      />
    </div>
    <AdminTable>
      <thead
        ><tr>
          <AdminSortableHeader
            label={at("partners_col_withdrawal", {}, "Withdrawal")}
            column={withdrawalSortColumns[0]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_partner", {}, "Partner")}
            column={withdrawalSortColumns[1]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_method", {}, "Method")}
            column={withdrawalSortColumns[2]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_amount", {}, "Amount")}
            column={withdrawalSortColumns[3]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_col_status", {}, "Status")}
            column={withdrawalSortColumns[4]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <AdminSortableHeader
            label={at("partners_requested", {}, "Requested")}
            column={withdrawalSortColumns[5]}
            currentSort={withdrawalSort}
            {at}
            onSort={(sort) => {
              withdrawalSort = sort;
              withdrawalPage = 0;
            }}
          />
          <th>{at("actions", {}, "Actions")}</th>
        </tr></thead
      >
      <tbody>
        {#each pagedWithdrawals as withdrawal (withdrawal.id)}
          <tr>
            <td
              class="partners-ref-cell"
              data-label={at("partners_col_withdrawal", {}, "Withdrawal")}
            >
              <AdminEntityLink
                kind="withdrawal"
                label={withdrawal.id}
                title={openWithdrawalLabel}
                onclick={() => onOpenWithdrawal(withdrawal)}
              />
            </td>
            <td class="admin-cell-primary" data-label={at("partners_col_partner", {}, "Partner")}>
              <AdminEntityLink
                kind="partner"
                label={withdrawal.partner}
                secondary={withdrawal.handle}
                idText={withdrawal.partnerId}
                title={openPartnerLabel}
                onclick={() => openPartnerById(withdrawal.partnerId)}
              />
            </td>
            <td class="partners-method-cell" data-label={at("partners_col_method", {}, "Method")}>
              <span>
                {at(`partners_method_${withdrawal.method}`, {}, withdrawal.method)}
                <small>{withdrawal.masked}</small>
              </span>
            </td>
            <td data-label={at("partners_col_amount", {}, "Amount")}>{money(withdrawal.amount)}</td>
            <td data-label={at("partners_col_status", {}, "Status")}>
              <AdminBadge variant={partnerStatusVariant(withdrawal.status)}
                >{statusLabel(withdrawal.status)}</AdminBadge
              >
            </td>
            <td data-label={at("partners_requested", {}, "Requested")}>{withdrawal.requested}</td>
            <td class="admin-cell-actions" data-label={at("actions", {}, "Actions")}>
              <AdminButton size="sm" onclick={() => onOpenWithdrawal(withdrawal)}
                >{at("partners_open", {}, "Open")}</AdminButton
              >
            </td>
          </tr>
        {/each}
      </tbody>
    </AdminTable>
    {#if !sortedWithdrawals.length}
      <AdminEmptyState class="partners-table-empty">
        <WalletCards size={24} />
        <strong>
          {at("partners_withdrawals_empty_title", {}, "No withdrawals match the filters")}
        </strong>
        <p>
          {at("partners_withdrawals_empty_hint", {}, "Clear the search or pick another status.")}
        </p>
        <AdminButton onclick={resetWithdrawalFilters}
          >{at("partners_reset_filters", {}, "Reset filters")}</AdminButton
        >
      </AdminEmptyState>
    {/if}
    {#if sortedWithdrawals.length}
      <AdminPagination
        page={withdrawalPage}
        pageCount={withdrawalPageCount}
        total={filteredWithdrawals.length}
        pageLabel={paginationLabels.page}
        ofLabel={paginationLabels.of}
        totalLabel={paginationLabels.total}
        jumpLabel={paginationLabels.page}
        jumpAriaLabel={paginationLabels.jump}
        goLabel={paginationLabels.go}
        prevLabel={paginationLabels.back}
        nextLabel={paginationLabels.next}
        onPageChange={(page) => (withdrawalPage = page)}
      />
    {/if}
  </section>
{/if}
