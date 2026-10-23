import numpy as np
from PIL import Image
from app.postprocess import preprocess, postprocess, nms, Letterbox

def fake_output(rows, n=8400, c=80):
    raw = np.zeros((1, 4 + c, n), dtype=np.float32)
    for k, (cx, cy, w, h, cls, s) in enumerate(rows):
        raw[0, 0:4, k] = (cx, cy, w, h); raw[0, 4 + cls, k] = s
    return raw

def test_letterbox_geometry():
    img = Image.new("RGB", (1280, 720), (255, 0, 0))
    t, lb = preprocess(img)
    assert t.shape == (1, 3, 640, 640) and t.dtype == np.float32
    assert (lb.scale, lb.pad_x, lb.pad_y) == (0.5, 0, 140)
    assert abs(t[0, 0, 320, 320] - 1.0) < 1e-6          # image area (red channel)
    assert abs(t[0, 0, 10, 10] - 114 / 255) < 1e-6      # padding area

def test_mapping_back_to_original():
    lb = Letterbox(0.5, 0, 140, 1280, 720)
    raw = fake_output([(320, 320, 100, 100, 16, 0.9)])
    (d,) = postprocess(raw, lb, 0.25, 0.45, 100)
    assert d["class_id"] == 16
    b = d["box"]
    assert (round(b["x1"]), round(b["y1"]), round(b["x2"]), round(b["y2"])) == (540, 260, 740, 460)

def test_nms_is_class_aware_and_suppresses_duplicates():
    lb = Letterbox(1.0, 0, 0, 640, 640)
    raw = fake_output([(200, 200, 100, 100, 0, 0.90),
                       (205, 205, 100, 100, 0, 0.80),   # duplicate person -> suppressed
                       (205, 205, 100, 100, 2, 0.70),   # same place, other class -> kept
                       (500, 500, 60, 60, 0, 0.10)])    # below threshold
    dets = postprocess(raw, lb, 0.25, 0.45, 100)
    assert [(d["class_id"], round(d["confidence"], 2)) for d in dets] == [(0, 0.9), (2, 0.7)]

def test_max_det_and_clipping_and_bad_shapes():
    lb = Letterbox(1.0, 0, 0, 640, 640)
    rows = [(50 + 60 * k, 50, 40, 40, 0, 0.9 - k * 0.01) for k in range(10)]
    assert len(postprocess(fake_output(rows), lb, 0.25, 0.45, 3)) == 3
    edge = postprocess(fake_output([(630, 630, 100, 100, 1, 0.9)]), lb, 0.25, 0.45, 5)[0]["box"]
    assert edge["x2"] == 640 and edge["y2"] == 640
    for bad in (np.zeros((1, 84), np.float32), np.zeros((2, 84, 10), np.float32)):
        try: postprocess(bad, lb, 0.25, 0.45, 5); assert False
        except ValueError: pass
    nan = fake_output([(1, 1, 1, 1, 0, 0.5)]); nan[0, 0, 0] = np.nan
    try: postprocess(nan, lb, 0.25, 0.45, 5); assert False
    except ValueError: pass

def test_worst_case_is_bounded():
    import time
    rng = np.random.default_rng(0)
    raw = rng.random((1, 84, 8400), dtype=np.float32)
    raw[0, :4] *= 640
    t = time.perf_counter()
    postprocess(raw, Letterbox(1.0, 0, 0, 640, 640), 0.05, 0.45, 100)
    assert time.perf_counter() - t < 2.0
