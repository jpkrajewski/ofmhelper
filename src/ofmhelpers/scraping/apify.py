from apify_client import ApifyClient

from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def get_client_with_most_credits(api_keys: list[str]) -> ApifyClient:
    best_client = None
    best_remaining = float("-inf")

    for key in api_keys:
        try:
            client = ApifyClient(token=key)
            user = client.user().get()
            limits = client.user().limits()
        except Exception:
            logger.warning("apify key ...%s unusable", key[-8:], exc_info=True)
            continue

        limit = limits.limits.max_monthly_usage_usd
        used = limits.current.monthly_usage_usd
        remaining = limit - used

        logger.info(
            "apify key ...%s (%s): $%.2f / $%.2f ($%.2f left)",
            key[-8:],
            # Only the private-info shape carries an email, and the SDK
            # types this as either -- it is a log line, not a lookup.
            getattr(user, "email", "?"),
            used,
            limit,
            remaining,
        )

        if remaining > best_remaining:
            best_remaining = remaining
            best_client = client

    if best_client is None:
        msg = "No usable Apify key found."
        raise RuntimeError(msg)

    return best_client


def run_actor(client: ApifyClient, actor_id: str, raw_input: dict) -> list:
    run = client.actor(actor_id).call(run_input=raw_input)
    if run is None:
        msg = f"Apify actor {actor_id} returned no run"
        raise RuntimeError(msg)
    return list(client.dataset(run.default_dataset_id).iterate_items())
