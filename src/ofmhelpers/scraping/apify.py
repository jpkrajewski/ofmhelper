from apify_client import ApifyClient


def get_client_with_most_credits(api_keys: list[str]) -> ApifyClient:
    best_client = None
    best_remaining = float("-inf")

    for key in api_keys:
        try:
            client = ApifyClient(token=key)
            user = client.user().get()
            limits = client.user().limits()
        except Exception as exc:
            print(f"[{key[-8:]}] error: {exc}")
            continue

        limit = limits.limits.max_monthly_usage_usd
        used = limits.current.monthly_usage_usd
        remaining = limit - used

        print(
            f"[{key[-8:]}] {user.email} — ${used:.2f} / ${limit:.2f} (${remaining:.2f} left)"
        )

        if remaining > best_remaining:
            best_remaining = remaining
            best_client = client

    if best_client is None:
        raise RuntimeError("No usable Apify key found.")

    return best_client


def run_actor(client: ApifyClient, actor_id: str, raw_input: dict) -> list:
    run = client.actor(actor_id).call(run_input=raw_input)
    return list(client.dataset(run.default_dataset_id).iterate_items())
