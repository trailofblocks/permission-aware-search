"""Synthetic workspaces for the revocation scenario."""

from .contracts import TenantSpec
from .store import Store, digest

DEMO_ACTORS = (
    {"actor": "maya", "name": "Maya Chen", "role": "Finance member", "workspace": "Northstar"},
    {"actor": "theo", "name": "Theo Brooks", "role": "Support member", "workspace": "Northstar"},
    {"actor": "elena", "name": "Elena Park", "role": "Workspace admin", "workspace": "Northstar"},
    {"actor": "ari", "name": "Ari Morgan", "role": "Workspace admin", "workspace": "Harbor"},
)


def demo_token(actor: str) -> str:
    return "demo-scope-" + digest("scope-demo-v1/" + actor)


def seed_demo(store: Store) -> None:
    workspaces = [
        {
            "tenant_id": "northstar", "name": "Northstar",
            "groups": [{"slug": "finance", "name": "Finance"}, {"slug": "support", "name": "Support"}],
            "users": [
                {"handle": "maya", "name": "Maya Chen", "groups": ["finance"]},
                {"handle": "theo", "name": "Theo Brooks", "groups": ["support"]},
                {"handle": "elena", "name": "Elena Park", "role": "admin"},
            ],
            "documents": [
                {
                    "slug": "vendor-finance-memo", "title": "Vendor policy and finance exceptions",
                    "collection": "Finance handbook", "group_slug": "finance",
                    "body": "The vendor policy allows Finance to approve a 2.5% early-payment rebate for qualifying "
                            "suppliers paid within ten business days. Finance reviews rebate eligibility before signing "
                            "a supplier agreement. The standard accounting workflow remains unchanged.",
                },
                {
                    "slug": "vendor-policy", "title": "Vendor onboarding policy",
                    "collection": "Operations handbook",
                    "body": "The vendor onboarding policy requires a completed registration form, a tax certificate, "
                            "and a named business owner before Procurement can activate a new vendor. "
                            "Submit the onboarding packet in the Procurement portal for review.",
                },
                {
                    "slug": "vendor-review", "title": "Annual procurement review",
                    "collection": "Operations handbook",
                    "body": "The vendor policy is reviewed every October. Business owners should send proposed "
                            "changes to Procurement before the annual review window closes. The policy change log "
                            "lists changes that apply across all departments.",
                },
                {
                    "slug": "support-handoff", "title": "Vendor support handoff",
                    "collection": "Support playbook", "group_slug": "support",
                    "body": "Escalate a vendor billing ticket to Procurement when a supplier cannot submit an invoice. "
                            "Include the case reference and the date the vendor contacted Support.",
                },
                {
                    "slug": "expense-policy", "title": "Travel expense policy",
                    "collection": "People handbook",
                    "body": "Employees should submit receipts and the purpose of business travel within 30 days. "
                            "Group travel requires advance approval from the department lead.",
                },
            ],
        },
        {
            "tenant_id": "harbor", "name": "Harbor", "groups": [],
            "users": [{"handle": "ari", "name": "Ari Morgan", "role": "admin"}],
            "documents": [{
                "slug": "vendor-policy", "title": "Harbor vendor policy",
                "collection": "Harbor operations",
                "body": "Harbor vendor policy requires two signed reference letters and an onboarding meeting "
                        "before a vendor receives access to the Harbor supplier portal.",
            }],
        },
    ]
    for raw in workspaces:
        workspace = TenantSpec.model_validate(raw)
        store.import_workspace(workspace, {user.handle: demo_token(user.handle) for user in workspace.users},
                               skip_existing=True)
