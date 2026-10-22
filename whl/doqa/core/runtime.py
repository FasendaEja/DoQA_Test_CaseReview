"""Framework identity of the running adapter."""


class AdapterRuntime:
    """Configured once by the framework bridge before any attribution or reporting."""

    _framework = "python"
    _framework_label = "python"

    @classmethod
    def configure(cls, framework_id: str, framework_label: str) -> None:
        """Each argument is applied only when non-blank."""
        if framework_id and framework_id.strip():
            cls._framework = framework_id.strip()
        if framework_label and framework_label.strip():
            cls._framework_label = framework_label.strip()

    @classmethod
    def framework(cls) -> str:
        """Prefix of fallback external ids; changing it changes every fallback id."""
        return cls._framework

    @classmethod
    def framework_label(cls) -> str:
        """Value of the ``framework`` label in the file sink."""
        return cls._framework_label
