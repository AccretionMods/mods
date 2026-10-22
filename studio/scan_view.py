"""Scan capabilities, filters, grouping and evidence; does not modify game configuration."""

import re

_CACHE = {}


def index(dev, name):
    paths = [
        dev.dump_path(name),
        dev.mod_dir(name) / "mod.json",
        dev.mod_dir(name) / "tweaks.json",
        dev.mod_dir(name) / "hooks.json",
        dev.patterns_path(),
    ]
    stamp = tuple(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else (str(p),)
        for p in paths
    )
    cached = _CACHE.get(name)
    if not cached or cached[0] != stamp:
        result, rows = dev.scan_rows(name)
        rows = annotate(rows)
        classes = dev.dump_scan.parse(dev.dump_path(name))
        rows = add_alternatives(rows, classes, dev.dump_methods(name))
        meta = dev.read_json(dev.mod_dir(name) / "mod.json")
        rows = attach_verification(
            rows,
            dev.load_tweaks(dev.mod_dir(name), meta),
            meta.get("tested_app_version"),
        )
        cached = _CACHE[name] = (stamp, result, rows, classes)
        if len(_CACHE) > 8:
            del _CACHE[next(iter(_CACHE))]
    return cached[1], cached[2]


def review_methods(dev, name, methods, convert):
    index(dev, name)
    rows = [
        {
            "class": m.full_name.rsplit(".", 1)[0],
            "member": m.name,
            "method": convert(m),
            "inspect_only": True,
            "name": m.name,
        }
        for m in methods
    ]
    reviewed = add_alternatives(annotate(rows), _CACHE[name][3], dev.dump_methods(name))
    return [
        dict(
            r["method"],
            nojit_alternatives=r["nojit_alternatives"],
            nojit_call=r.get("nojit_call"),
            ambiguous=r.get("ambiguous", False),
            explanation=r["explanation"],
        )
        for r in reviewed
    ]


def query(dev, name, options):
    result, rows = index(dev, name)
    area, find = options.get("area"), options.get("find")
    if area:
        rows = [r for r in rows if r["area"].lower().startswith(area.lower())]
    if find:
        try:
            rx = re.compile(find, re.IGNORECASE)
        except re.error as error:
            raise dev.DevError(f"Search pattern: {error}") from error
        rows = [r for r in rows if rx.search(f"{r['class']}.{r['member']}")]
    if options.get("flagged") == "1":
        rows = [r for r in rows if r.get("flag")]
    rows = filter_rows(
        rows,
        options.get("mode", "all"),
        options.get("verified", "all"),
        options.get("hide_has") == "1",
    )
    return result, group_rows(rows)


def group_rows(rows):
    groups = {}
    for r in rows:
        name = re.sub(r"^(get_|set_|m_)", "", r["member"])
        name = re.sub(r"^<(.+)>k__BackingField$", r"\1", name).lstrip("_").lower()
        key = (r.get("assembly", "Assembly-CSharp"), r["class"], name)
        groups.setdefault(key, []).append(r)
    out = []
    for variants in groups.values():
        primary = min(
            variants,
            key=lambda r: {"pin": 0, "hook": 1, "investigate": 2}.get(
                r["capability"], 3
            ),
        )
        out.append(
            dict(primary, variants=variants, has=all(r.get("has") for r in variants))
        )
    return out


def page(rows, offset=0, limit=300):
    offset, limit = max(0, int(offset)), min(300, max(1, int(limit)))
    selected = rows[offset : offset + limit]
    next_offset = offset + len(selected)
    return {
        "rows": selected,
        "matched": len(rows),
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset < len(rows),
    }


def annotate(rows):
    out = []
    for row in rows:
        r = dict(row)
        method = r.get("method")
        r["capability"] = (
            "hook" if method else "investigate" if r.get("inspect_only") else "pin"
        )
        r["nojit"] = r["capability"] == "pin" or bool(
            method and not method.get("needs_jit", True)
        )
        r["explanation"] = (
            "A reachable scalar value: the ticker writes data without patching code. Gameplay effect is unverified."
            if r["capability"] == "pin"
            else "Concrete method: direct-call interception needs JIT; virtual/callback dispatch may permit pointer hooks."
            if method
            else "Mutable scalar field; an object path must be identified first."
        )
        r["confidence"] = "structural candidate; not phone verification"
        r.setdefault("verification", {"state": "untested", "results": []})
        out.append(r)
    return out


def verification_state(result, version):
    if not result or result.get("result") not in {"works", "fails"}:
        return "untested"
    if not version or result.get("app_version") != version:
        return "stale"
    return result["result"]


def attach_verification(rows, tweaks, version):
    out = []
    for row in rows:
        r = dict(row)
        target = f"{r['class']}.{r['member']}"
        results = []
        for tweak in tweaks.get("tweaks", []):
            matched = any(h.get("method") == target for h in tweak.get("hooks", []))
            for pin in tweak.get("pins", []):
                member = pin.get("field") or pin.get("setter") or pin.get("call")
                obj = pin.get("object", "")
                owner_matches = (
                    pin.get("class") == r["class"]
                    or obj == r.get("object")
                    or obj in (r["class"], r["class"] + "[]")
                )
                matched |= member == r["member"] and owner_matches
            if matched and tweak.get("verified"):
                results.append(dict(tweak["verified"], key=tweak["key"]))
        states = [verification_state(v, version) for v in results]
        state = next(
            (s for s in ("fails", "works", "stale") if s in states), "untested"
        )
        r["verification"] = {"state": state, "app_version": version, "results": results}
        if r.get("nojit_alternatives"):
            r["nojit_alternatives"] = attach_verification(
                r["nojit_alternatives"], tweaks, version
            )
        out.append(r)
    return out


def component_classes(classes):
    components = set()
    for _ in range(len(classes) + 1):
        previous = len(components)
        for cls, info in classes.items():
            base = info.get("base", "")
            local = cls.rpartition(".")[0]
            if (
                base == "MonoBehaviour"
                or base in components
                or (local and f"{local}.{base}" in components)
            ):
                components.add(cls)
        if len(components) == previous:
            break
    return components


def object_paths(classes):
    paths = {}
    components = component_classes(classes)

    def resolve(owner, target):
        local = owner.rpartition(".")[0]
        candidates = (
            [target]
            if "." in target
            else [f"{local}.{target}" if local else target, target]
        )
        return next((c for c in candidates if c in classes), None)

    for cls, info in classes.items():
        for field in info.get("statics", []):
            if "<" not in field:
                paths.setdefault(cls, {"object": f"{cls}.{field}"})
        if cls in components:
            paths.setdefault(cls, {"object": cls + "[]"})
        for field, target in info.get("references", {}).items():
            target = resolve(cls, target)
            if target and "<" not in field:
                paths.setdefault(target, {"object": f"{cls}.{field}"})
    # Only one hop: this is the path the generator supports, not an inferred arbitrary object graph.
    for cls, path in list(paths.items()):
        for getter, target in classes[cls].get("object_getters", {}).items():
            target = resolve(cls, target)
            if target:
                paths.setdefault(
                    target,
                    {**path, "via": getter, "via_owner": cls, "target_class": target},
                )
    return paths


def add_alternatives(rows, classes, methods):
    from collections import Counter

    from dump_scan import NUMERIC, _suggest
    from il2cpp_dump import UNITY_MESSAGES

    paths = object_paths(classes)
    components = component_classes(classes)
    counts = Counter((m.assembly, m.full_name, m.args) for m in methods)
    lookup = {(m.assembly, m.full_name, m.args): m for m in methods}
    out = []
    for row in rows:
        r = dict(row, nojit_alternatives=[])
        method = r.get("method")
        if method:
            cls, name = r["class"], r["member"]
            signature_key = (method["assembly"], method["full_name"], method["args"])
            r["ambiguous"] = counts[signature_key] > 1
            if r["ambiguous"]:
                r["explanation"] = (
                    "Same-name/same-arity overloads: the runtime cannot select this signature safely. Inspect before adding."
                )
                r["capability"] = "investigate"
                r["nojit"] = False
            # Merely naming a method Update does not establish Unity dispatch on an ordinary class.
            if (
                name in UNITY_MESSAGES
                and method.get("slot") is None
                and not unity_callback(method, cls in components)
            ):
                r["method"] = dict(method, needs_jit=True)
                r["nojit"] = False
            same_owner = cls in paths and (
                paths[cls]["object"] in (cls, cls + "[]")
                or paths[cls]["object"].startswith(cls + ".")
            )
            if (
                not r["ambiguous"]
                and same_owner
                and not paths[cls].get("via")
                and method["return_type"] == "void"
                and not name.startswith(("get_", "set_"))
                and not re.search(r"\bstatic\b", method.get("signature", ""))
                and all(
                    p.split("=")[0].strip().rsplit(" ", 1)[0] in NUMERIC
                    for p in method.get("params", [])
                )
            ):
                r["nojit_call"] = dict(
                    paths[cls],
                    evidence="Timed original-method call; not interception. Supply arguments and test side effects.",
                )
                r["nojit"] = True
            if (
                not r["ambiguous"]
                and name.startswith("get_")
                and method["args"] == 0
                and method["return_type"] in NUMERIC
                and cls in paths
            ):
                prop, ctype = name[4:], NUMERIC[method["return_type"]]
                setter_key = (method["assembly"], cls + ".set_" + prop, 1)
                setter = lookup.get(setter_key)
                target, kind, evidence = None, "field", ""
                if (
                    setter
                    and counts[setter_key] == 1
                    and setter.return_type == "void"
                    and not re.search(r"\bstatic\b", setter.signature)
                    and setter.params[0].rsplit(" ", 1)[0] == method["return_type"]
                ):
                    target, kind, evidence = (
                        "set_" + prop,
                        "setter",
                        "Exact same-class property and scalar type",
                    )
                if not target:
                    fields = classes[cls].get("fields", {})
                    for candidate in (
                        f"<{prop}>k__BackingField",
                        prop,
                        "_" + prop[0].lower() + prop[1:],
                        "m_" + prop,
                    ):
                        f = fields.get(candidate)
                        if f and f["ctype"] == ctype and not f.get("readonly"):
                            target, evidence = (
                                candidate,
                                "Same-class scalar field; gameplay equivalence requires testing",
                            )
                            break
                path = paths[cls]
                via_ok = (
                    not path.get("via")
                    or counts[
                        (method["assembly"], path["via_owner"] + "." + path["via"], 0)
                    ]
                    == 1
                )
                if (
                    target
                    and via_ok
                    and not re.search(r"\bstatic\b", method.get("signature", ""))
                ):
                    alt = dict(
                        paths[cls],
                        **{
                            "class": cls,
                            "target_class": cls,
                            "member": target,
                            "name": prop,
                            "ctype": ctype,
                            "kind": kind,
                            "evidence": evidence,
                            "has": False,
                            "inspect_only": False,
                        },
                    )
                    alt.update(_suggest(prop, ctype, kind == "setter"))
                    r["nojit_alternatives"].append(alt)
                    r["nojit"] = True
        out.append(r)
    return out


def unity_callback(method, is_component):
    if not is_component or re.search(r"\bstatic\b", method.get("signature", "")):
        return False
    name = method["method"]
    params = [
        p.split("=")[0].strip().rsplit(" ", 1)[0].rsplit(".", 1)[-1]
        for p in method.get("params", [])
    ]
    result = method["return_type"].rsplit(".", 1)[-1]
    if result != "void" and not (name == "Start" and result == "IEnumerator"):
        return False
    if name in {"OnApplicationPause", "OnApplicationFocus"}:
        return params == ["bool"]
    if name.startswith("OnTrigger"):
        return params == ["Collider2D" if name.endswith("2D") else "Collider"]
    if name.startswith("OnCollision"):
        return params == ["Collision2D" if name.endswith("2D") else "Collision"]
    return not params


def filter_rows(rows, mode="all", verified="all", hide_has=False):
    return [
        r
        for r in rows
        if (
            mode == "all"
            or (mode == "no-jit" and r["nojit"])
            or r["capability"] == mode
        )
        and (verified == "all" or r["verification"]["state"] == verified)
        and (not hide_has or not r.get("has"))
    ]
