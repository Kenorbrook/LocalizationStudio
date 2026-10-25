# Engine-side verification, invoked explicitly without a game window.
init 999 python:
    def _studio_verify_command():
        import io
        import json
        ap = renpy.arguments.ArgumentParser(description="Verify Studio language layer")
        ap.add_argument("--expected", required=True)
        ap.add_argument("--result", required=True)
        args = ap.parse_args()
        with io.open(args.expected, "r", encoding="utf-8") as stream:
            expected = json.load(stream)
        renpy.load_language("studio_russian")
        translator = renpy.game.script.translator
        checked = 0
        for record in expected["records"]:
            if record["type"] == "string":
                actual = renpy.translate_string(record["source"], language="studio_russian")
                if actual != record["text"]:
                    raise Exception("Studio string translation mismatch: " + record["source"])
            else:
                node = translator.language_translates.get((record["identifier"], "studio_russian"))
                nodes = [node] + list(getattr(node, "block", []))
                if not any(getattr(item, "what", None) == record["text"] for item in nodes):
                    raise Exception("Studio dialogue translation mismatch: " + record["identifier"])
                if record["identifier"] not in translator.default_translates:
                    raise Exception("Studio original dialogue missing")
            checked += 1
        renpy.translation.init_translation()
        original_font = style.default.font
        renpy.change_language("studio_russian", force=True)
        if _preferences.language != "studio_russian":
            raise Exception("Studio Russian language activation failed")
        if style.default.font != "studio_localization/DejaVuSans.ttf":
            raise Exception("Studio Cyrillic style activation failed")
        if not renpy.loadable("studio_localization/DejaVuSans.ttf"):
            raise Exception("Studio Cyrillic font is not loadable")
        renpy.change_language(None, force=True)
        if _preferences.language is not None:
            raise Exception("Studio original language activation failed")
        if style.default.font != original_font:
            raise Exception("Studio original font restoration failed")
        if expected["mode"] == "menu" and not renpy.has_screen("_studio_language_selector"):
            raise Exception("Studio language control is missing")
        with io.open(args.result, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"checked": checked, "languages": ["studio_russian", None], "font": True, "mode": expected["mode"]}, ensure_ascii=True))
        return False
    renpy.arguments.register_command("studio_verify", _studio_verify_command)
