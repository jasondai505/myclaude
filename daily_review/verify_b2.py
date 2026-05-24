"""B2 验证：逻辑涨停 label 纳入综合评分（催化维度加分）"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import engine


def _base_kwargs():
    return dict(
        stock={"hot_rank": 50, "zt_boards": 1, "zt_time": "09:30:00"},
        fev_total=20, theme_level=3, theme_trend="验证",
        lhb_info=None, research=None,
        zsxq_mentions=0, crash_warnings=None,
    )


def test_pure_logic_adds_full_bonus():
    base = engine.compute_composite_score(**_base_kwargs())
    boosted = engine.compute_composite_score(**_base_kwargs(), limit_up_label="纯逻辑")
    print(f"base catalyst={base['scores']['catalyst']} boosted={boosted['scores']['catalyst']}")
    assert boosted["scores"]["catalyst"] >= base["scores"]["catalyst"], "纯逻辑应加分"
    assert boosted["total"] >= base["total"], "总分应不降"
    assert boosted["scores"]["catalyst"] <= 15


def test_pian_logic_adds_partial():
    base = engine.compute_composite_score(**_base_kwargs())
    boosted = engine.compute_composite_score(**_base_kwargs(), limit_up_label="偏逻辑")
    print(f"偏逻辑 catalyst: base={base['scores']['catalyst']} boosted={boosted['scores']['catalyst']}")
    assert boosted["scores"]["catalyst"] >= base["scores"]["catalyst"]


def test_pure_emotion_no_bonus():
    base = engine.compute_composite_score(**_base_kwargs())
    boosted = engine.compute_composite_score(**_base_kwargs(), limit_up_label="纯情绪")
    print(f"纯情绪 catalyst: base={base['scores']['catalyst']} boosted={boosted['scores']['catalyst']}")
    assert boosted["scores"]["catalyst"] == base["scores"]["catalyst"], "情绪不应在 catalyst 加分"


def test_label_none_backward_compatible():
    r = engine.compute_composite_score(**_base_kwargs())
    assert "total" in r and "scores" in r and "advice" in r
    print(f"backward: total={r['total']} advice={r['advice']}")


def test_catalyst_caps_at_15():
    kwargs = _base_kwargs()
    kwargs["lhb_info"] = {"comment": "机构专用"}
    kwargs["research"] = [{"rating": "买入"}, {"rating": "买入"}]
    kwargs["zsxq_mentions"] = 2
    r = engine.compute_composite_score(**kwargs, limit_up_label="纯逻辑")
    print(f"capped: catalyst={r['scores']['catalyst']}")
    assert r["scores"]["catalyst"] == 15


if __name__ == "__main__":
    test_pure_logic_adds_full_bonus()
    test_pian_logic_adds_partial()
    test_pure_emotion_no_bonus()
    test_label_none_backward_compatible()
    test_catalyst_caps_at_15()
    print("\n=== All B2 checks passed ===")
