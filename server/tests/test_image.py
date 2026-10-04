"""`GET /api/problem/<pid>/image/<kind>`：字节出去，失败仍然是 JSON（契约 §7）。"""

from __future__ import annotations

from conftest import PNG_1X1, get_json, make_card

PID = "p-20200101-aaaaaa"


def api_with_images(api_for, card=None, **images):
    blob = {f"{PID}-problem.png": PNG_1X1, f"{PID}-clean.png": PNG_1X1}
    blob.update(images)
    return api_for([card or make_card(PID)], images=blob)


def test_original_image_comes_back_as_bytes(api_for):
    r = api_with_images(api_for).handle("GET", f"/api/problem/{PID}/image/original")

    assert r.status == 200
    assert r.body == PNG_1X1
    assert r.content_type == "image/png"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Ai-Note-Image-Kind"] == "original"
    assert r.headers["Content-Length"] == str(len(PNG_1X1))


def test_clean_image_is_the_one_the_redo_page_must_use(api_for):
    r = api_with_images(api_for).handle("GET", f"/api/problem/{PID}/image/clean")

    assert r.status == 200
    assert r.body == PNG_1X1
    assert r.headers["X-Ai-Note-Image-Kind"] == "clean"


def test_mask_is_served_only_when_the_file_exists(api_for):
    api = api_with_images(api_for, **{f"{PID}-cleanmask.png": PNG_1X1})
    r = api.handle("GET", f"/api/problem/{PID}/image/mask")
    assert r.status == 200 and r.body == PNG_1X1


def test_missing_clean_image_is_a_404_that_lists_what_does_exist(api_for):
    """卡里记了擦除图、文件不在：一个空白的 <img> 没有信息，这个 404 有。"""
    api = api_for([make_card(PID)], images={f"{PID}-problem.png": PNG_1X1})
    status, body = get_json(api, f"/api/problem/{PID}/image/clean")

    assert status == 404
    assert body["ok"] is False
    error = body["error"]
    assert error["code"] == "not_found"
    assert error["details"]["available_kinds"] == ["original"]
    assert error["details"]["kind"] == "clean"
    assert PID in error["message"]
    assert "clean" in error["message"]


def test_available_kinds_reflects_files_not_declarations(api_for):
    """卡里声明了擦除图、文件却不在 → 它**不在** available_kinds 里。"""
    api = api_for([make_card(PID)], images={f"{PID}-problem.png": PNG_1X1})
    _, body = get_json(api, f"/api/problem/{PID}/image/mask")
    assert body["error"]["details"]["available_kinds"] == ["original"]


def test_bad_kind_is_a_400_that_lists_the_enum(api_for):
    status, body = get_json(api_with_images(api_for), f"/api/problem/{PID}/image/thumb")

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["details"]["param"] == "kind"
    assert body["error"]["details"]["allowed"] == ["original", "clean", "mask"]


def test_path_traversal_in_the_id_is_a_400_not_a_404(api_for):
    """服务将来要经 Tailscale 暴露（#13）：`..` 必须被当成输入错误拒掉。"""
    api = api_with_images(api_for)
    for target in (
        "/api/problem/../image/original",
        "/api/problem/%2e%2e%2f%2e%2e%2fetc%2fpasswd/image/original",
        "/api/problem/a..b/image/original",
        "/api/problem//image/original",
    ):
        status, body = get_json(api, target)
        assert status == 400, (target, status, body)
        assert body["error"]["code"] == "bad_request"


def test_image_for_an_unknown_problem_is_a_404(api_for):
    status, body = get_json(api_with_images(api_for), "/api/problem/p-nope-999999/image/clean")

    assert status == 404
    assert body["error"]["details"]["what"] == "problem"
