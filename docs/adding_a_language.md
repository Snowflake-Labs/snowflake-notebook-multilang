# Adding a New Language

This guide walks through adding support for a new language (using Kotlin
as an example).

## 1. Create the Plugin Module

Create `src/sfnb_multilang/languages/kotlin.py`:

```python
from ..config import ToolkitConfig
from .base import LanguagePlugin, PluginResult


class KotlinPlugin(LanguagePlugin):

    @property
    def name(self) -> str:
        return "kotlin"

    @property
    def display_name(self) -> str:
        return "Kotlin"

    def get_conda_packages(self, config: ToolkitConfig) -> list[str]:
        # Kotlin runs on the JVM, so we need OpenJDK
        # This will be deduplicated if Scala is also enabled
        return ["openjdk=17"]

    def get_pip_packages(self, config: ToolkitConfig) -> list[str]:
        # JPype is shared with Scala
        return ["JPype1"]

    def get_network_hosts(self, config: ToolkitConfig) -> list[dict]:
        return [
            {"host": "repo1.maven.org", "port": 443,
             "purpose": "Maven Central (Kotlin compiler)", "required": True},
            {"host": "github.com", "port": 443,
             "purpose": "Kotlin releases", "required": True},
        ]

    def post_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        # Download Kotlin compiler, set up REPL, etc.
        ...
        return PluginResult(success=True, language="kotlin", env_prefix=env_prefix)

    def validate_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        # Check that kotlinc exists and works
        ...
        return PluginResult(success=True, language="kotlin", env_prefix=env_prefix)
```

## 2. Register the Plugin

In `src/sfnb_multilang/languages/__init__.py`, add Kotlin to the registry:

```python
from .kotlin import KotlinPlugin

def _build_registry() -> dict[str, type[LanguagePlugin]]:
    from .r import RPlugin
    from .scala import ScalaPlugin
    from .julia import JuliaPlugin

    return {
        "r": RPlugin,
        "scala": ScalaPlugin,
        "julia": JuliaPlugin,
        "kotlin": KotlinPlugin,  # <-- add this
    }
```

## 3. Add Config Support

Add a `KotlinConfig` dataclass in `config.py` and add parsing in
`_build_config()`. Users can then enable Kotlin in YAML:

```yaml
languages:
  kotlin:
    enabled: true
    kotlin_version: "2.0"
```

## 4. Create the Helper Module

Create `src/sfnb_multilang/helpers/kotlin_helpers.py` with the notebook
magic (`%%kotlin`) and setup function.

## 5. Write Tests

Create `tests/unit/test_plugins/test_kotlin.py`:

- Test `get_conda_packages()` returns expected packages
- Test `get_network_hosts()` returns required hosts
- Test version conflict resolution when Scala + Kotlin both request
  `openjdk` (should deduplicate silently)

## 6. Update Documentation

- Add Kotlin to `docs/configuration.md`
- Add a Kotlin section to `docs/quickstart.md`
- Create a `configs/kotlin_only.yaml` preset

## 7. Signal Handler Conflicts (Native Runtimes)

If your language runs as an in-process native runtime (like Julia via
JuliaCall, or the JVM via JPype), be aware that native signal handlers
can conflict.

**Known conflict: JVM + Julia.** Both install SIGSEGV handlers. If
Julia is initialised after the JVM and a Snowpark Scala session is
then created (SSL handshake), the conflicting handlers cause a kernel
crash (SIGSEGV). The mitigation is to enforce cell ordering: Snowpark
Scala bootstrap must complete before Julia is initialised.

When adding a new native runtime:

- Test it alongside every existing native runtime (JVM, Julia, R/C)
- Watch for SIGSEGV, SIGFPE, or SIGBUS crashes during network I/O,
  GC, or JIT compilation
- Add guards in your `setup_*_environment()` helper that detect
  conflicting runtimes and warn the user about required ordering
- Document any ordering constraints in `docs/quickstart.md`

## 8. Submit a Pull Request

Checklist:

- [ ] Plugin implements all abstract methods from `LanguagePlugin`
- [ ] Plugin registered in `languages/__init__.py`
- [ ] Config section added with sensible defaults
- [ ] Helper module provides `setup_kotlin_environment()`
- [ ] Unit tests pass
- [ ] Documentation updated
- [ ] No hardcoded personal identifiers
- [ ] Tested alongside all other enabled runtimes for signal conflicts
