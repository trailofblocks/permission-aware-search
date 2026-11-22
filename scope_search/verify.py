"""Exercise revocation and restoration using only the public HTTP interface."""

import httpx

QUERY = "vendor policy"
PRIVATE = "vendor-finance-memo"
SHARED = "vendor-policy"


def verify(base_url: str) -> dict:
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        def credentials(actor):
            response = client.post("/v1/demo/session", json={"actor": actor}).raise_for_status()
            return {"Authorization": "Bearer " + response.json()["token"]}

        finance = credentials("maya")
        support = credentials("theo")
        admin = credentials("elena")
        other = credentials("ari")
        change = "/v1/admin/groups/finance/members/maya"
        state = client.get("/v1/admin/access", headers=admin).raise_for_status().json()
        initial = any(row["handle"] == "maya" and "finance" in row["groups"] for row in state["users"])

        def membership(enabled):
            return client.put(change, headers=admin, json={"enabled": enabled}).raise_for_status().json()

        def search(headers):
            return client.get("/v1/search", headers=headers, params={"q": QUERY}).raise_for_status().json()

        def cited(headers):
            return client.post("/v1/answer", headers=headers, json={"query": QUERY}).raise_for_status().json()

        def slugs(payload, key):
            return {row["slug"] for row in payload[key]}

        try:
            membership(True)
            search(finance)
            warm = search(finance)
            baseline_answer = cited(finance)
            shared_only = search(support)
            other_tenant = search(other)
            membership(False)
            after = search(finance)
            after_answer = cited(finance)
            direct = client.get(f"/v1/documents/{PRIVATE}", headers=finance)
            checks = {
                "source_was_visible_on_warm_cache": warm["cache"] == "hit" and PRIVATE in slugs(warm, "results"),
                "finance_answer_cited_private_source": PRIVATE in slugs(baseline_answer, "citations"),
                "support_sees_shared_policy": SHARED in slugs(shared_only, "results") and PRIVATE not in slugs(shared_only, "results"),
                "other_tenant_stays_separate": slugs(other_tenant, "results") == {SHARED}
                and other_tenant["results"][0]["title"] == "Harbor vendor policy",
                "revoked_source_absent_from_results": after["cache"] == "miss"
                and PRIVATE not in slugs(after, "results") and SHARED in slugs(after, "results"),
                "revoked_source_absent_from_answer": PRIVATE not in slugs(after_answer, "citations"),
                "revoked_direct_document_is_404": direct.status_code == 404,
                "member_cannot_change_access": client.put(change, headers=support, json={"enabled": True}).status_code == 403,
            }
            if not all(checks.values()):
                raise RuntimeError("Revocation verification failed: " + ", ".join(key for key, value in checks.items() if not value))
            return {"checks": checks, "warm_cache": warm["cache"], "after_revoke_cache": after["cache"],
                    "before": [item["title"] for item in warm["results"]],
                    "after": [item["title"] for item in after["results"]]}
        finally:
            membership(initial)
