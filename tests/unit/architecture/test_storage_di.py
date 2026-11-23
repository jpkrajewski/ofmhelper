import ast

from test_architecture import check, trees

STORAGE = "ofmhelpers.features.storage"
PROVIDERS = (
    "ofmhelpers.integrations.storage.gdrive",
    "ofmhelpers.integrations.storage.dropbox",
)
CONNECT_FLOWS = frozenset({"features/storage/dropbox_connect.py"})
FACTORY = f"{STORAGE}.client"
COMPOSER = "features/storage/service.py"
COMPOSITION_ROOTS = frozenset(
    {
        "web/dependencies.py",
        "features/storage/jobs.py",
        "features/trials/tasks.py",
        "features/trials/posted.py",
        "features/threads/tasks.py",
        "features/distribution/publish.py",
        "features/distribution/sweep.py",
        "features/clone_links/review.py",
    }
)
PUBLIC = frozenset(
    {
        "service",
        "jobs",
        "client",
        "constants",
        "schema",
        "models",
        "posting",
        "storage_choice",
        "dropbox_connect",
    }
)
SECRETLESS_ROOTS = frozenset({"features/trials/cleanup.py"})
SECRETLESS_FACTORY = frozenset({f"{FACTORY}.saved_storage"})
RAW_CLIENT = frozenset(
    {
        "StorageClient",
        "ReviewPlace",
        "ScopedDrive",
        "ReadOnlyDrive",
        "PerThreadDrive",
        "DriveSheet",
    }
)
RAW_CLIENT_OWNERS = ("features/storage/", "integrations/", "features/fakes.py")
COMPOSE = "compose"
NOT_MOVED_YET = frozenset(
    {
        "features/trials/folder.py",
        "features/trials/tasks.py",
        "features/threads/service.py",
        "features/threads/tasks.py",
        "features/distribution/sources.py",
        "features/distribution/sweep.py",
        "features/distribution/publish.py",
        "features/clone/service.py",
        "features/repurpose/service.py",
        "web/routers/generation/clone.py",
        "features/clone_links/page.py",
        "features/clone_links/review.py",
        "features/accounts/removal.py",
    }
)
CONSTANTS_CLASS = "StorageConstants"
CONSTANTS_BUILDER = "features/storage/client.py"


def imported(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def storage_modules(tree: ast.Module) -> set[str]:
    prefix = f"{STORAGE}."
    return {
        name.removeprefix(prefix).split(".")[0]
        for name in imported(tree)
        if name.startswith(prefix)
    }


def builds_a_service(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else ""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            name = func.attr
        if name.startswith("StorageService"):
            return True
    return False


def names_in(node: ast.AST) -> set[str]:
    return {
        part.id if isinstance(part, ast.Name) else part.attr
        for part in ast.walk(node)
        if isinstance(part, ast.Name | ast.Attribute)
    }


def composers(tree: ast.Module) -> set[ast.AST]:
    return {
        method
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for method in node.body
        if isinstance(method, ast.FunctionDef)
        and method.name == COMPOSE
        and any(
            isinstance(d, ast.Name) and d.id == "classmethod"
            for d in method.decorator_list
        )
    }


def functions_taking_storage(tree: ast.Module) -> set[str]:
    exempt = composers(tree)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node not in exempt
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if arg.annotation is not None
        and any(n.startswith("StorageService") for n in names_in(arg.annotation))
    }


def builds_storage_constants(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call) and CONSTANTS_CLASS in names_in(node.func)
        for node in ast.walk(tree)
    )


def raw_client_imports(tree: ast.Module) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in RAW_CLIENT
    }


def test_no_function_outside_storage_takes_storage():
    found = {
        f"{name}::{function}"
        for name, tree in trees().items()
        if not name.startswith("features/storage/") and name not in NOT_MOVED_YET
        for function in functions_taking_storage(tree)
    }
    check(found, set())


def test_nothing_outside_storage_holds_the_raw_client():
    found = {
        f"{name}::{imported_name}"
        for name, tree in trees().items()
        if not name.startswith(RAW_CLIENT_OWNERS) and name not in NOT_MOVED_YET
        for imported_name in raw_client_imports(tree)
    }
    check(found, set())


def test_files_not_moved_yet_still_need_the_move():
    trees_now = trees()
    done = {
        name
        for name in NOT_MOVED_YET
        if not functions_taking_storage(trees_now[name])
        and not raw_client_imports(trees_now[name])
    }
    check(done, set())


def test_only_the_factory_builds_storage_constants():
    found = {
        name
        for name, tree in trees().items()
        if name != CONSTANTS_BUILDER and builds_storage_constants(tree)
    }
    check(found, set())


def test_only_the_factory_imports_a_storage_provider():
    found = {
        name
        for name, tree in trees().items()
        if not name.startswith("integrations/")
        and name != "features/storage/client.py"
        and name not in CONNECT_FLOWS
        and any(m.startswith(PROVIDERS) for m in imported(tree))
    }
    check(found, set())


def test_only_composition_roots_import_the_storage_factory():
    found = {
        name
        for name, tree in trees().items()
        if name not in COMPOSITION_ROOTS
        and any(m == FACTORY or m.startswith(f"{FACTORY}.") for m in imported(tree))
    }
    check(found, set())


def test_preview_worker_jobs_take_only_the_storage_that_reads_no_secret():
    found = {
        f"{name}::{module}"
        for name, tree in trees().items()
        if name in SECRETLESS_ROOTS
        for module in imported(tree)
        if module.startswith(f"{FACTORY}.") and module not in SECRETLESS_FACTORY
    }
    check(found, set())


def test_only_compose_builds_a_storage_service():
    found = {
        name
        for name, tree in trees().items()
        if name != COMPOSER and builds_a_service(tree)
    }
    check(found, set())


def test_other_code_reaches_storage_only_through_its_public_modules():
    found = {
        f"{name}::{module}"
        for name, tree in trees().items()
        if not name.startswith("features/storage/")
        for module in storage_modules(tree) - PUBLIC
    }
    check(found, set())


def test_the_rules_see_what_they_look_for():
    tree = ast.parse(
        "from ofmhelpers.features.storage import folders\n"
        "from ofmhelpers.features.storage.review import StorageServiceReview\n"
        "from ofmhelpers.integrations.storage.gdrive import connect\n"
        "StorageServiceReview(client=None)\n"
        "StorageService.compose(access)\n"
    )

    assert storage_modules(tree) == {"folders", "review"}
    assert builds_a_service(tree)
    assert any(m.startswith(PROVIDERS) for m in imported(tree))
    assert not builds_a_service(ast.parse("StorageService.compose(access)\n"))
    taking = ast.parse(
        "from ofmhelpers.integrations.storage import DriveSheet\n"
        "def read(folders: StorageServiceFolders | None, day: date) -> None: ...\n"
        "def page(*, uploads: service.StorageServiceUploads) -> None: ...\n"
        "def route(storage: Storage) -> None: ...\n"
        "def compose(storage: StorageService) -> None: ...\n"
        "class Part(BaseModel):\n"
        "    storage: StorageService\n"
        "    @classmethod\n"
        "    def compose(cls, storage: StorageService) -> Part: ...\n"
    )
    assert functions_taking_storage(taking) == {"read", "page", "compose"}
    assert raw_client_imports(taking) == {"DriveSheet"}
    assert builds_storage_constants(ast.parse("c = StorageConstants()\n"))
    assert builds_storage_constants(ast.parse("c = constants.StorageConstants()\n"))
    assert builds_storage_constants(
        ast.parse("c = StorageConstants.model_validate({})\n")
    )
    assert not builds_storage_constants(ast.parse("c = self.constants\n"))
    assert not builds_storage_constants(
        ast.parse("from m import StorageConstants\nx: StorageConstants\n")
    )
