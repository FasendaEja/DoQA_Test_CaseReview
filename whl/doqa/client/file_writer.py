"""File sink: serializes the def/result model into parser-compatible Allure files.

Active when ``effective_reporting()`` is ``files`` - an explicit ``reporting=files`` or
``auto`` without a complete API config. No network, no credentials. The DoQA parser matches
files by name SUFFIX (``*-result.json`` / ``*-container.json``), so the directory name is
free to configure.
"""

import os
import shutil
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import jsonio, multipart
from .models import AutotestDef, AutotestResult, run_properties
from .properties import escape_value

# Label names are part of the DoQA parser contract (matched case-insensitively there).
LABEL_ID = "doqa_id"
LABEL_CASES = "doqa_cases"
LABEL_WORK_ITEMS = "doqa_work_items"
LABEL_ALLURE_ID = "AS_ID"
LABEL_CREATE_MANUAL_CASE = "doqa_create_manual_case"
# Allure has no native slot for a title - it travels as a label.
LABEL_TITLE = "doqa_title"


# Side file next to the results: which sink the session used (``sink=api|files``), the run it
# reported into and how many results fell back to files. The upload step of a pipeline reads it
# to tell "no result files because everything went over the API" from "no result files because
# the adapter never reported".
REPORTING_INFO_FILE = "doqa-reporting.properties"


class AllureFileWriter:

    def __init__(self, results_dir: "os.PathLike[str]", framework_label: str):
        self._dir = os.fspath(results_dir)
        self._framework_label = framework_label
        # source -> (original name, content type); NOT cleared per result: a class-fixture
        # attachment is referenced by every result of the class.
        self._attachment_meta: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError as e:
            raise OSError("cannot create results dir " + self._dir) from e

    # ------------------------------------------------------------------ attachments
    def store_attachment(self, path: "os.PathLike[str]") -> str:
        """Copy a file into the results dir; the returned source is the file-sink analog of
        ``media_file_id``."""
        path = os.fspath(path)
        name = os.path.basename(path)
        dot = name.rfind(".")
        ext = name[dot:] if dot >= 0 else ".bin"
        source = str(uuid.uuid4()) + "-attachment" + ext
        try:
            shutil.copyfile(path, os.path.join(self._dir, source))
        except OSError as e:
            raise OSError("cannot copy attachment " + path) from e
        self._attachment_meta[source] = (name, multipart.content_type_for(name))
        return source

    def store_attachment_bytes(self, name: Optional[str], content: Optional[bytes],
                               content_type: Optional[str] = None) -> str:
        effective_name = name if name is not None else "attachment"
        dot = effective_name.rfind(".")
        ext = effective_name[dot:] if dot >= 0 else ".bin"
        source = str(uuid.uuid4()) + "-attachment" + ext
        try:
            with open(os.path.join(self._dir, source), "wb") as f:
                f.write(content if content is not None else b"")
        except OSError as e:
            raise OSError("cannot write attachment " + effective_name) from e
        ct = content_type if content_type is not None \
            else multipart.content_type_for(effective_name)
        self._attachment_meta[source] = (effective_name, ct)
        return source

    def clear_attachment_meta(self) -> None:
        """Called once per run, after the last serialization."""
        self._attachment_meta.clear()

    # ------------------------------------------------------------------ environment
    @property
    def results_dir(self) -> str:
        return self._dir

    def write_reporting_info(self, entries: Dict[str, Optional[str]]) -> None:
        """Write :data:`REPORTING_INFO_FILE` from ``entries`` (None values are skipped)."""
        path = os.path.join(self._dir, REPORTING_INFO_FILE)
        with open(path, "w", encoding="utf-8") as f:
            for key, value in entries.items():
                if value is None:
                    continue
                f.write(escape_value(key) + "=" + escape_value(str(value)) + "\n")

    def write_environment(self, environment: Optional[str]) -> None:
        """The file-sink channel of the run environment label (Allure-results convention)."""
        if environment is None or not environment.strip():
            return
        path = os.path.join(self._dir, "environment.properties")
        with open(path, "w", encoding="utf-8") as f:
            f.write("environment=" + environment.strip() + "\n")

    # ------------------------------------------------------------------ results
    def write(self, definition: AutotestDef, result: AutotestResult,
              full_name: Optional[str], allure_id: Optional[str]) -> str:
        """Write ``<uuid>-result.json`` (+ a per-result container when fixtures exist);
        returns the result uuid for later shared containers."""
        def_map = definition.to_payload()
        res_map = result.to_payload()
        result_uuid = str(uuid.uuid4())

        doc: Dict[str, Any] = {
            "uuid": result_uuid,
            "historyId": res_map.get("external_id"),
            "name": res_map.get("name"),
        }
        if full_name is not None:
            doc["fullName"] = full_name
        if "description" in def_map:
            # unlike title, description has a native Allure slot
            doc["description"] = def_map["description"]
        doc["status"] = res_map.get("outcome")
        status_details: Dict[str, Any] = {}
        if "message" in res_map:
            status_details["message"] = res_map["message"]
        if "traces" in res_map:
            status_details["trace"] = res_map["traces"]
        if status_details:
            doc["statusDetails"] = status_details
        doc["start"] = res_map.get("started_on")
        doc["stop"] = res_map.get("completed_on")
        doc["labels"] = self._labels(def_map, res_map, allure_id)
        doc["parameters"] = res_map.get("parameters", [])
        doc["attachments"] = self._attachments(res_map.get("attachments"))
        doc["steps"] = self._steps(res_map.get("step_results"))
        # links come from the DEFINITION, not the result
        doc["links"] = def_map.get("links", [])

        self._write_json(result_uuid + "-result.json", doc)

        befores = self._steps(res_map.get("setup_results"))
        afters = self._steps(res_map.get("teardown_results"))
        if befores or afters:
            container_uuid = str(uuid.uuid4())
            self._write_json(container_uuid + "-container.json", {
                "uuid": container_uuid,
                "children": [result_uuid],
                "befores": befores,
                "afters": afters,
            })
        return result_uuid

    def write_container(self, children_uuids: Optional[Iterable[str]],
                        befores: Optional[Iterable[Any]] = None,
                        afters: Optional[Iterable[Any]] = None) -> None:
        """Shared fixture container for class/module-scoped fixtures covering several
        results; the parser attaches befores/afters to every child result. Fixture nodes
        are :class:`~doqa.client.models.StepResult` instances (or their payload dicts)."""
        children = list(children_uuids) if children_uuids else []
        before_payloads = _payloads(befores)
        after_payloads = _payloads(afters)
        if not children or (not before_payloads and not after_payloads):
            return
        container_uuid = str(uuid.uuid4())
        self._write_json(container_uuid + "-container.json", {
            "uuid": container_uuid,
            "children": children,
            "befores": self._steps(before_payloads),
            "afters": self._steps(after_payloads),
        })

    # ------------------------------------------------------------------ projections
    def _labels(self, def_map: Dict[str, Any], res_map: Dict[str, Any],
                allure_id: Optional[str]) -> List[Dict[str, str]]:
        labels: List[Dict[str, str]] = []

        def add(name: str, value: Any) -> None:
            if value is None:
                return
            text = str(value)
            if text:
                labels.append({"name": name, "value": text})

        add(LABEL_TITLE, def_map.get("title"))
        add(LABEL_ID, def_map.get("external_id"))
        case_ids = def_map.get("case_ids")
        if case_ids:
            csv = ",".join(str(c) for c in case_ids)
            add(LABEL_CASES, csv)
            # both parser generations understand the case binding this way; the label a
            # given parser does not know lands harmlessly in properties
            add(LABEL_WORK_ITEMS, csv)
        add(LABEL_ALLURE_ID, allure_id)
        if res_map.get("create_manual_case"):
            add(LABEL_CREATE_MANUAL_CASE, "true")
        for prop in run_properties(self._framework_label, def_map.get("namespace"),
                                   def_map.get("classname")):
            add(prop.name, prop.value)
        for tag in def_map.get("tags", []):
            add("tag", tag)
        for label in def_map.get("labels", []):
            add("tag", label)
        return labels

    def _steps(self, nodes: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(nodes, list):
            return out
        for node in nodes:
            if not isinstance(node, dict):
                continue
            step: Dict[str, Any] = {
                "name": node.get("title"),
                "status": node.get("outcome"),
            }
            if "message" in node:
                step["statusDetails"] = {"message": node["message"]}
            if "started_on" in node:
                step["start"] = node["started_on"]
            if "completed_on" in node:
                step["stop"] = node["completed_on"]
            step["attachments"] = self._attachments(node.get("attachments"))
            step["steps"] = self._steps(node.get("steps"))
            out.append(step)
        return out

    def _attachments(self, refs: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(refs, list):
            return out
        for ref in refs:
            if not isinstance(ref, dict) or "media_file_id" not in ref:
                continue
            source = str(ref["media_file_id"])
            name, content_type = self._attachment_meta.get(source, (None, None))
            out.append({
                "name": name if name is not None else source,
                "source": source,
                "type": content_type if content_type is not None
                else multipart.DEFAULT_CONTENT_TYPE,
            })
        return out

    def _write_json(self, filename: str, doc: Dict[str, Any]) -> None:
        with open(os.path.join(self._dir, filename), "w", encoding="utf-8") as f:
            f.write(jsonio.write(doc))


def _payloads(nodes: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in nodes or []:
        out.append(node.to_payload() if hasattr(node, "to_payload") else node)
    return out
