"""A user's saved provider keys (`schemas.users.SavedKeys`)."""

from ofmhelpers.schemas.users import SavedKey, SavedKeys


def test_a_saved_key_is_found_by_its_provider():
    saved = SavedKeys(
        keys=[
            SavedKey(provider="kie", api_key="kie-key-1111"),
            SavedKey(provider="oopsie", api_key="oopsie-key-2222"),
        ]
    )

    assert saved.get("oopsie") == "oopsie-key-2222"


def test_a_provider_without_a_saved_key_gives_an_empty_string():
    assert SavedKeys().get("kie") == ""
