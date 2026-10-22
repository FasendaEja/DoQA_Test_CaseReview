"""Identity of one test, shared by selection, ordering and reporting.

``fqcn`` is the dotted module path plus, for class-bound tests, ``::``-separated class
segments (``pkg.module::TestOuter::TestInner``) - ``::`` is the nesting marker used
consistently by class-fixture chains and realtime buffering.
"""

from typing import Any, List, Optional


class ExternalMarkup:
    """Markup declared outside the code (``.feature`` tags); code-level markup wins."""

    def __init__(self) -> None:
        self.explicit_id: Optional[str] = None
        self.doqa_id: Optional[str] = None
        self.allure_id: Optional[str] = None
        self.case_ids: List[int] = []
        #: definition_name keeps the Outline ``<param>`` template
        self.name: Optional[str] = None
        self.definition_name: Optional[str] = None


class TestRef:

    def __init__(self, fqcn: Optional[str], method_name: Optional[str],
                 method_param_types: Optional[str], display_name: Optional[str],
                 parameterized: bool, test_class: Any = None, test_method: Any = None):
        self.fqcn = fqcn
        self.method_name = method_name
        self.method_param_types = method_param_types
        self.display_name = display_name
        #: True => invocations of this element collapse into one method-level identity.
        self.parameterized = parameterized
        self.test_class = test_class
        self.test_method = test_method
        self.markup: Optional[ExternalMarkup] = None

    def full_name(self) -> Optional[str]:
        """Allure-style full name: dotted container path, ``#``, function name."""
        if not self.fqcn:
            return self.method_name if self.method_name else self.display_name
        if not self.method_name:
            return self.fqcn
        return self.fqcn.replace("::", ".") + "#" + self.method_name

    def method_key(self) -> Optional[str]:
        return (self.fqcn or "") + "#" + (self.method_name or "")

    def package_name(self) -> Optional[str]:
        """Dotted namespace: the module path for class-bound tests, the parent package for
        module-level functions; None when there is no parent."""
        if not self.fqcn:
            return None
        marker = self.fqcn.find("::")
        if marker >= 0:
            return self.fqcn[:marker]
        dot = self.fqcn.rfind(".")
        return self.fqcn[:dot] if dot > 0 else None

    def simple_class_name(self) -> Optional[str]:
        if not self.fqcn:
            return None
        marker = self.fqcn.rfind("::")
        if marker >= 0:
            return self.fqcn[marker + 2:]
        return self.fqcn[self.fqcn.rfind(".") + 1:]
