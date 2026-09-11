import pytest

from lib import pipeline

from conftest import seed_product


def options(**kw):
    return pipeline.PrepareOptions.from_dict({"discount_vnd": 45000, **kw})


def test_simple_prepare_builds_valid_draft(fake_ai, store):
    url = seed_product("dji-pocket-4-creator-combo", "DJI Pocket 4 Creator Combo", 14740000)
    draft = pipeline.prepare([url], options(categories=[{"id": 11, "name": "Gimbal camera"}]))
    assert draft["kind"] == "simple"
    assert draft["sku"] == "fcp-dji-pocket-4-creator-combo"
    assert draft["regular_price"] == 14740000 and draft["sale_price"] == 14695000
    assert draft["box_items"] == ["Túi đựng", "Cáp USB-C"]
    assert len(draft["images"]) == 3
    assert draft["existing"] is None

    # Second prepare reuses every cached step.
    pipeline.prepare([url], options())
    assert fake_ai.rewrite_calls == 1 and fake_ai.box_calls == 1


def test_simple_publish_create_skip_update(fake_ai, store):
    url = seed_product("osmo-360", "DJI Osmo 360", 9990000)
    draft = pipeline.prepare([url], options(categories=[{"id": 11, "name": "Gimbal camera"}]))
    draft["title"] = "Tên đã sửa"

    result = pipeline.publish(draft)
    assert result["action"] == "created"
    product = store.products[result["product_id"]]
    assert product["name"] == "Tên đã sửa"
    assert [c["id"] for c in product["categories"]] == [10, 11], "parent category added first"
    assert product["brands"] == [{"id": 7}]
    assert product["sale_price"] == "9945000"
    assert "Trong hộp có gì" in product["description"] and "product-specs" in product["description"]
    assert "1545 mAh<br>240 phút" in product["description"]
    assert store.uploads == 3

    again = pipeline.publish(draft)
    assert again["action"] == "skipped"
    assert store.uploads == 3

    draft["on_exists"] = "update"
    draft["sale_price"] = None
    updated = pipeline.publish(draft)
    assert updated["action"] == "updated"
    assert store.products[result["product_id"]]["sale_price"] == ""
    assert store.uploads == 3, "images are deduplicated by content hash"


def test_variable_publish_and_extend_with_new_combo(fake_ai, store):
    a = seed_product("dji-pocket-4-creator-combo", "DJI Pocket 4 Creator Combo", 14740000)
    b = seed_product("dji-pocket-4-standard-combo", "DJI Pocket 4 Standard Combo", 11635000)
    draft = pipeline.prepare([a, b], options())
    assert draft["kind"] == "variable"
    assert draft["key"] == "_variable-dji-pocket-4"
    assert [v["label"] for v in draft["variants"]] == ["Creator Combo", "Standard Combo"]
    assert [v["sku"] for v in draft["variants"]] == ["fcp-dji-pocket-4-creator-combo", "fcp-dji-pocket-4-standard-combo"]

    result = pipeline.publish(draft)
    parent = store.products[result["product_id"]]
    assert parent["type"] == "variable"
    assert parent["attributes"][0]["options"] == ["Creator Combo", "Standard Combo"]
    assert len(store.variations[parent["id"]]) == 2
    # gallery (3) + standard combo hero (1); creator hero is already in the gallery
    assert store.uploads == 4

    # Later: a third combo of the same device is imported.
    c = seed_product("dji-pocket-4-vlog-combo", "DJI Pocket 4 Vlog Combo", 16000000)
    draft2 = pipeline.prepare([a, b, c], options())
    assert draft2["existing"]["id"] == parent["id"]
    uploads_before = store.uploads
    result2 = pipeline.publish(draft2)
    assert result2["action"] == "extended"
    assert store.products[parent["id"]]["attributes"][0]["options"] == ["Creator Combo", "Standard Combo", "Vlog Combo"]
    assert len(store.variations[parent["id"]]) == 3
    assert [v["action"] for v in result2["variations"]] == ["skipped", "skipped", "created"]
    assert store.uploads == uploads_before + 1, "only the new combo's image is uploaded"


def test_publish_refuses_trashed_or_wrong_type(fake_ai, store):
    url = seed_product("mini-5", "DJI Mini 5", 20000000)
    draft = pipeline.prepare([url], options())
    created = pipeline.publish(draft)
    store.products[created["product_id"]]["status"] = "trash"
    with pytest.raises(pipeline.WooCommerceAPIError, match="thùng rác"):
        pipeline.publish(draft)
    store.products[created["product_id"]].update(status="publish", type="variable")
    with pytest.raises(pipeline.WooCommerceAPIError, match="loại"):
        pipeline.publish(draft)


def test_normalize_draft_validation(fake_ai, store):
    a = seed_product("x-creator-combo", "DJI X Creator Combo", 1000000)
    b = seed_product("x-standard-combo", "DJI X Standard Combo", 900000)
    draft = pipeline.prepare([a, b], options())

    bad = dict(draft, title="  ")
    with pytest.raises(pipeline.DraftError, match="Tên"):
        pipeline.normalize_draft(bad)

    bad = dict(draft, variants=[dict(v, label="Combo") for v in draft["variants"]])
    with pytest.raises(pipeline.DraftError, match="trùng"):
        pipeline.normalize_draft(bad)

    bad = dict(draft, variants=[dict(draft["variants"][0], sale_price=2000000), draft["variants"][1]])
    with pytest.raises(pipeline.DraftError, match="khuyến mãi"):
        pipeline.normalize_draft(bad)

    bad = dict(draft, images=["../../etc/passwd"])
    with pytest.raises(pipeline.DraftError):
        pipeline.normalize_draft(bad)

    ok = pipeline.normalize_draft(dict(draft, description='<p>ok</p><script>x</script>'))
    assert "script" not in ok["description"]


def test_legacy_cache_formats_are_read(fake_ai, store):
    url = seed_product("legacy", "DJI Legacy", 5000000)
    d = pipeline.product_dir("legacy")
    (d / "box_description.json").write_text('{"html": "<p><strong>Trong hộp có gì:</strong></p><ul><li>Túi</li><li>Dây</li></ul>"}', encoding="utf-8")
    (d / "state.json").write_text('{"wc_product_id": 555, "wc_product_link": "x"}', encoding="utf-8")
    draft = pipeline.prepare([url], options())
    assert draft["box_items"] == ["Túi", "Dây"]
    assert fake_ai.box_calls == 0


def test_variation_sku_clash_is_caught_before_any_write(fake_ai, store):
    # A simple product imported earlier owns the SKU the Creator variation would get.
    a = seed_product("dji-pocket-4-creator-combo", "DJI Pocket 4 Creator Combo", 14740000)
    b = seed_product("dji-pocket-4-standard-combo", "DJI Pocket 4 Standard Combo", 11635000)
    pipeline.publish(pipeline.prepare([a], options()))
    products_before = dict(store.products)

    draft = pipeline.prepare([a, b], options(status="publish"))
    with pytest.raises(pipeline.WooCommerceAPIError, match="đang được dùng bởi sản phẩm"):
        pipeline.publish(draft)
    assert store.products == products_before, "no half-built parent left on the store"


def test_new_variable_parent_goes_live_only_after_variations(fake_ai, store, monkeypatch):
    a = seed_product("x-creator-combo", "DJI X Creator Combo", 1000000)
    b = seed_product("x-standard-combo", "DJI X Standard Combo", 900000)
    draft = pipeline.prepare([a, b], options(status="publish"))
    statuses = []
    original = store.create_variation

    def spy(pid, payload):
        statuses.append(store.products[pid]["status"])
        return original(pid, payload)

    monkeypatch.setattr(store, "create_variation", spy)
    result = pipeline.publish(draft)
    assert statuses == ["draft", "draft"]
    assert store.products[result["product_id"]]["status"] == "publish"
    state = pipeline.load_json(pipeline.settings.CACHE_DIR / draft["key"] / "state.json")
    assert len(state["variations"]) == 2


def test_skipped_result_reports_prices_on_the_store(fake_ai, store):
    url = seed_product("osmo-mobile", "DJI Osmo Mobile", 3000000)
    draft = pipeline.prepare([url], options(discount_vnd=0))
    created = pipeline.publish(draft)
    store.products[created["product_id"]]["regular_price"] = "2000000"
    draft["regular_price"] = 9999999
    result = pipeline.publish(draft)
    assert result["action"] == "skipped"
    assert result["price_min"] == 2000000


def test_cancel_during_ai_step_stops_before_review(fake_ai, store):
    url = seed_product("cancel-me", "DJI Cancel", 1000000)

    class CancelDuringRewrite(pipeline.Reporter):
        cancelled = False

        def log(self, stage, msg, level="info"):
            if "đang viết lại" in msg:
                self.cancelled = True

        def check_cancelled(self):
            if self.cancelled:
                raise pipeline.Cancelled()

    with pytest.raises(pipeline.Cancelled):
        pipeline.prepare([url], options(), CancelDuringRewrite())


def test_draft_key_and_image_paths_are_validated(fake_ai, store):
    url = seed_product("safe", "DJI Safe", 1000000)
    draft = pipeline.prepare([url], options())
    for bad_key in ["/etc", "../x", "Safe Key"]:
        with pytest.raises(pipeline.DraftError):
            pipeline.normalize_draft(dict(draft, key=bad_key))
    for ref in ["../safe/x.png", "./images/x.png", "safe/../raw.json"]:
        with pytest.raises(pipeline.DraftError):
            pipeline.resolve_image(ref)
