from bot.app.web.partner_schemas import (
    PartnerApplicationCreateIn,
    PartnerApplicationOut,
    PartnerBalanceRenewIn,
    PartnerClientOut,
    PartnerCommissionOut,
    PartnerOverviewOut,
    PartnerWithdrawalCreateIn,
    PartnerWithdrawalOut,
)
from bot.app.web.route_contracts import (
    INTEGER_SCHEMA,
    STRING_SCHEMA,
    RouteContract,
    ok_envelope_for,
    ok_envelope_with,
    schema_ref,
)

_PAGINATION = {
    "total": INTEGER_SCHEMA,
    "limit": INTEGER_SCHEMA,
    "offset": INTEGER_SCHEMA,
}

PARTNER_ROUTE_CONTRACTS: dict[str, RouteContract] = {
    "partner_overview_route": RouteContract(
        response_schema=ok_envelope_for(PartnerOverviewOut),
        models=(PartnerOverviewOut,),
    ),
    "partner_application_create_route": RouteContract(
        request_model=PartnerApplicationCreateIn,
        response_schema=ok_envelope_for(PartnerApplicationOut, key="application"),
        models=(PartnerApplicationCreateIn, PartnerApplicationOut),
    ),
    "partner_clients_route": RouteContract(
        response_schema=ok_envelope_with(
            {
                "clients": {"type": "array", "items": schema_ref(PartnerClientOut)},
                **_PAGINATION,
            }
        ),
        models=(PartnerClientOut,),
    ),
    "partner_commissions_route": RouteContract(
        response_schema=ok_envelope_with(
            {
                "commissions": {"type": "array", "items": schema_ref(PartnerCommissionOut)},
                **_PAGINATION,
            }
        ),
        models=(PartnerCommissionOut,),
    ),
    "partner_withdrawals_route": RouteContract(
        response_schema=ok_envelope_with(
            {
                "withdrawals": {"type": "array", "items": schema_ref(PartnerWithdrawalOut)},
                **_PAGINATION,
            }
        ),
        models=(PartnerWithdrawalOut,),
    ),
    "partner_withdrawal_create_route": RouteContract(
        request_model=PartnerWithdrawalCreateIn,
        response_schema=ok_envelope_for(PartnerWithdrawalOut, key="withdrawal"),
        models=(PartnerWithdrawalCreateIn, PartnerWithdrawalOut),
    ),
    "partner_withdrawal_cancel_route": RouteContract(
        response_schema=ok_envelope_for(PartnerWithdrawalOut, key="withdrawal"),
        models=(PartnerWithdrawalOut,),
    ),
    "partner_balance_renew_route": RouteContract(
        request_model=PartnerBalanceRenewIn,
        response_schema=ok_envelope_with(
            {
                "payment_id": INTEGER_SCHEMA,
                "status": STRING_SCHEMA,
                "remaining_balance_minor": INTEGER_SCHEMA,
            }
        ),
        models=(PartnerBalanceRenewIn,),
    ),
}
